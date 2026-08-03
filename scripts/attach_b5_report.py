#!/usr/bin/env python3
"""Attach a BLOCKED B5 report to the resolved MLflow run, never register.

Default execution is a local dry run. `--apply` additionally requires a
separate owner-approved AWS authorization record. The original attempt-5
snapshot is never overwritten: attachment metadata is written into a local
copy and uploaded to a new create-only snapshot key.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.b5_gates import FailClosedError, sha256_file  # noqa: E402
from pipeline.mlflow_sync import consistent_snapshot  # noqa: E402

BUCKET = "medzen-speech"
REGION = "eu-central-1"
PROFILE = "medzen"


def _load(path: Path) -> dict:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise FailClosedError(f"{path} is not a JSON object")
    return value


def _validate(snapshot: Path, report_path: Path, resolution_path: Path) -> dict:
    resolution = _load(resolution_path)
    report = _load(report_path)
    expected_snapshot = resolution["snapshot"]["sha256_expected"]
    if sha256_file(snapshot) != expected_snapshot:
        raise FailClosedError("MLflow source snapshot hash mismatch")
    report_sha = sha256_file(report_path)
    if report_path.stem != report_sha:
        raise FailClosedError("gate report filename is not its content address")
    if report.get("overall") != "BLOCKED":
        raise FailClosedError("refusal-only MLflow attachment requires BLOCKED")
    run_id = resolution["attachment_rule"]["target_run_id"]

    import mlflow
    mlflow.set_tracking_uri(f"sqlite:///{snapshot.resolve()}")
    client = mlflow.tracking.MlflowClient()
    run = client.get_run(run_id)
    expected = resolution["source_child_bindings"]
    params = run.data.params
    tags = run.data.tags
    checks = {
        "source_checkpoint": params.get("source_checkpoint"),
        "source_adapter_tree_sha256": params.get("source_adapter_tree_sha256"),
        "artifact_evaluation_sha256": params.get("artifact_evaluation_sha256"),
        "selected_precision": params.get("selected_precision"),
        "promotable": params.get("promotable"),
        "registered_models": params.get("registered_models"),
    }
    for key, value in checks.items():
        if value != expected[key]:
            raise FailClosedError(f"MLflow source run mismatch at {key}")
    if (tags.get("medzen.campaign_run") != expected["campaign_run"]
            or tags.get("medzen.attempt") != expected["attempt"]
            or tags.get("medzen.stage") != resolution["resolved_runs"][
                "source_child_stage"]):
        raise FailClosedError("MLflow source run tags do not match the resolution")
    registered = list(client.search_registered_models())
    versions = list(client.search_model_versions())
    if registered or versions:
        raise FailClosedError("MLflow registry is not empty before attachment")
    artifact_key = f"mlflow/artifacts/{run_id}/b5/gate-reports/{report_sha}.json"
    snapshot_key = (
        "mlflow/snapshots/b4-scoped-count-tolerance-61145b7/attempt-5/"
        f"b5-blocked-gate-report/{report_sha}/mlflow.db")
    return {
        "client": client,
        "run_id": run_id,
        "report": report,
        "report_sha256": report_sha,
        "artifact_key": artifact_key,
        "snapshot_key": snapshot_key,
        "registered_models_before": 0,
        "model_versions_before": 0,
    }


def _approval(path: Path) -> dict:
    decision = _load(path)
    if (decision.get("status") != "owner-approved"
            or "mlflow_blocked_report_attachment" not in decision.get(
                "approved_operations", [])):
        raise FailClosedError("AWS write approval does not authorize attachment")
    return decision


def _s3_client():
    import boto3
    return boto3.Session(profile_name=PROFILE, region_name=REGION).client("s3")


def _put_create_or_same(client, key: str, body: bytes, content_type: str,
                        tagging: str) -> str:
    from botocore.exceptions import ClientError
    digest = hashlib.sha256(body).hexdigest()
    try:
        result = client.put_object(
            Bucket=BUCKET, Key=key, Body=body, ContentType=content_type,
            IfNoneMatch="*", Tagging=tagging)
        return result.get("VersionId", "")
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") not in {
                "PreconditionFailed", "ConditionalRequestConflict"}:
            raise
        existing = client.get_object(Bucket=BUCKET, Key=key)["Body"].read()
        if hashlib.sha256(existing).hexdigest() != digest:
            raise FailClosedError(f"immutable MLflow key already differs: {key}")
        return "EXISTING_IDENTICAL"


def apply_attachment(snapshot: Path, report_path: Path, plan: dict) -> dict:
    client = plan.pop("client")
    s3 = _s3_client()
    report_body = report_path.read_bytes()
    report_version = _put_create_or_same(
        s3, plan["artifact_key"], report_body, "application/json",
        "medzen-gate=BLOCKED&medzen-content-addressed=true&medzen-model-registration=false")
    report_uri = f"s3://{BUCKET}/{plan['artifact_key']}"
    client.set_tag(plan["run_id"], "b5.gate_report_uri", report_uri)
    client.set_tag(plan["run_id"], "b5.gate_report_sha256",
                   plan["report_sha256"])
    client.set_tag(plan["run_id"], "b5.gate_outcome", "BLOCKED")
    client.set_tag(plan["run_id"], "b5.model_registration_permitted", "false")
    if list(client.search_registered_models()) or list(client.search_model_versions()):
        raise FailClosedError("MLflow registry changed during report attachment")
    with tempfile.TemporaryDirectory() as directory:
        new_snapshot = Path(directory) / "mlflow.db"
        consistent_snapshot(snapshot, new_snapshot)
        snapshot_body = new_snapshot.read_bytes()
    snapshot_version = _put_create_or_same(
        s3, plan["snapshot_key"], snapshot_body, "application/x-sqlite3",
        "medzen-gate=BLOCKED&medzen-content-addressed=true&medzen-model-registration=false")
    return {
        "status": "ATTACHED_BLOCKED_REPORT",
        "run_id": plan["run_id"],
        "report_uri": report_uri,
        "report_sha256": plan["report_sha256"],
        "report_version_id": report_version,
        "snapshot_uri": f"s3://{BUCKET}/{plan['snapshot_key']}",
        "snapshot_sha256": hashlib.sha256(snapshot_body).hexdigest(),
        "snapshot_version_id": snapshot_version,
        "registered_models_after": 0,
        "model_versions_after": 0,
        "approved_asr_writes": 0,
        "ssm_writes": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--resolution", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--aws-approval", type=Path)
    args = parser.parse_args()
    plan = _validate(args.snapshot, args.report, args.resolution)
    printable = {key: value for key, value in plan.items()
                 if key not in {"client", "report"}}
    printable.update({"mode": "APPLY" if args.apply else "DRY_RUN",
                      "writes_performed": 0})
    if not args.apply:
        print(json.dumps(printable, indent=2, sort_keys=True))
        return 0
    if args.aws_approval is None:
        raise FailClosedError("--apply requires --aws-approval")
    _approval(args.aws_approval)
    result = apply_attachment(args.snapshot, args.report, plan)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
