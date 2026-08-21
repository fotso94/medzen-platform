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
        protocol = json.loads(
            (Path(__file__).resolve().parents[1] / "platform/decisions/"
             "PROMOTION-PROTOCOL-2026-003.json").read_bytes())
        mandatory = set(protocol["mandatory_languages"])
        requested = {t.strip() for t in
                     environment.get("MEDZEN_LANGUAGES", "").split(",")
                     if t.strip()}
        if requested != mandatory:
            raise JobRefusal(
                f"multilingual-full packets bind the frozen pilot set "
                f"{sorted(mandatory)} exactly; got {sorted(requested)}")
        if environment.get("MEDZEN_MANIFEST_VERSION") != "gb7":
            raise JobRefusal(
                "multilingual-full packets bind dataset version gb7 "
                "(B5-GB7 — the 7-language revision incl. pidgin)")
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
    text = shared_file.read_text()
    marker = f"authorizing training job {job_id} "
    if marker not in text:
        return False
    if "DECISION: APPROVED" not in text.split(marker)[0][-4000:]:
        return False
    if bindings is not None:
        sha = canonical_bindings_sha256(bindings)
        window = text.split(marker)[0][-4000:] + marker + \
            text.split(marker, 1)[1][:400]
        if f"bindings-sha256 {sha}" not in window:
            return False
    return True


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
                f"found in {SHARED_REVIEWS} — the authorization must cite "
                "the exact packet (Codex review #9)")
        completed = subprocess.run(
            ["aws", "sagemaker", "create-training-job",
             "--region", REGION,
             "--cli-input-json", json.dumps(request, sort_keys=True)],
            capture_output=True, text=True)
        if completed.returncode != 0:
            raise JobRefusal(f"create-training-job failed: "
                             f"{completed.stderr[-400:]}")
        print(completed.stdout.strip())
        return 0
    except JobRefusal as exc:
        print(json.dumps({"status": "REFUSED", "detail": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
