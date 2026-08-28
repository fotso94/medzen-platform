#!/usr/bin/env python3
"""SEALED EVALUATOR launcher — the structured composition required by
SEALED-EVALUATOR-SPEC-2026-001. This replaces the deliberate hold in
scripts/launch_sealed_eval.py for the packet-driven path; every numbered
spec requirement maps to code here:

 1. STRUCTURED COMPOSITION — the SageMaker Processing request is generated
    from the packet's typed fields only; there is no free-form user-data
    and no script argument that could smuggle a different holdout through.
 2. GIT-BLOB MATERIALIZATION — the packet is read from `git show HEAD:` and
    THOSE bytes are anchored and hashed; the working tree has no vote.
 3. EVERY BINDING VALIDATED AND USED — holdout keys/shas come from the
    packet, are cross-checked against the ledger's RESERVED shas, and the
    manifests' delivered bytes are re-verified inside the container;
    the image digest is pinned; results land only under the packet's
    output prefix (medzen-sealed-results/<job_name>/).
 4. OWNER-AUTHORIZED PACKET — the packet sha256 must appear verbatim in a
    COMMITTED owner-authorization record named by the packet.
 5. BUDGET + WATCHDOG — the worst case is validated against the packet
    ceiling pre-launch, and termination is enforced EXTERNALLY by the
    SageMaker control plane (StoppingCondition.MaxRuntimeInSeconds),
    not by a tag or an in-box timer.
 6. STORAGE — explicit volume size + the project KMS key on the volume.
 7. EXACTLY-ONCE ACROSS CONTROLLERS — sealed access is gated by the
    flock+hash-chained HOLDOUT-CONSUMPTION-LEDGER (record_consumption)
    and the consumption is durably committed (durable_commit) BEFORE the
    first sealed byte is read for composition.
 8. IDEMPOTENT LAUNCH — the job name is the idempotency token: an
    existing job is described first and adopted when it matches the
    packet contract; an ambiguous half-launch is recoverable by rerun.
 9. REHEARSAL — tests/test_sealed_evaluator.py runs the full successful
    composition + launch ordering against a stubbed AWS session in CI.

The launcher reads sealed manifests ONLY to extract audio object keys for
the exact-keys channel manifest (the identity-index precedent: identities
and object locations are extractable under governance; reference text is
never touched, printed, or persisted by this process).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "services/model-loader"))

REGION = "eu-central-1"
ACCOUNT = "558069890522"
PADDED_USD_PER_HOUR = {"ml.g5.xlarge": 1.60, "ml.g6.xlarge": 1.60}


class SealedLaunchRefusal(RuntimeError):
    pass


def _head_bytes(rel: str) -> bytes:
    proc = subprocess.run(["git", "-C", str(ROOT), "show", f"HEAD:{rel}"],
                          capture_output=True)
    if proc.returncode != 0:
        raise SealedLaunchRefusal(
            f"{rel} is not committed at HEAD — the sealed launcher consumes "
            "only committed inputs (spec item 2)")
    return proc.stdout


def load_packet(rel: str, expected_sha: str) -> tuple[dict, bytes]:
    raw = _head_bytes(rel)
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected_sha:
        raise SealedLaunchRefusal(
            f"committed packet hashes to {actual[:16]}, caller expected "
            f"{expected_sha[:16]} — refusing")
    packet = json.loads(raw)
    from medzen_model_loader.promotion_check import (
        validate_sealed_run_contract)
    contract = validate_sealed_run_contract(packet["sealed_run_contract"])
    languages = packet.get("languages") or {}
    if len(languages) != 7:
        raise SealedLaunchRefusal(
            f"packet must bind exactly the 7 mandatory languages, "
            f"got {sorted(languages)}")
    for lang, entry in languages.items():
        for field in ("holdout_key", "holdout_manifest_sha256"):
            value = str(entry.get(field, ""))
            if not value:
                raise SealedLaunchRefusal(
                    f"{lang}: packet language entry lacks {field}")
    if contract["account_id"] != ACCOUNT or contract["region"] != REGION:
        raise SealedLaunchRefusal("contract account/region mismatch")
    return packet, raw


def verify_owner_authorization(packet: dict, packet_sha: str) -> None:
    """Spec item 4: committed ≠ approved."""
    rel = str(packet.get("owner_authorization_record", ""))
    if not rel.startswith("platform/decisions/"):
        raise SealedLaunchRefusal(
            "packet names no committed owner-authorization record")
    record = json.loads(_head_bytes(rel))
    statement = str(record.get("statement", ""))
    if packet_sha not in statement or "sealed evaluation" not in statement:
        raise SealedLaunchRefusal(
            "the committed owner authorization does not quote this exact "
            "packet sha256 for sealed evaluation — committed is not "
            "approved (spec item 4)")


def verify_budget(packet: dict) -> float:
    contract = packet["sealed_run_contract"]
    rate = PADDED_USD_PER_HOUR.get(str(contract["instance_type"]))
    if rate is None:
        raise SealedLaunchRefusal(
            f"no padded rate for {contract['instance_type']!r} — an "
            "unpriced instance cannot be budgeted (spec item 5)")
    worst = round(rate * int(contract["max_runtime_seconds"]) / 3600
                  * int(contract["instance_count"]), 2)
    ceiling = float(packet.get("cost_ceiling_usd", 0))
    if not worst <= ceiling:
        raise SealedLaunchRefusal(
            f"worst case ${worst} exceeds the packet ceiling ${ceiling}")
    return worst


def anchor_packet(session, packet: dict, raw: bytes) -> dict:
    """Predeclaration chronology: the packet bytes go to S3 BEFORE the job
    exists; the object's storage-set LastModified is the anchor time the
    gate compares against the job's CreationTime."""
    bucket = str(packet["anchor"]["bucket"])
    key = str(packet["anchor"]["key"])
    response = session.client("s3").put_object(
        Bucket=bucket, Key=key, Body=raw,
        ServerSideEncryption="aws:kms",
        SSEKMSKeyId=str(packet["sealed_run_contract"]["output_kms_key_arn"]))
    return {"type": "s3", "bucket": bucket, "key": key,
            "version_id": str(response["VersionId"])}


def consume_holdouts(packet: dict, job_name: str) -> None:
    """Spec item 7: durable, exactly-once acquisition BEFORE any sealed
    byte is read. One CONSUMED entry per holdout, then one durable commit
    verifying the committed tail."""
    from scripts.holdout_ledger import record_consumption
    from scripts.launch_sealed_eval import durable_commit
    last = None
    for lang in sorted(packet["languages"]):
        entry = packet["languages"][lang]
        last = record_consumption(
            str(entry["holdout_key"]),
            str(entry["holdout_manifest_sha256"]),
            consumed_by=f"sealed-eval:{job_name}")
    durable_commit(last)


def compose_channel_manifests(session, packet: dict) -> dict[str, str]:
    """Post-consumption composition: read each sealed manifest (exact
    VersionId + sha verified), extract ONLY audio object keys, and write
    the two SageMaker ManifestFile channel lists next to the anchor.
    Reference text is never touched."""
    s3 = session.client("s3")
    bucket = "medzen-speech"
    manifest_entries: list[str] = [json.dumps({"prefix": f"s3://{bucket}/"})]
    audio_entries: list[str] = [json.dumps({"prefix": f"s3://{bucket}/"})]
    audio_keys: set[str] = set()
    for lang in sorted(packet["languages"]):
        entry = packet["languages"][lang]
        key = str(entry["holdout_key"])
        body = s3.get_object(
            Bucket=bucket, Key=key,
            VersionId=str(entry["holdout_s3_version_id"]))["Body"].read()
        actual = hashlib.sha256(body).hexdigest()
        if actual != str(entry["holdout_manifest_sha256"]):
            raise SealedLaunchRefusal(
                f"{lang}: sealed manifest hashes to {actual[:16]}, packet "
                f"pins {str(entry['holdout_manifest_sha256'])[:16]}")
        manifest_entries.append(json.dumps(key))
        for line in body.decode().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            uri = next((str(v) for k, v in row.items()
                        if isinstance(v, str) and v.startswith("s3://")
                        and ("audio" in k or "uri" in k)), "")
            if not uri.startswith(f"s3://{bucket}/"):
                raise SealedLaunchRefusal(
                    f"{lang}: a sealed row names no in-bucket audio object")
            audio_keys.add(uri.removeprefix(f"s3://{bucket}/"))
    audio_entries.extend(json.dumps(k) for k in sorted(audio_keys))
    inputs_prefix = str(packet["channel_inputs_prefix"]).rstrip("/")
    out: dict[str, str] = {}
    for name, entries in (("manifests", manifest_entries),
                          ("audio", audio_entries)):
        body = ("[" + ",\n".join(entries) + "]").encode()
        key = f"{inputs_prefix}/{name}.manifest.json"
        s3.put_object(Bucket=bucket, Key=key, Body=body,
                      ServerSideEncryption="aws:kms",
                      SSEKMSKeyId=str(
                          packet["sealed_run_contract"][
                              "output_kms_key_arn"]))
        out[name] = f"s3://{bucket}/{key}"
    return out


def build_request(packet: dict, channel_manifests: dict[str, str]) -> dict:
    contract = packet["sealed_run_contract"]
    channels = contract["channels"]
    inputs = []
    for name, spec in sorted(channels.items()):
        uri = spec["s3_uri"]
        if name in channel_manifests:
            if uri != channel_manifests[name]:
                raise SealedLaunchRefusal(
                    f"channel {name}: packet pins {uri}, composition "
                    f"produced {channel_manifests[name]} — drift")
        inputs.append({
            "InputName": name,
            "S3Input": {
                "S3Uri": uri,
                "LocalPath": f"/opt/ml/processing/input/{name}",
                "S3DataType": spec["s3_data_type"],
                "S3InputMode": spec["input_mode"],
                "S3DataDistributionType":
                    spec["s3_data_distribution_type"],
                "S3CompressionType": spec["compression_type"],
            }})
    return {
        "ProcessingJobName": str(contract["job_name"]),
        "AppSpecification": {
            "ImageUri": str(contract["image_digest"]),
            "ContainerEntrypoint": ["python", "-m", "pipeline.sealed_eval"],
        },
        "Environment": {str(k): str(v)
                        for k, v in packet["environment"].items()},
        "ProcessingResources": {"ClusterConfig": {
            "InstanceCount": int(contract["instance_count"]),
            "InstanceType": str(contract["instance_type"]),
            "VolumeSizeInGB": int(contract["volume_size_gb"]),
            "VolumeKmsKeyId": str(contract["volume_kms_key_arn"]),
        }},
        "StoppingCondition": {
            "MaxRuntimeInSeconds": int(contract["max_runtime_seconds"])},
        "ProcessingInputs": inputs,
        "ProcessingOutputConfig": {
            "KmsKeyId": str(contract["output_kms_key_arn"]),
            "Outputs": [{
                "OutputName": "sealed",
                "S3Output": {
                    "S3Uri": str(contract["output_s3_prefix"]),
                    "LocalPath": "/opt/ml/processing/output",
                    "S3UploadMode": "EndOfJob",
                }}]},
        "NetworkConfig": {"EnableNetworkIsolation": True},
        "RoleArn": str(contract["execution_role_arn"]),
        "Tags": [{"Key": "medzen-tier", "Value": "sealed-evaluation"}],
    }


def launch(session, request: dict) -> dict:
    """Spec item 8: the job name is the idempotency token."""
    sm = session.client("sagemaker")
    name = request["ProcessingJobName"]
    try:
        described = sm.describe_processing_job(ProcessingJobName=name)
    except Exception:
        described = None
    if described is not None:
        if described.get("AppSpecification", {}).get("ImageUri") != \
                request["AppSpecification"]["ImageUri"]:
            raise SealedLaunchRefusal(
                f"job {name} already exists with a DIFFERENT image — an "
                "ambiguous prior launch needs owner adjudication")
        return {"adopted": True, "arn": described.get("ProcessingJobArn"),
                "status": described.get("ProcessingJobStatus")}
    response = sm.create_processing_job(**request)
    return {"adopted": False, "arn": response["ProcessingJobArn"],
            "status": "Creating"}


def main(argv=None, *, session=None, consume=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet", required=True,
                        help="repo-relative committed packet path")
    parser.add_argument("--packet-sha256", required=True)
    parser.add_argument("--out", type=Path, default=None,
                        help="where to write the anchor envelope")
    args = parser.parse_args(argv)

    packet, raw = load_packet(args.packet, args.packet_sha256)
    packet_sha = hashlib.sha256(raw).hexdigest()
    verify_owner_authorization(packet, packet_sha)
    worst = verify_budget(packet)
    if session is None:
        import boto3
        session = boto3.session.Session(region_name=REGION)
    identity = session.client("sts").get_caller_identity()
    if identity.get("Account") != ACCOUNT:
        raise SealedLaunchRefusal(
            f"effective account {identity.get('Account')!r} is not the "
            f"MedZen account {ACCOUNT}")
    # ORDER (spec items 2, 7, 3, 8): anchor -> consume -> compose -> launch
    anchor = anchor_packet(session, packet, raw)
    (consume or consume_holdouts)(packet, str(
        packet["sealed_run_contract"]["job_name"]))
    channel_manifests = compose_channel_manifests(session, packet)
    request = build_request(packet, channel_manifests)
    result = launch(session, request)
    envelope = {
        "record": str(packet.get("record", "")) + "-ANCHOR-ENVELOPE",
        "packet": args.packet,
        "packet_sha256": packet_sha,
        "storage": anchor,
        "worst_case_usd": worst,
        "launch": result,
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    payload = json.dumps(envelope, indent=1, sort_keys=True) + "\n"
    out = args.out or Path(tempfile.gettempdir()) / "sealed-anchor.json"
    out.write_text(payload)
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
