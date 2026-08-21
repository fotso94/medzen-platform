#!/usr/bin/env python3
"""B5 SageMaker training-job authoring (work item C2): render / validate / launch.

The same discipline as the pilot executor, applied to CreateTrainingJob:

  render    derives the EXACT request from a bindings file — every
            environment-specific value (image digest, subnets, security
            group, data version, cost ceiling) comes from bindings built
            and reviewed at packet time; nothing here is invented;
  validate  re-derives and compares byte-for-byte, then screens the
            request against prohibited scopes and the cost ceiling —
            a drifted request is a refusal, not a warning;
  launch    refuses unless the shared-file review for this job id exists
            (the driver's gate, verbatim in spirit), then submits the
            validated request with the aws CLI and prints the ARN.

Only launch touches AWS. Spot is mandatory: the ceiling arithmetic uses
the ON-DEMAND rate, so the true spend lands at or under ~35% of it.
"""

from __future__ import annotations

import argparse
import json
import hashlib
import re
import subprocess
import sys
from pathlib import Path

ACCOUNT = "558069890522"
REGION = "eu-central-1"
BUCKET = "medzen-speech"
ROLE_ARN = f"arn:aws:iam::{ACCOUNT}:role/medzen-trainer-role"
INSTANCE_ALLOWLIST = {"ml.g6.xlarge"}
# g6 carries local NVMe instance storage: hardware-encrypted at rest by AWS,
# and CreateTrainingJob REFUSES VolumeKmsKeyId for it (live refusal at T5
# launch). S3 output KMS is separate and always required.
NVME_LOCAL_STORAGE_TYPES = {"ml.g6.xlarge"}
ON_DEMAND_USD_PER_HOUR = {"ml.g6.xlarge": 1.60}  # DELIBERATELY above any
# published eu-central-1 SageMaker rate (~$1.2-1.3/h at last check; EC2
# g6.xlarge is $0.805 in the B4 design table). This constant only converts
# max_runtime into worst-case dollars for the ceiling refusal, so erring
# high can only refuse too eagerly, never authorize too much.
SHARED_REVIEWS = Path.home() / "Documents/medzen-shared/claude_instructions.txt"
SHA_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
PROHIBITED_SUBSTRINGS = (
    "iam:", "approved/asr", "/medzen/registry/production",
    "model-registration", "mlflow", "eval/",
)
REQUIRED_ENVIRONMENT = (
    "MEDZEN_VARIANT", "MEDZEN_MANIFEST_VERSION", "MEDZEN_LANGUAGES",
    "MEDZEN_SEED", "MEDZEN_MAX_STEPS",
    # Codex review #4: a packet that omitted the mode silently trained
    # LoRA — every packet now DECLARES what kind of training it buys
    "MEDZEN_TRAIN_MODE",
)


class JobRefusal(RuntimeError):
    pass


def _require(bindings: dict, key: str):
    value = bindings.get(key)
    if value in (None, "", [], {}):
        raise JobRefusal(f"bindings key {key!r} is required and absent")
    return value


def render_request(bindings: dict) -> dict:
    job_id = _require(bindings, "job_id")
    if re.fullmatch(r"[a-z0-9-]{1,40}", job_id) is None:
        raise JobRefusal("job_id must be lowercase kebab, <=40 chars")
    image = _require(bindings, "image_uri_with_digest")
    if "@sha256:" not in image or not image.startswith(
            f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/"):
        raise JobRefusal(
            "image must be this account's ECR URI pinned by digest — "
            "a floating tag can train on unreviewed code")
    instance_type = _require(bindings, "instance_type")
    if instance_type not in INSTANCE_ALLOWLIST:
        raise JobRefusal(f"instance {instance_type!r} is outside the allowlist "
                         f"{sorted(INSTANCE_ALLOWLIST)}")
    kms = _require(bindings, "kms_key_arn")
    if not kms.startswith(f"arn:aws:kms:{REGION}:{ACCOUNT}:key/"):
        raise JobRefusal("kms_key_arn is not this account's key in-region")
    subnets = _require(bindings, "subnets")
    security_groups = _require(bindings, "security_group_ids")
    max_runtime_s = int(_require(bindings, "max_runtime_seconds"))
    # Spot is the default and the campaign posture. Opting out requires BOTH
    # an explicit false AND a written reason in bindings — added when the
    # account's spot-training quota was 0 at T5 launch (increase filed);
    # the ceiling arithmetic below is on-demand-based either way, so an
    # on-demand run can never cost more than the ceiling contemplated.
    managed_spot = bindings.get("managed_spot", True)
    if not isinstance(managed_spot, bool):
        raise JobRefusal("managed_spot must be a boolean when present")
    if not managed_spot and not str(bindings.get("managed_spot_reason", "")).strip():
        raise JobRefusal("opting out of spot requires managed_spot_reason")
    if managed_spot:
        max_wait_s = int(_require(bindings, "max_wait_seconds"))
        if max_wait_s < max_runtime_s:
            raise JobRefusal("max_wait must cover max_runtime (spot contract)")
    elif bindings.get("max_wait_seconds") is not None:
        raise JobRefusal("max_wait_seconds is a spot-only setting")
    ceiling_usd = float(_require(bindings, "cost_ceiling_usd"))
    worst_case = max_runtime_s / 3600.0 * ON_DEMAND_USD_PER_HOUR[instance_type]
    if worst_case > ceiling_usd:
        raise JobRefusal(
            f"max_runtime {max_runtime_s}s costs up to ${worst_case:.2f} "
            f"on-demand, above the ${ceiling_usd:.2f} ceiling — shrink the "
            "runtime or raise the ceiling in review, never here")
    environment = dict(_require(bindings, "environment"))
    missing = [k for k in REQUIRED_ENVIRONMENT if not environment.get(k)]
    if missing:
        raise JobRefusal(f"environment lacks {missing}")
    if environment["MEDZEN_VARIANT"] != "ctc":
        raise JobRefusal("only the calibrated ctc variant is launchable")
    # Codex review #4 (reproduced: LR=nan passed): the trainer's OWN parser
    # is the single source of truth for environment semantics — run it at
    # packet time so a bad packet dies here, not after instance spin-up.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from pipeline.omniasr_train import TrainerRefusal, parse_config
    try:
        parse_config(environment)
    except TrainerRefusal as exc:
        raise JobRefusal(
            f"the trainer would refuse this environment at container "
            f"start: {exc}") from exc
    # Codex review #8 step 6: a multilingual-full packet must bind the
    # EXACT approved pilot profile, not merely satisfy generic limits.
    if environment.get("MEDZEN_MULTILINGUAL_FULL_ACK"):
        protocol = load_protocol(
            Path(__file__).resolve().parents[1])
        mandatory = set(protocol["mandatory_languages"])
        requested = {t.strip() for t in
                     environment.get("MEDZEN_LANGUAGES", "").split(",")
                     if t.strip()}
        if requested != mandatory:
            raise JobRefusal(
                f"multilingual-full packets bind the frozen pilot set "
                f"{sorted(mandatory)} exactly; got {sorted(requested)}")
        if environment.get("MEDZEN_MANIFEST_VERSION") != "gb9":
            raise JobRefusal(
                "multilingual-full packets bind dataset version gb9 "
                "(B5-GB9: gb8 minus the cross-language CV contributor "
                "whose voice sits in the kinyarwanda dev-selection "
                "surface — Codex review #19 finding 4)")
        if environment.get("MEDZEN_TEMPERATURE") != "0.5":
            raise JobRefusal(
                "multilingual-full packets bind temperature 0.5 exactly "
                "(the approved pilot profile)")
        expected_ref = ("s3://medzen-speech/curated/_versions/gb3/"
                        "DQ-2026-006-gb3-pulaar-question-mark-deferral.json")
        if environment.get("MEDZEN_EXCLUSIONS_REF") != expected_ref:
            raise JobRefusal(
                "multilingual-full packets bind the exact DQ-2026-006 "
                "exclusions reference gb6 adoption was granted on")
    registry_line = _require(bindings, "cost_registry_line")
    volume_gb = int(bindings.get("volume_gb", 100))
    if not 1 <= volume_gb <= 500:
        raise JobRefusal(f"volume_gb {volume_gb} is outside 1..500")

    prefix = f"research/b5-training/{job_id}"
    return {
        "TrainingJobName": f"medzen-b5-{job_id}",
        "RoleArn": ROLE_ARN,
        "AlgorithmSpecification": {
            "TrainingImage": image,
            "TrainingInputMode": "File",
            # Without an explicit entrypoint SageMaker appends its default
            # 'train' argument to the image ENTRYPOINT — the first T5 attempt
            # died in seconds on python trying to open a file named 'train'.
            "ContainerEntrypoint": ["/opt/venv/bin/python"],
            "ContainerArguments": ["-m", "pipeline.omniasr_train"],
        },
        "OutputDataConfig": {
            "S3OutputPath": f"s3://{BUCKET}/{prefix}/output",
            "KmsKeyId": kms,
        },
        "CheckpointConfig": {
            "S3Uri": f"s3://{BUCKET}/{prefix}/checkpoints",
            "LocalPath": "/opt/ml/checkpoints",
        },
        "ResourceConfig": (
            {"InstanceType": instance_type, "InstanceCount": 1,
             "VolumeSizeInGB": volume_gb}
            if instance_type in NVME_LOCAL_STORAGE_TYPES else
            {"InstanceType": instance_type, "InstanceCount": 1,
             "VolumeSizeInGB": volume_gb, "VolumeKmsKeyId": kms}
        ),
        "VpcConfig": {
            "SecurityGroupIds": list(security_groups),
            "Subnets": list(subnets),
        },
        "StoppingCondition": (
            {"MaxRuntimeInSeconds": max_runtime_s,
             "MaxWaitTimeInSeconds": max_wait_s}
            if managed_spot else
            {"MaxRuntimeInSeconds": max_runtime_s}
        ),
        "EnableManagedSpotTraining": managed_spot,
        "EnableNetworkIsolation": False,
        "Environment": dict(sorted(environment.items())),
        "Tags": [
            {"Key": "medzen:cost-registry", "Value": registry_line},
            {"Key": "medzen:job", "Value": job_id},
            {"Key": "medzen:classification",
             "Value": "OFFLINE_TRAINING_PUBLIC_RESEARCH_NO_PHI"},
        ],
    }


def validate_request(request: dict, bindings: dict) -> dict:
    expected = render_request(bindings)
    if request != expected:
        raise JobRefusal("request differs from the exact rendered form")
    if request["RoleArn"] != ROLE_ARN:
        raise JobRefusal("RoleArn is not the pinned trainer role")
    # RoleArn is pinned to the exact constant above, so it is excluded from
    # the substring screen — any OTHER field smuggling an iam: scope still trips.
    screened = {k: v for k, v in request.items() if k != "RoleArn"}
    flattened = json.dumps(screened, sort_keys=True).casefold()
    for prohibited in PROHIBITED_SUBSTRINGS:
        if prohibited.casefold() in flattened:
            raise JobRefusal(f"request contains prohibited scope: {prohibited}")
    return {
        "status": "PASS_EXACT_TRAINING_REQUEST",
        "job": request["TrainingJobName"],
        "worst_case_on_demand_usd": round(
            request["StoppingCondition"]["MaxRuntimeInSeconds"] / 3600.0
            * ON_DEMAND_USD_PER_HOUR[request["ResourceConfig"]["InstanceType"]], 2),
        "spot": request["EnableManagedSpotTraining"],
    }


def canonical_bindings_sha256(bindings: dict) -> str:
    """The packet identity the authorization must cite (Codex review #9:
    the phrase bound only the job id, so a mutated packet — different
    seed, LR, batch, image — launched under an old approval)."""
    return hashlib.sha256(json.dumps(
        bindings, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


CALIBRATION_TIER_USD = 10.0
AUTH_DIR = "platform/decisions/launch-authorizations"
REVIEWS_DIR = "platform/decisions/reviews"
INTENTS_DIR = "platform/decisions/launch-intents"
ALLOWED_SIGNERS = "platform/decisions/OWNER-ALLOWED-SIGNERS"
SIGN_NAMESPACE = "medzen-launch"
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
# Codex review #22: the calibration proves the RECIPE; an arm may differ
# from it ONLY on these declared scale dimensions — every other
# environment key must be byte-identical to the committed calibration
# packet (LR/batch/accum/seed/schedule drift refuses).
SCALE_KEYS = frozenset({"MEDZEN_MAX_STEPS", "MEDZEN_CHECKPOINT_EVERY",
                        "MEDZEN_WARMUP_STEPS", "MEDZEN_AUDIO_CAP_HOURS"})


def repo_head_oid(root: Path) -> str:
    """ONE captured commit OID for EVERY governed input in a launch
    decision (Codex reviews #21-#22: separate HEAD resolutions and
    working-tree reads could disagree mid-decision)."""
    completed = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                               capture_output=True, text=True)
    oid = (completed.stdout or "").strip()
    if completed.returncode != 0 or not _HEX40.fullmatch(oid):
        raise JobRefusal("cannot resolve a git HEAD commit — committed-"
                         "evidence gates need a repository")
    return oid


def _show_at(root: Path, oid: str, rel: str) -> bytes | None:
    if rel.startswith(("/", "..")) or ":" in rel or "\\" in rel \
            or "/../" in rel:
        return None
    completed = subprocess.run(["git", "-C", str(root), "show",
                                f"{oid}:{rel}"],
                               capture_output=True)
    return completed.stdout if completed.returncode == 0 else None


def load_protocol(root: Path, oid: str | None = None) -> dict:
    """Protocol via the hash-binding pointer. With `oid`, BOTH files come
    from that captured commit (Codex review #22: working-tree bytes and
    an uncontained pointer path were accepted); without it (authoring
    time: render/validate) the working tree is read with the same
    containment and hash rules."""
    pointer_rel = "platform/decisions/CURRENT-PROMOTION-PROTOCOL.json"
    if oid is None:
        pointer = json.loads((root / pointer_rel).read_bytes())
    else:
        body = _show_at(root, oid, pointer_rel)
        if body is None:
            raise JobRefusal("protocol pointer is not committed")
        pointer = json.loads(body)
    rel = str(pointer.get("file") or "")
    if not rel.startswith("platform/decisions/") or rel.startswith(("/",
            "..")) or "/../" in rel or ":" in rel:
        raise JobRefusal(f"protocol pointer path {rel!r} escapes "
                         "platform/decisions/ — refusing")
    if oid is None:
        protocol_body = (root / rel).read_bytes()
    else:
        protocol_body = _show_at(root, oid, rel)
        if protocol_body is None:
            raise JobRefusal(f"protocol file {rel} is not committed")
    if hashlib.sha256(protocol_body).hexdigest() != pointer.get("sha256"):
        raise JobRefusal("protocol file does not match the pointer hash")
    protocol = json.loads(protocol_body)
    if protocol.get("record") != pointer.get("record"):
        raise JobRefusal("protocol record id does not match the pointer")
    return protocol


def assert_medzen_account(runner=subprocess.run) -> None:
    """CLI-based account assertion (tooling/tests). The launch path uses
    ONE boto3 session for the STS check AND the mutation."""
    completed = runner(["aws", "sts", "get-caller-identity",
                        "--query", "Account", "--output", "text"],
                       capture_output=True, text=True)
    account = (completed.stdout or "").strip()
    if completed.returncode != 0 or account != ACCOUNT:
        raise JobRefusal(
            f"effective AWS account is {account or 'unknown'!r}, not the "
            f"MedZen account {ACCOUNT} — refusing to mutate anything "
            "under ambient credentials (Codex review #20)")


def review_record_approves(job_id: str, bindings: dict, root: Path,
                            oid: str) -> dict:
    """Codex review #22 finding 1 (reproduced: HOLD_BEFORE_LAST_MARKER_
    ACCEPTED): free-text window parsing is GONE. The review decision is a
    structured committed record at {REVIEWS_DIR}/<job_id>.json binding
    the exact packet sha; its `decision` field is an enum and ONLY
    "APPROVED" launches. A HOLD or CHANGES_REQUIRED decision is that
    record's current state until a NEW commit replaces it — nothing
    "nearby" can revive a held packet."""
    rel = f"{REVIEWS_DIR}/{job_id}.json"
    body = _show_at(root, oid, rel)
    if body is None:
        raise JobRefusal(f"no committed review record at {rel} — "
                         "free-text log entries no longer authorize "
                         "(Codex review #22)")
    record = json.loads(body)
    if record.get("job_id") != job_id:
        raise JobRefusal(f"review record {rel} names a different job")
    if record.get("bindings_sha256") != canonical_bindings_sha256(bindings):
        raise JobRefusal("review record binds a DIFFERENT packet sha — "
                         "a mutated packet cannot ride an old review")
    if record.get("decision") != "APPROVED":
        raise JobRefusal(f"review decision is {record.get('decision')!r}, "
                         "not APPROVED — the packet is held")
    if not str(record.get("basis") or "").strip():
        raise JobRefusal("review record lacks a basis")
    return record


def verify_calibration_receipt(bindings: dict,
                                repo_root: Path | None = None,
                                head_oid: str | None = None) -> dict:
    """Codex reviews #20-#22. The COMMITTED CALIBRATION PACKET is the
    recipe authority (a fabricated receipt cannot invent invariants):
    the arm environment must equal the calibration packet environment on
    EVERY key except the declared SCALE_KEYS. Receipt facts that only
    AWS knows (terminal status, billable time, image, artifact identity)
    are re-verified ONLINE at launch via verify_receipt_against_aws.
    Returns the parsed receipt record for that online step."""
    root = repo_root or Path(__file__).resolve().parents[1]
    oid = head_oid or repo_head_oid(root)
    receipt = bindings.get("calibration_receipt")
    if not isinstance(receipt, dict):
        raise JobRefusal(
            "above-tier multilingual packets must bind calibration_receipt "
            "{record, record_sha256} — an arm may never launch on an "
            "unproven chain (Codex reviews #20-#22)")
    rel = str(receipt.get("record") or "")
    if not rel.startswith("platform/evidence/"):
        raise JobRefusal(f"calibration_receipt.record {rel!r} must be a "
                         "repo-relative platform/evidence/ path")
    body = _show_at(root, oid, rel)
    if body is None:
        raise JobRefusal(f"calibration receipt {rel} is not committed at "
                         f"{oid[:12]}")
    if hashlib.sha256(body).hexdigest() != receipt.get("record_sha256"):
        raise JobRefusal("calibration_receipt.record_sha256 does not match "
                         f"the committed bytes of {rel}")
    record = json.loads(body)

    def field(name):
        value = record.get(name)
        if value in (None, "", [], {}):
            raise JobRefusal(f"calibration receipt lacks {name!r}")
        return value

    if field("terminal_status") != "Completed":
        raise JobRefusal("calibration terminal_status is not Completed")
    billable = record.get("billable_seconds")
    # bool is an int subclass — Codex review #22 reproduced True passing
    if type(billable) is not int or billable <= 0:
        raise JobRefusal("billable_seconds must be a positive integer")
    verdict = str(field("verdict"))
    if not (verdict == "PASS" or verdict.startswith(("PASS ", "PASS —"))):
        raise JobRefusal(f"verdict {verdict[:20]!r} is not PASS — "
                         "PASS-prefixed words (reproduced: PASSWORD) refuse")
    if sorted(record.get("declared_scale_keys") or []) != \
            sorted(SCALE_KEYS):
        raise JobRefusal("receipt must declare EXACTLY the permitted "
                         "calibration-to-pilot scale keys")
    env = bindings["environment"]
    # recipe authority: the COMMITTED calibration packet
    packet_rel = str(field("calibration_packet"))
    if not packet_rel.startswith("platform/manifests/"):
        raise JobRefusal("calibration_packet must live under "
                         "platform/manifests/")
    packet_body = _show_at(root, oid, packet_rel)
    if packet_body is None:
        raise JobRefusal(f"calibration packet {packet_rel} is not committed")
    cal = json.loads(packet_body)
    if canonical_bindings_sha256(cal) != \
            field("calibration_bindings_sha256"):
        raise JobRefusal("receipt calibration_bindings_sha256 does not "
                         "match the committed calibration packet")
    cal_env = cal.get("environment") or {}
    for key in sorted(set(cal_env) | set(env)):
        if key in SCALE_KEYS:
            continue
        if cal_env.get(key) != env.get(key):
            raise JobRefusal(
                f"recipe drift on {key}: calibration proved "
                f"{cal_env.get(key)!r}, the arm binds {env.get(key)!r} — "
                "only the declared scale keys may differ (Codex #22)")
    if cal.get("image_uri_with_digest") != \
            bindings.get("image_uri_with_digest") or \
            record.get("image_uri_with_digest") != \
            bindings.get("image_uri_with_digest"):
        raise JobRefusal("the arm binds a different image digest than the "
                         "calibrated chain")
    version = env.get("MEDZEN_MANIFEST_VERSION", "")
    if field("dataset_version") != version:
        raise JobRefusal(
            f"receipt proves dataset {record.get('dataset_version')!r} but "
            f"the arm binds {version!r} — the wrong calibration cannot "
            "justify this arm")
    adoption_rel = (f"platform/evidence/"
                    f"B5-{version.upper()}-ADOPTION-2026-001.json")
    adoption_body = _show_at(root, oid, adoption_rel)
    if adoption_body is None:
        raise JobRefusal(f"no committed adoption evidence {adoption_rel}")
    if field("dataset_complete_raw_sha256") != \
            json.loads(adoption_body)["complete_raw_sha256"]:
        raise JobRefusal("receipt dataset adoption sha does not match the "
                         f"committed {version} adoption")
    export = field("export")
    if export.get("status") != "PASS_MERGED_EXPORT" or not (
            _hex(str(export.get("model_sha256", "")), 64)
            and _hex(str(export.get("manifest_sha256", "")), 64)):
        raise JobRefusal("receipt export block is not a hash-complete "
                         "PASS_MERGED_EXPORT")
    artifact = field("artifact")
    if not artifact.get("s3_version_id"):
        raise JobRefusal("receipt artifact block lacks an S3 VersionId")
    if not str(artifact.get("kms_key", "")).startswith(
            f"arn:aws:kms:{REGION}:{ACCOUNT}:key/"):
        raise JobRefusal("receipt artifact KMS key is not this account's "
                         "in-region key (Codex review #22: wrong-account "
                         "ARN was accepted)")
    return record


def verify_receipt_against_aws(record: dict, sagemaker_client,
                                s3_client) -> None:
    """Codex review #22: a committed receipt is still a repo writer's
    document — the facts only AWS knows are re-queried at launch on the
    SAME session that will create the training job."""
    job = record["job"]
    desc = sagemaker_client.describe_training_job(TrainingJobName=job)
    if desc.get("TrainingJobStatus") != "Completed":
        raise JobRefusal(f"AWS says calibration {job} is "
                         f"{desc.get('TrainingJobStatus')!r}, not Completed")
    if desc.get("BillableTimeInSeconds") != record["billable_seconds"]:
        raise JobRefusal("AWS billable seconds do not match the receipt")
    if desc.get("AlgorithmSpecification", {}).get("TrainingImage") != \
            record["image_uri_with_digest"]:
        raise JobRefusal("AWS training image does not match the receipt")
    artifact = record["artifact"]
    uri = artifact["s3_uri"]
    bucket, key = uri.replace("s3://", "", 1).split("/", 1)
    head = s3_client.head_object(Bucket=bucket, Key=key)
    if head.get("VersionId") != artifact["s3_version_id"]:
        raise JobRefusal("artifact S3 VersionId does not match AWS")
    if head.get("SSEKMSKeyId") != artifact["kms_key"]:
        raise JobRefusal("artifact KMS identity does not match AWS")


def _hex(value: str, length: int) -> bool:
    return len(value) == length and all(
        c in "0123456789abcdef" for c in value.lower())


def verify_active_reservation(registry_binding: dict, bindings: dict,
                               worst_case_usd: float,
                               repo_root: Path | None = None,
                               head_oid: str | None = None) -> None:
    """Codex reviews #21-#22. The committed registry must show THIS
    packet's allocation as the single ACTIVE_RESERVED line, sized for
    the worst case, BOUND TO THIS PACKET'S SHA (a different packet's
    reservation was accepted), and the registry's own arithmetic must
    hold: recognized + active reservations within the aggregate ceiling
    (an over-budget registry was accepted)."""
    root = repo_root or Path(__file__).resolve().parents[1]
    oid = head_oid or repo_head_oid(root)
    if not isinstance(registry_binding, dict):
        raise JobRefusal("launch intent must bind the cost registry "
                         "{file, sha256, allocation_id}")
    rel = str(registry_binding.get("file") or "")
    if not rel.startswith("platform/finance/"):
        raise JobRefusal("registry binding must live under "
                         "platform/finance/")
    body = _show_at(root, oid, rel)
    if body is None:
        raise JobRefusal(f"registry {rel} is not committed at {oid[:12]}")
    if hashlib.sha256(body).hexdigest() != registry_binding.get("sha256"):
        raise JobRefusal("registry binding sha256 does not match the "
                         f"committed bytes of {rel}")
    registry = json.loads(body)
    allocation_id = registry_binding.get("allocation_id")
    effective: dict[str, dict] = {}
    for line in registry.get("allocations", []):
        if line.get("allocation_id"):
            effective[line["allocation_id"]] = line
    ours = effective.get(allocation_id)
    if ours is None:
        raise JobRefusal(f"allocation {allocation_id!r} does not exist")
    if ours.get("financial_state") != "ACTIVE_RESERVED":
        raise JobRefusal(
            f"allocation {allocation_id!r} is "
            f"{ours.get('financial_state')!r}, not ACTIVE_RESERVED")
    if float(ours.get("reservation_usd", 0)) < worst_case_usd:
        raise JobRefusal("active reservation does not cover the worst case")
    if ours.get("packet_bindings_sha256") != \
            canonical_bindings_sha256(bindings):
        raise JobRefusal(
            "the ACTIVE reservation is bound to a DIFFERENT packet sha — "
            "another packet's reservation cannot fund this launch "
            "(Codex review #22)")
    active = {k: line for k, line in effective.items()
              if line.get("financial_state") == "ACTIVE_RESERVED"}
    if list(active) != [allocation_id]:
        raise JobRefusal(
            f"one-active-reservation rule violated: {sorted(active)}")
    summary = registry.get("guardrail_summary") or {}
    ceiling = float(summary.get("aggregate_ceiling_usd", 0))
    recognized = float(summary.get("recognized_committed_guardrail_usd", 0))
    active_sum = sum(float(line.get("reservation_usd", 0))
                     for line in active.values())
    if abs(float(summary.get("active_reservations_usd", -1))
           - active_sum) > 0.01:
        raise JobRefusal("registry summary active_reservations_usd does "
                         "not match its own allocation lines")
    if recognized + active_sum > ceiling + 1e-9:
        raise JobRefusal(
            f"registry arithmetic breaches the aggregate ceiling: "
            f"{recognized:.2f} recognized + {active_sum:.2f} reserved > "
            f"{ceiling:.2f}")


def owner_intent_is_signed(job_id: str, root: Path, oid: str,
                            identity: str = "owner@medzen") -> dict:
    """Owner authorization v4 (Codex review #22: commit ids are
    DETERMINISTIC — the reviewer precomputed a future commit sha, so
    oid-quoting proves reference, not order or identity). The owner now
    SIGNS: an SSH signature (ssh-keygen -Y, namespace medzen-launch)
    over the committed launch-intent record's exact bytes, verified
    against the COMMITTED allowed-signers file. The signing key lives
    with the owner; no repository write can conjure a signature.

    Residual trust, stated: if the owner's private key is readable on
    this machine, a local actor could still sign. The upgrade path
    (GitHub protected-environment approval on the existing remote)
    removes even that."""
    if not job_id or not all(c.islower() or c.isdigit() or c == "-"
                             for c in job_id):
        raise JobRefusal("malformed job id")
    intent_rel = f"{INTENTS_DIR}/{job_id}.json"
    sig_rel = f"{INTENTS_DIR}/{job_id}.sig"
    intent_body = _show_at(root, oid, intent_rel)
    if intent_body is None:
        raise JobRefusal(f"no committed launch intent at {intent_rel}")
    sig_body = _show_at(root, oid, sig_rel)
    if sig_body is None:
        raise JobRefusal(f"no committed owner signature at {sig_rel} — "
                         "the owner has not authorized this launch")
    signers_body = _show_at(root, oid, ALLOWED_SIGNERS)
    if signers_body is None:
        raise JobRefusal(f"no committed {ALLOWED_SIGNERS} — enroll the "
                         "owner's signing key first")
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        signers = Path(td) / "allowed_signers"
        signers.write_bytes(signers_body)
        sig = Path(td) / "intent.sig"
        sig.write_bytes(sig_body)
        completed = subprocess.run(
            ["ssh-keygen", "-Y", "verify", "-f", str(signers),
             "-I", identity, "-n", SIGN_NAMESPACE, "-s", str(sig)],
            input=intent_body, capture_output=True)
    if completed.returncode != 0:
        raise JobRefusal(
            "owner signature does NOT verify over the committed launch "
            "intent — refusing (" +
            (completed.stderr or b"").decode(errors="replace")[:120] + ")")
    intent = json.loads(intent_body)
    if intent.get("job_id") != job_id:
        raise JobRefusal("launch intent names a different job")
    return intent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("render", "validate", "launch"))
    parser.add_argument("--bindings", type=Path, required=True)
    parser.add_argument("--request", type=Path)
    args = parser.parse_args()
    bindings = json.loads(args.bindings.read_bytes())

    try:
        if args.mode == "render":
            print(json.dumps(render_request(bindings), indent=1, sort_keys=True))
            return 0
        if args.request is None:
            raise JobRefusal(f"--request is required for {args.mode}")
        request = json.loads(args.request.read_bytes())
        result = validate_request(request, bindings)
        if args.mode == "validate":
            print(json.dumps(result, sort_keys=True))
            return 0
        job_id = bindings["job_id"]
        worst_case = result["worst_case_on_demand_usd"]
        root = Path(__file__).resolve().parents[1]
        head = repo_head_oid(root)
        review_record_approves(job_id, bindings, root, head)
        receipt_record = None
        if worst_case > CALIBRATION_TIER_USD:
            intent = owner_intent_is_signed(job_id, root, head)
            packet_rel = str(intent.get("packet", {}).get("file") or "")
            packet_body = _show_at(root, head, packet_rel)
            if packet_body is None or canonical_bindings_sha256(
                    json.loads(packet_body)) != \
                    canonical_bindings_sha256(bindings) or \
                    intent["packet"].get("canonical_sha256") != \
                    canonical_bindings_sha256(bindings):
                raise JobRefusal("the signed intent binds a DIFFERENT "
                                 "packet than the one launching")
            receipt_record = verify_calibration_receipt(bindings,
                                                        head_oid=head)
            if intent.get("receipt", {}).get("record_sha256") != \
                    bindings["calibration_receipt"]["record_sha256"]:
                raise JobRefusal("the signed intent binds a different "
                                 "calibration receipt")
            verify_active_reservation(intent.get("registry"), bindings,
                                      worst_case, head_oid=head)
            load_protocol(root, oid=head)   # committed-bytes re-check
        # ONE boto3 session: STS pin + AWS receipt facts + the mutation
        import boto3
        session = boto3.session.Session(region_name=REGION)
        account = session.client("sts").get_caller_identity().get("Account")
        if account != ACCOUNT:
            raise JobRefusal(
                f"effective AWS account is {account!r}, not the MedZen "
                f"account {ACCOUNT} — refusing to launch")
        if receipt_record is not None:
            verify_receipt_against_aws(receipt_record,
                                       session.client("sagemaker"),
                                       session.client("s3"))
        response = session.client("sagemaker").create_training_job(**request)
        print(json.dumps({"TrainingJobArn": response["TrainingJobArn"]},
                         indent=4))
        return 0
    except JobRefusal as exc:
        print(json.dumps({"status": "REFUSED", "detail": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
