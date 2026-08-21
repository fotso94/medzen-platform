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
        root_dir = Path(__file__).resolve().parents[1]
        pointer = json.loads((root_dir / "platform/decisions/"
                              "CURRENT-PROMOTION-PROTOCOL.json").read_bytes())
        protocol_body = (root_dir / pointer["file"]).read_bytes()
        if hashlib.sha256(protocol_body).hexdigest() != pointer["sha256"]:
            raise JobRefusal("promotion-protocol file does not match the "
                             "committed pointer hash (Codex review #21)")
        protocol = json.loads(protocol_body)
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


def review_is_recorded(job_id: str, bindings: dict | None = None,
                       shared_file: Path = SHARED_REVIEWS) -> bool:
    """Independent-review channel. Codex review #21 parser fixes: the
    LAST mention of the job governs (not the first), the APPROVED must
    sit adjacent to that mention (2,000 before / 400 after — an
    unrelated earlier approval 4k away no longer qualifies), and any
    LATER `DECISION: HOLD` overrides an older approval."""
    text = shared_file.read_text()
    marker = f"authorizing training job {job_id} "
    idx = text.rfind(marker)
    if idx < 0:
        return False
    window = text[max(0, idx - 2000):idx + len(marker) + 400]
    if "DECISION: APPROVED" not in window:
        return False
    if "DECISION: HOLD" in text[idx:]:
        return False
    if bindings is not None:
        sha = canonical_bindings_sha256(bindings)
        if f"bindings-sha256 {sha}" not in window:
            return False
    return True


CALIBRATION_TIER_USD = 10.0
AUTH_DIR = "platform/decisions/launch-authorizations"
APPROVALS_DIR = Path.home() / "Documents/medzen-shared/authorizations"
_HEX40 = re.compile(r"^[0-9a-f]{40}$")


def repo_head_oid(root: Path) -> str:
    """One captured commit OID for EVERY committed-evidence read in a
    launch decision (Codex review #21: separate HEAD resolutions could
    read different commits mid-decision)."""
    completed = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                               capture_output=True, text=True)
    oid = (completed.stdout or "").strip()
    if completed.returncode != 0 or not _HEX40.fullmatch(oid):
        raise JobRefusal("cannot resolve a git HEAD commit — committed-"
                         "evidence gates need a repository")
    return oid


def _show_at(root: Path, oid: str, rel: str) -> bytes | None:
    if rel.startswith(("/", "..")) or ":" in rel or "\\" in rel:
        return None
    completed = subprocess.run(["git", "-C", str(root), "show",
                                f"{oid}:{rel}"],
                               capture_output=True)
    return completed.stdout if completed.returncode == 0 else None


def assert_medzen_account(runner=subprocess.run) -> None:
    """Codex review #20 finding 5: launch relied on ambient credentials;
    this machine's DEFAULT AWS account is not MedZen's. Any mutation must
    first prove the effective identity lives in account 558069890522.
    (The launch path itself now uses ONE boto3 session for both the STS
    check and CreateTrainingJob — no credential TOCTOU; this CLI-based
    variant remains for tooling and tests.)"""
    completed = runner(["aws", "sts", "get-caller-identity",
                        "--query", "Account", "--output", "text"],
                       capture_output=True, text=True)
    account = (completed.stdout or "").strip()
    if completed.returncode != 0 or account != ACCOUNT:
        raise JobRefusal(
            f"effective AWS account is {account or 'unknown'!r}, not the "
            f"MedZen account {ACCOUNT} — refusing to mutate anything "
            "under ambient credentials (Codex review #20)")


RECEIPT_INVARIANTS = ("MEDZEN_VARIANT", "MEDZEN_TRAIN_MODE",
                      "MEDZEN_LANGUAGES", "MEDZEN_TEMPERATURE",
                      "MEDZEN_EXCLUSIONS_REF", "MEDZEN_EXPECT_EXCLUDED",
                      "MEDZEN_MANIFEST_VERSION")


def verify_calibration_receipt(bindings: dict,
                                repo_root: Path | None = None,
                                head_oid: str | None = None) -> None:
    """Codex review #20 finding 4 + review #21: the first version only
    checked committed+sha+PASS with the dataset sha supplied BY THE
    PACKET — the genuine gb8 receipt authorized a gb9 arm. Every
    semantic identity now comes from the RECEIPT RECORD itself and is
    cross-checked against the arm packet and committed evidence at ONE
    captured commit:
      terminal AWS status + billable seconds; the calibration packet's
      canonical sha (recomputed from the committed packet); dataset
      version + adoption sha; image digest; the invariant training
      environment; export status + hashes; artifact VersionId + KMS."""
    root = repo_root or Path(__file__).resolve().parents[1]
    oid = head_oid or repo_head_oid(root)
    receipt = bindings.get("calibration_receipt")
    if not isinstance(receipt, dict):
        raise JobRefusal(
            "above-tier multilingual packets must bind calibration_receipt "
            "{record, record_sha256} — an arm may never launch on an "
            "unproven chain (Codex reviews #20-#21)")
    rel = str(receipt.get("record") or "")
    if not rel.startswith("platform/evidence/"):
        raise JobRefusal(f"calibration_receipt.record {rel!r} must be a "
                         "repo-relative platform/evidence/ path")
    body = _show_at(root, oid, rel)
    if body is None:
        raise JobRefusal(f"calibration receipt {rel} is not committed at "
                         f"{oid[:12]} — working-tree receipts do not count")
    if hashlib.sha256(body).hexdigest() != receipt.get("record_sha256"):
        raise JobRefusal("calibration_receipt.record_sha256 does not match "
                         f"the committed bytes of {rel}")
    record = json.loads(body)

    def field(name):
        value = record.get(name)
        if value in (None, "", [], {}):
            raise JobRefusal(f"calibration receipt lacks {name!r} — a "
                             "receipt that cannot be externally verified "
                             "cannot justify an arm")
        return value

    if field("terminal_status") != "Completed":
        raise JobRefusal("calibration terminal_status is not Completed")
    if not isinstance(record.get("billable_seconds"), int) or             record["billable_seconds"] <= 0:
        raise JobRefusal("calibration receipt lacks positive "
                         "billable_seconds")
    if not str(field("verdict")).startswith("PASS"):
        raise JobRefusal("calibration verdict is not PASS")
    env = bindings["environment"]
    if field("dataset_version") != env.get("MEDZEN_MANIFEST_VERSION"):
        raise JobRefusal(
            f"receipt proves dataset {record.get('dataset_version')!r} but "
            f"the arm binds {env.get('MEDZEN_MANIFEST_VERSION')!r} — the "
            "wrong calibration cannot justify this arm (Codex review #21)")
    version = env.get("MEDZEN_MANIFEST_VERSION", "")
    adoption_rel = (f"platform/evidence/"
                    f"B5-{version.upper()}-ADOPTION-2026-001.json")
    adoption_body = _show_at(root, oid, adoption_rel)
    if adoption_body is None:
        raise JobRefusal(f"no committed adoption evidence {adoption_rel}")
    adoption = json.loads(adoption_body)
    if field("dataset_complete_raw_sha256") !=             adoption["complete_raw_sha256"]:
        raise JobRefusal("receipt dataset adoption sha does not match the "
                         f"committed {version} adoption")
    if field("image_uri_with_digest") !=             bindings.get("image_uri_with_digest"):
        raise JobRefusal("the arm binds a different image digest than the "
                         "calibration proved")
    invariants = field("environment_invariants")
    for key in RECEIPT_INVARIANTS:
        if invariants.get(key) != env.get(key):
            raise JobRefusal(
                f"invariant {key} differs from the calibrated chain: "
                f"receipt {invariants.get(key)!r} vs arm {env.get(key)!r}")
    packet_rel = str(field("calibration_packet"))
    packet_body = _show_at(root, oid, packet_rel)
    if packet_body is None:
        raise JobRefusal(f"calibration packet {packet_rel} is not "
                         "committed — the receipt cannot be re-derived")
    if canonical_bindings_sha256(json.loads(packet_body)) !=             field("calibration_bindings_sha256"):
        raise JobRefusal("receipt calibration_bindings_sha256 does not "
                         "match the committed calibration packet")
    export = field("export")
    if export.get("status") != "PASS_MERGED_EXPORT" or not (
            _hex(str(export.get("model_sha256", "")), 64)
            and _hex(str(export.get("manifest_sha256", "")), 64)):
        raise JobRefusal("receipt export block is not a hash-complete "
                         "PASS_MERGED_EXPORT")
    artifact = field("artifact")
    if not artifact.get("s3_version_id") or not str(
            artifact.get("kms_key", "")).startswith("arn:aws:kms:"):
        raise JobRefusal("receipt artifact block lacks S3 VersionId or "
                         "KMS identity")


def _hex(value: str, length: int) -> bool:
    return len(value) == length and all(
        c in "0123456789abcdef" for c in value.lower())


def verify_active_reservation(bindings: dict, worst_case_usd: float,
                               repo_root: Path | None = None,
                               head_oid: str | None = None) -> None:
    """Codex review #21: the launcher never read the cost registry, so a
    job could launch while its reservation said PENDING. The packet
    binds the registry file's sha256; at launch the committed registry
    must show the packet's allocation as the SINGLE ACTIVE_RESERVED
    line, sized for the worst case."""
    root = repo_root or Path(__file__).resolve().parents[1]
    oid = head_oid or repo_head_oid(root)
    binding = bindings.get("cost_registry_binding")
    if not isinstance(binding, dict):
        raise JobRefusal(
            "above-tier packets must bind cost_registry_binding "
            "{file, sha256, allocation_id} — reserve BEFORE billable "
            "execution (Codex review #21)")
    rel = str(binding.get("file") or "")
    if not rel.startswith("platform/finance/"):
        raise JobRefusal("cost_registry_binding.file must live under "
                         "platform/finance/")
    body = _show_at(root, oid, rel)
    if body is None:
        raise JobRefusal(f"registry {rel} is not committed at {oid[:12]}")
    if hashlib.sha256(body).hexdigest() != binding.get("sha256"):
        raise JobRefusal("cost_registry_binding.sha256 does not match the "
                         f"committed bytes of {rel}")
    registry = json.loads(body)
    allocation_id = binding.get("allocation_id")
    effective: dict[str, dict] = {}
    for line in registry.get("allocations", []):
        if line.get("allocation_id"):
            effective[line["allocation_id"]] = line
    ours = effective.get(allocation_id)
    if ours is None:
        raise JobRefusal(f"allocation {allocation_id!r} does not exist in "
                         f"{rel}")
    if ours.get("financial_state") != "ACTIVE_RESERVED":
        raise JobRefusal(
            f"allocation {allocation_id!r} is "
            f"{ours.get('financial_state')!r}, not ACTIVE_RESERVED — the "
            "reservation must be activated (a new registry revision, "
            "committed) BEFORE launch")
    if float(ours.get("reservation_usd", 0)) < worst_case_usd:
        raise JobRefusal(
            f"active reservation ${ours.get('reservation_usd')} does not "
            f"cover the ${worst_case_usd:.2f} worst case")
    active = [k for k, line in effective.items()
              if line.get("financial_state") == "ACTIVE_RESERVED"]
    if active != [allocation_id]:
        raise JobRefusal(
            f"one-active-reservation rule violated: {sorted(active)}")


def owner_authorization_is_committed(job_id: str, bindings: dict,
                                     worst_case_usd: float,
                                     repo_root: Path | None = None,
                                     approvals_dir: Path | None = None,
                                     head_oid: str | None = None) -> bool:
    """Owner-authorization gate v3 (Codex reviews #19-#21).

    v2 failed because the approval phrase was knowable in advance — it
    was even pre-published — and the free-text window parser accepted
    any nearby stale DECISION: APPROVED. v3:

      1. The authorization record must be COMMITTED at the captured
         commit under {AUTH_DIR}/<job_id>.json, binding the canonical
         packet sha, an INTEGER-VALUED ceiling >= worst case (a $70.9
         ceiling can no longer masquerade as '70'), non-transferability
         and the owner's statement.
      2. The approval phrase must quote the first 16 hex of the COMMIT
         THAT INTRODUCED THE RECORD — unknowable before that commit
         exists, so pre-published phrases are structurally dead.
      3. The phrase lives alone in a DEDICATED file
         (~/Documents/medzen-shared/authorizations/<job_id>.approval)
         compared by exact equality — no windows, no free-text parsing.

    HONEST RESIDUAL TRUST: every channel still lives on one machine;
    a determined local writer can fabricate all three artifacts. Real
    separation of authority needs owner signing keys or the GitHub
    protected-environment approval once remote CI activates — until
    then this gate guarantees ORDER (record first, approval second),
    EXACTNESS and AUDITABILITY, not cryptographic identity."""
    if not job_id or not all(c.islower() or c.isdigit() or c == "-"
                             for c in job_id):
        return False
    root = repo_root or Path(__file__).resolve().parents[1]
    try:
        oid = head_oid or repo_head_oid(root)
    except JobRefusal:
        return False
    rel = f"{AUTH_DIR}/{job_id}.json"
    body = _show_at(root, oid, rel)
    if body is None:
        return False
    try:
        record = json.loads(body)
    except ValueError:
        return False
    ceiling = record.get("ceiling_usd")
    if not (record.get("job_id") == job_id
            and record.get("bindings_sha256")
                == canonical_bindings_sha256(bindings)
            and isinstance(ceiling, (int, float))
            and float(ceiling).is_integer()
            and ceiling >= worst_case_usd
            and record.get("non_transferable") is True
            and bool(str(record.get("owner_statement") or "").strip())):
        return False
    log = subprocess.run(
        ["git", "-C", str(root), "log", "-1", "--format=%H", oid,
         "--", rel], capture_output=True, text=True)
    record_commit = (log.stdout or "").strip()
    if log.returncode != 0 or not _HEX40.fullmatch(record_commit):
        return False
    expected_phrase = (f"I authorize {job_id} ceiling usd {int(ceiling)} "
                       f"bindings-sha256-16 "
                       f"{record['bindings_sha256'][:16]} "
                       f"record-commit {record_commit[:16]}")
    approval_path = (approvals_dir or APPROVALS_DIR) / f"{job_id}.approval"
    try:
        approval = approval_path.read_text().strip()
    except OSError:
        return False
    return approval == expected_phrase


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
        if not review_is_recorded(job_id, bindings):
            raise JobRefusal(
                f"no APPROVED review binding training-job {job_id} AND "
                f"bindings-sha256 {canonical_bindings_sha256(bindings)} "
                f"adjacent to its LAST mention in {SHARED_REVIEWS} (a "
                "later DECISION: HOLD overrides) — Codex reviews #9/#21")
        worst_case = result["worst_case_on_demand_usd"]
        root = Path(__file__).resolve().parents[1]
        head = repo_head_oid(root)
        if worst_case > CALIBRATION_TIER_USD:
            if not owner_authorization_is_committed(job_id, bindings,
                                                    worst_case,
                                                    head_oid=head):
                raise JobRefusal(
                    f"worst case ${worst_case:.2f} exceeds the calibration "
                    f"tier (${CALIBRATION_TIER_USD:.0f}) and the owner "
                    f"authorization gate v3 is not satisfied: committed "
                    f"record at {AUTH_DIR}/{job_id}.json + the exact "
                    f"approval phrase (quoting that record's commit) in "
                    f"the dedicated approvals file (Codex reviews "
                    f"#19-#21)")
            verify_calibration_receipt(bindings, head_oid=head)
            verify_active_reservation(bindings, worst_case, head_oid=head)
        # ONE boto3 session for the account check AND the mutation — no
        # credential TOCTOU between separate CLI processes (review #21)
        import boto3
        session = boto3.session.Session(region_name=REGION)
        account = session.client("sts").get_caller_identity().get("Account")
        if account != ACCOUNT:
            raise JobRefusal(
                f"effective AWS account is {account!r}, not the MedZen "
                f"account {ACCOUNT} — refusing to launch")
        response = session.client("sagemaker").create_training_job(**request)
        print(json.dumps({"TrainingJobArn": response["TrainingJobArn"]},
                         indent=4))
        return 0
    except JobRefusal as exc:
        print(json.dumps({"status": "REFUSED", "detail": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
