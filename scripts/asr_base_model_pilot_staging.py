#!/usr/bin/env python3
"""Pre-stage and verify the immutable ASR pilot bundle outside a timed attempt.

The transfer implementation deliberately uses S3's multipart API directly.
Each part has an independent SHA-256, bounded retry, and a durable progress
heartbeat in an external workdir. Completion is create-only and the resulting
full-object SHA-256 is verified through S3's stored checksum before a version
is admitted to the proof.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from pipeline.asr_base_model_pilot_receipts import canonical_json, write_exclusive
from scripts.asr_base_model_pilot_assets import (
    AssetRefusal,
    pilot_bundle_identity,
    sha256_file,
    stage_assets,
)


BUCKET = "medzen-speech"
PROFILE = "medzen"
REGION = "eu-central-1"
PART_BYTES = 64 * 1024 * 1024
MAX_PART_ATTEMPTS = 3
ZERO_PROGRESS_SECONDS = 240
MIN_WINDOW_RESERVE_SECONDS = 900


class StagingRefusal(RuntimeError):
    def __init__(self, reason_code: str, detail: str):
        super().__init__(detail)
        self.reason_code = reason_code
        self.detail = detail


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _checksum_b64(digest: str) -> str:
    return base64.b64encode(bytes.fromhex(digest)).decode()


def _atomic_replace(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{os.getpid()}.tmp"
    with temporary.open("wb") as stream:
        stream.write(canonical_json(value))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def validate_window_budget(
    proof: dict[str, Any], *, deadline_seconds: int, expected_bundle_sha256: str
) -> dict[str, Any]:
    """Refuse a timed window that still contains bulk artifact transfer."""
    transfer = proof.get("transfer", {})
    measured = transfer.get("measured_uplink_bits_per_second")
    budget = proof.get("timed_window", {})
    remaining_upload_bytes = budget.get("in_attempt_upload_bytes")
    if (
        proof.get("status") != "PASS_COMPLETE_MODEL_BUNDLE_PRESTAGED"
        or proof.get("pilot_bundle", {}).get("identity_sha256") != expected_bundle_sha256
        or isinstance(measured, bool)
        or not isinstance(measured, (int, float))
        or measured <= 0
        or isinstance(remaining_upload_bytes, bool)
        or not isinstance(remaining_upload_bytes, int)
        or remaining_upload_bytes < 0
        or budget.get("artifact_stage_mode") != "VERIFY_ONLY"
    ):
        raise StagingRefusal(
            "WINDOW_BUDGET_INPUT_DIFFERS",
            "pre-staging proof or measured uplink binding differs",
        )
    estimated = budget.get("estimated_fast_stage_seconds")
    required_upload_seconds = int((remaining_upload_bytes * 8 + measured - 1) // measured)
    if (
        isinstance(estimated, bool)
        or not isinstance(estimated, int)
        or estimated <= 0
        or estimated + required_upload_seconds + MIN_WINDOW_RESERVE_SECONDS
        > deadline_seconds
    ):
        raise StagingRefusal(
            "WINDOW_BUDGET_INFEASIBLE",
            "fast-stage estimate plus cleanup reserve exceeds the immutable window",
        )
    return {
        "status": "PASS_UPLINK_AND_WINDOW_BUDGET_PREFLIGHT",
        "measured_uplink_bits_per_second": round(float(measured), 2),
        "in_attempt_upload_bytes": remaining_upload_bytes,
        "required_upload_seconds_at_measured_uplink": required_upload_seconds,
        "estimated_fast_stage_seconds": estimated,
        "cleanup_reserve_seconds": MIN_WINDOW_RESERVE_SECONDS,
        "deadline_seconds": deadline_seconds,
    }


def validate_prestage_proof(
    proof: dict[str, Any], *, expected_bundle_sha256: str
) -> dict[str, Any]:
    objects = proof.get("objects")
    bundle = proof.get("pilot_bundle", {})
    if (
        proof.get("schema_version") != 1
        or proof.get("status") != "PASS_COMPLETE_MODEL_BUNDLE_PRESTAGED"
        or proof.get("classification") != "PUBLIC_RESEARCH_NO_PHI"
        or bundle.get("identity_sha256") != expected_bundle_sha256
        or not isinstance(objects, list)
        or not objects
        or proof.get("timed_window", {}).get("in_attempt_upload_bytes") != 0
    ):
        raise StagingRefusal("PRESTAGE_PROOF_MALFORMED", "pre-stage proof identity differs")
    keys: set[str] = set()
    total = 0
    for item in objects:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("key"), str)
            or item["key"] in keys
            or not isinstance(item.get("version_id"), str)
            or not item["version_id"]
            or not isinstance(item.get("bytes"), int)
            or item["bytes"] < 0
            or not isinstance(item.get("sha256"), str)
            or len(item["sha256"]) != 64
            or item.get("checksum_type") not in {"FULL_OBJECT", "COMPOSITE"}
            or not isinstance(item.get("s3_checksum_sha256"), str)
            or not item["s3_checksum_sha256"]
        ):
            raise StagingRefusal("PRESTAGE_OBJECT_BINDING_MALFORMED", "pre-stage object binding differs")
        keys.add(item["key"])
        total += item["bytes"]
    if total != proof.get("object_bytes") or len(objects) != proof.get("object_count"):
        raise StagingRefusal("PRESTAGE_OBJECT_TOTAL_DIFFERS", "pre-stage object totals differ")
    validate_window_budget(
        proof,
        deadline_seconds=int(proof["timed_window"]["deadline_seconds"]),
        expected_bundle_sha256=expected_bundle_sha256,
    )
    return {
        "status": "PASS_PRESTAGE_PROOF_STRUCTURE",
        "object_count": len(objects),
        "object_bytes": total,
        "bundle_identity_sha256": expected_bundle_sha256,
    }


class ManagedMultipartCreateOnlyStore:
    def __init__(
        self,
        s3: Any,
        kms_key_arn: str,
        heartbeat_path: Path,
        *,
        part_bytes: int = PART_BYTES,
        maximum_part_attempts: int = MAX_PART_ATTEMPTS,
        zero_progress_seconds: int = ZERO_PROGRESS_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        if part_bytes < 5 * 1024 * 1024:
            raise StagingRefusal("MULTIPART_PART_SIZE_INVALID", "multipart part size is below S3's minimum")
        self.s3 = s3
        self.kms_key_arn = kms_key_arn
        self.heartbeat_path = heartbeat_path
        self.part_bytes = part_bytes
        self.maximum_part_attempts = maximum_part_attempts
        self.zero_progress_seconds = zero_progress_seconds
        self.clock = clock
        self.sleeper = sleeper
        self.started = clock()
        self.last_progress = self.started
        self.completed_bytes = 0
        self.uploaded_bytes = 0
        self.reused_bytes = 0
        self.uploaded_objects = 0
        self.reused_objects = 0
        self.part_retries = 0
        self.verified_bytes = 0

    def download(self, bucket: str, key: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as stream:
            self.s3.download_fileobj(bucket, key, stream)

    def _heartbeat(
        self,
        *,
        status: str,
        bucket: str,
        key: str,
        object_bytes: int,
        part_number: int | None = None,
        part_bytes: int = 0,
        attempt: int | None = None,
        object_completed_bytes: int | None = None,
    ) -> None:
        now = self.clock()
        if status in {
            "PART_COMPLETE",
            "OBJECT_REUSED",
            "OBJECT_COMPLETE",
            "READBACK_PROGRESS",
            "READBACK_COMPLETE",
        }:
            self.last_progress = now
        _atomic_replace(
            self.heartbeat_path,
            {
                "schema_version": 1,
                "status": status,
                "recorded_utc": _utc(),
                "bucket": bucket,
                "key": key,
                "object_bytes": object_bytes,
                "part_number": part_number,
                "part_bytes": part_bytes,
                "part_attempt": attempt,
                "object_completed_bytes": object_completed_bytes,
                "completed_bytes": self.completed_bytes,
                "verified_bytes": self.verified_bytes,
                "seconds_since_byte_progress": round(now - self.last_progress, 3),
                "zero_progress_watchdog_seconds": self.zero_progress_seconds,
            },
        )

    def _assert_progress_bound(self) -> None:
        if self.clock() - self.last_progress > self.zero_progress_seconds:
            raise StagingRefusal(
                "MULTIPART_ZERO_PROGRESS_WATCHDOG",
                "multipart transfer exceeded the bounded zero-progress interval",
            )

    def _head(self, bucket: str, key: str, *, version_id: str | None = None) -> dict[str, Any] | None:
        request: dict[str, Any] = {"Bucket": bucket, "Key": key, "ChecksumMode": "ENABLED"}
        if version_id is not None:
            request["VersionId"] = version_id
        try:
            return self.s3.head_object(**request)
        except Exception as exc:
            response = getattr(exc, "response", {})
            code = str(response.get("Error", {}).get("Code", ""))
            status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if code in {"404", "NoSuchKey", "NotFound"} or status == 404:
                return None
            raise

    def _exact_existing(
        self,
        bucket: str,
        key: str,
        digest: str,
        size: int,
        *,
        version_id: str | None = None,
        checksum_sha256: str | None = None,
        checksum_type: str | None = None,
    ) -> dict[str, Any] | None:
        value = self._head(bucket, key, version_id=version_id)
        if value is None:
            return None
        if (
            value.get("ContentLength") != size
            or value.get("Metadata", {}).get("sha256") != digest
            or not isinstance(value.get("ChecksumSHA256"), str)
            or value.get("ChecksumType") not in {"FULL_OBJECT", "COMPOSITE"}
            or value.get("ServerSideEncryption") != "aws:kms"
            or not isinstance(value.get("VersionId"), str)
        ):
            raise StagingRefusal(
                "CREATE_ONLY_OBJECT_OCCUPIED_DIFFERENTLY",
                "an immutable staging key is occupied by different bytes or metadata",
            )
        if checksum_sha256 is not None and value["ChecksumSHA256"] != checksum_sha256:
            raise StagingRefusal(
                "PRESTAGED_OBJECT_CHECKSUM_DIFFERS",
                "the bound S3 checksum differs",
            )
        if checksum_type is not None and value["ChecksumType"] != checksum_type:
            raise StagingRefusal(
                "PRESTAGED_OBJECT_CHECKSUM_TYPE_DIFFERS",
                "the bound S3 checksum type differs",
            )
        return value

    def _verify_full_bytes(
        self, bucket: str, key: str, version_id: str, digest: str, size: int
    ) -> None:
        response = self.s3.get_object(Bucket=bucket, Key=key, VersionId=version_id)
        measured = hashlib.sha256()
        measured_bytes = 0
        self._heartbeat(
            status="READBACK_STARTED",
            bucket=bucket,
            key=key,
            object_bytes=size,
        )
        for block in iter(lambda: response["Body"].read(8 * 1024 * 1024), b""):
            measured.update(block)
            measured_bytes += len(block)
            self.verified_bytes += len(block)
            self._heartbeat(
                status="READBACK_PROGRESS",
                bucket=bucket,
                key=key,
                object_bytes=size,
                part_bytes=len(block),
                object_completed_bytes=measured_bytes,
            )
            self._assert_progress_bound()
        if measured.hexdigest() != digest or measured_bytes != size:
            raise StagingRefusal(
                "PRESTAGED_OBJECT_FULL_READBACK_DIFFERS",
                "full-object readback differs from the local SHA-256",
            )
        self._heartbeat(
            status="READBACK_COMPLETE",
            bucket=bucket,
            key=key,
            object_bytes=size,
        )

    def upload_create_only(self, source: Path, bucket: str, key: str, sha256: str) -> str:
        digest, size = sha256_file(source)
        if digest != sha256 or size <= 0:
            raise AssetRefusal("multipart source size or digest differs")
        existing = self._exact_existing(bucket, key, digest, size)
        if existing is not None:
            self._verify_full_bytes(bucket, key, existing["VersionId"], digest, size)
            self.reused_objects += 1
            self.reused_bytes += size
            self.completed_bytes += size
            self._heartbeat(status="OBJECT_REUSED", bucket=bucket, key=key, object_bytes=size)
            return existing["VersionId"]

        initiated = self.s3.create_multipart_upload(
            Bucket=bucket,
            Key=key,
            ChecksumAlgorithm="SHA256",
            ChecksumType="COMPOSITE",
            ServerSideEncryption="aws:kms",
            SSEKMSKeyId=self.kms_key_arn,
            Metadata={"sha256": digest, "classification": "offline-evaluation-only"},
        )
        upload_id = initiated["UploadId"]
        completed_parts: list[dict[str, Any]] = []
        self._heartbeat(status="UPLOAD_STARTED", bucket=bucket, key=key, object_bytes=size)
        try:
            with source.open("rb") as stream:
                part_number = 1
                offset = 0
                while offset < size:
                    self._assert_progress_bound()
                    block = stream.read(min(self.part_bytes, size - offset))
                    if not block:
                        raise StagingRefusal("MULTIPART_SOURCE_TRUNCATED", "multipart source ended early")
                    checksum = base64.b64encode(hashlib.sha256(block).digest()).decode()
                    last_error: Exception | None = None
                    for attempt in range(1, self.maximum_part_attempts + 1):
                        self._heartbeat(
                            status="PART_ATTEMPT",
                            bucket=bucket,
                            key=key,
                            object_bytes=size,
                            part_number=part_number,
                            part_bytes=len(block),
                            attempt=attempt,
                        )
                        started = self.clock()
                        try:
                            response = self.s3.upload_part(
                                Bucket=bucket,
                                Key=key,
                                UploadId=upload_id,
                                PartNumber=part_number,
                                Body=block,
                                ContentLength=len(block),
                                ChecksumSHA256=checksum,
                            )
                            if self.clock() - started > self.zero_progress_seconds:
                                raise StagingRefusal(
                                    "MULTIPART_ZERO_PROGRESS_WATCHDOG",
                                    "a multipart part completed beyond the zero-progress bound",
                                )
                            if response.get("ChecksumSHA256") != checksum:
                                raise StagingRefusal(
                                    "MULTIPART_PART_CHECKSUM_DIFFERS",
                                    "S3 part checksum differs from the local bytes",
                                )
                            completed_parts.append(
                                {
                                    "PartNumber": part_number,
                                    "ETag": response["ETag"],
                                    "ChecksumSHA256": checksum,
                                }
                            )
                            offset += len(block)
                            self.completed_bytes += len(block)
                            self.uploaded_bytes += len(block)
                            self._heartbeat(
                                status="PART_COMPLETE",
                                bucket=bucket,
                                key=key,
                                object_bytes=size,
                                part_number=part_number,
                                part_bytes=len(block),
                                attempt=attempt,
                            )
                            break
                        except Exception as exc:
                            last_error = exc
                            if attempt == self.maximum_part_attempts:
                                raise StagingRefusal(
                                    "MULTIPART_PART_RETRIES_EXHAUSTED",
                                    f"part {part_number} exhausted its bounded retries",
                                ) from exc
                            self.part_retries += 1
                            self.sleeper(min(2 ** (attempt - 1), 4))
                    if last_error is not None and len(completed_parts) != part_number:
                        raise StagingRefusal("MULTIPART_PART_NOT_RECORDED", "multipart part was not recorded")
                    part_number += 1
            response = self.s3.complete_multipart_upload(
                Bucket=bucket,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={"Parts": completed_parts},
                ChecksumType="COMPOSITE",
                IfNoneMatch="*",
            )
            version = response.get("VersionId")
            exact = self._exact_existing(bucket, key, digest, size, version_id=version)
            if exact is None:
                raise StagingRefusal("MULTIPART_COMPLETION_READBACK_ABSENT", "completed object is absent")
            self._verify_full_bytes(bucket, key, exact["VersionId"], digest, size)
            self.uploaded_objects += 1
            self._heartbeat(status="OBJECT_COMPLETE", bucket=bucket, key=key, object_bytes=size)
            return exact["VersionId"]
        except Exception:
            try:
                self.s3.abort_multipart_upload(
                    Bucket=bucket, Key=key, UploadId=upload_id
                )
            finally:
                self._heartbeat(status="UPLOAD_REFUSED", bucket=bucket, key=key, object_bytes=size)
            raise

    def verify_bound_object(self, bucket: str, item: dict[str, Any]) -> dict[str, Any]:
        value = self._exact_existing(
            bucket,
            item["key"],
            item["sha256"],
            item["bytes"],
            version_id=item["version_id"],
            checksum_sha256=item["s3_checksum_sha256"],
            checksum_type=item["checksum_type"],
        )
        if value is None:
            raise StagingRefusal("PRESTAGED_OBJECT_ABSENT", "a bound pre-staged object is absent")
        return {
            "key": item["key"],
            "sha256": item["sha256"],
            "bytes": item["bytes"],
            "version_id": value["VersionId"],
            "s3_checksum_sha256": value["ChecksumSHA256"],
            "checksum_type": value["ChecksumType"],
        }

    def summary(self) -> dict[str, Any]:
        elapsed = max(self.clock() - self.started, 0.001)
        measured = (self.uploaded_bytes * 8) / elapsed if self.uploaded_bytes else 0.0
        return {
            "method": "S3_MANAGED_MULTIPART_DIRECT_API",
            "part_bytes": self.part_bytes,
            "maximum_part_attempts": self.maximum_part_attempts,
            "zero_progress_watchdog_seconds": self.zero_progress_seconds,
            "uploaded_objects": self.uploaded_objects,
            "uploaded_bytes": self.uploaded_bytes,
            "reused_objects": self.reused_objects,
            "reused_bytes": self.reused_bytes,
            "part_retries": self.part_retries,
            "verified_bytes": self.verified_bytes,
            "elapsed_seconds": round(elapsed, 3),
            "measured_uplink_bits_per_second": round(measured, 2),
            "heartbeat_path_external": True,
        }


def verify_prestaged_bundle(
    s3: Any,
    proof: dict[str, Any],
    *,
    expected_bundle_sha256: str,
    destination: Path,
) -> dict[str, Any]:
    validate_prestage_proof(proof, expected_bundle_sha256=expected_bundle_sha256)
    store = ManagedMultipartCreateOnlyStore(
        s3,
        proof["kms_key_arn"],
        destination.parent / "verify-heartbeat.json",
    )
    verified = [store.verify_bound_object(BUCKET, item) for item in proof["objects"]]
    bundle_object = proof["pilot_bundle"]["object"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb") as stream:
        s3.download_fileobj(
            BUCKET,
            bundle_object["key"],
            stream,
            ExtraArgs={"VersionId": bundle_object["version_id"]},
        )
    digest, size = sha256_file(destination)
    if digest != bundle_object["sha256"] or size != bundle_object["bytes"]:
        raise StagingRefusal("PRESTAGED_BUNDLE_READBACK_DIFFERS", "pilot bundle readback differs")
    bundle = json.loads(destination.read_bytes())
    if (
        bundle.get("bundle_identity", {}).get("sha256") != expected_bundle_sha256
        or bundle.get("receipt_sha256") != proof["pilot_bundle"]["receipt_sha256"]
        or bundle.get("objects")
        != [
            {key: value for key, value in item.items() if key not in {"s3_checksum_sha256", "checksum_type"}}
            for item in proof["objects"]
            if item["key"] != bundle_object["key"]
        ]
    ):
        raise StagingRefusal("PRESTAGED_BUNDLE_CONTENT_DIFFERS", "pilot bundle content differs from proof")
    return {
        "status": "PASS_PRESTAGED_BUNDLE_VERIFY_ONLY",
        "object_count": len(verified),
        "object_bytes": sum(item["bytes"] for item in verified),
        "bundle_identity_sha256": expected_bundle_sha256,
        "bundle_object_sha256": digest,
        "aws_mutations": 0,
        "artifact_upload_bytes": 0,
    }


def prestage(
    *,
    selection_path: Path,
    workdir: Path,
    output: Path,
    expected_bundle_sha256: str,
    kms_key_arn: str,
    model_cache: Path | None,
) -> dict[str, Any]:
    if workdir.exists():
        raise StagingRefusal("PRESTAGE_WORKDIR_EXISTS", "pre-stage workdir must be fresh")
    workdir.mkdir(parents=True)
    selection = json.loads(selection_path.read_bytes())
    identity = pilot_bundle_identity(selection["public_row_list_sha256"])
    if identity["sha256"] != expected_bundle_sha256:
        raise StagingRefusal("PRESTAGE_SELECTION_BINDING_DIFFERS", "selection and bundle identity differ")
    try:
        import boto3
        from botocore.config import Config
    except Exception as exc:
        raise StagingRefusal("PRESTAGE_AWS_SDK_ABSENT", "the pinned AWS SDK is unavailable") from exc
    session = boto3.Session(profile_name=PROFILE, region_name=REGION)
    s3 = session.client(
        "s3",
        config=Config(
            connect_timeout=10,
            read_timeout=ZERO_PROGRESS_SECONDS,
            retries={"mode": "standard", "total_max_attempts": 1},
        ),
    )
    prefix = f"research/asr-base-model/pilot/{expected_bundle_sha256}/"
    store = ManagedMultipartCreateOnlyStore(
        s3,
        kms_key_arn,
        workdir / "progress" / "heartbeat.json",
    )
    bundle = stage_assets(
        selection,
        store,
        workdir / "assets",
        prefix,
        model_cache=model_cache,
    )
    if bundle["bundle_identity"]["sha256"] != expected_bundle_sha256:
        raise StagingRefusal("PRESTAGED_BUNDLE_IDENTITY_DIFFERS", "staged bundle identity differs")
    bundle_path = workdir / "pilot-bundle.json"
    write_exclusive(bundle_path, canonical_json(bundle))
    bundle_digest, bundle_bytes = sha256_file(bundle_path)
    bundle_key = prefix + "pilot-bundle.json"
    bundle_version = store.upload_create_only(
        bundle_path, BUCKET, bundle_key, bundle_digest
    )
    all_objects = []
    for item in [*bundle["objects"], {
        "key": bundle_key,
        "sha256": bundle_digest,
        "bytes": bundle_bytes,
        "version_id": bundle_version,
    }]:
        observed = store._exact_existing(
            BUCKET,
            item["key"],
            item["sha256"],
            item["bytes"],
            version_id=item["version_id"],
        )
        if observed is None:
            raise StagingRefusal("PRESTAGED_OBJECT_ABSENT", "a completed object is absent")
        all_objects.append({
            **item,
            "s3_checksum_sha256": observed["ChecksumSHA256"],
            "checksum_type": observed["ChecksumType"],
        })
    all_objects.sort(key=lambda item: item["key"])
    transfer = store.summary()
    if transfer["uploaded_bytes"] <= 0:
        raise StagingRefusal("UPLINK_MEASUREMENT_ABSENT", "pre-stage did not measure an actual upload")
    proof = {
        "schema_version": 1,
        "record": "ASR_BASE_MODEL_COMPLETE_BUNDLE_PRESTAGE_PROOF",
        "id": "ASR-BASE-MODEL-PRESTAGE-PROOF-2026-001",
        "status": "PASS_COMPLETE_MODEL_BUNDLE_PRESTAGED",
        "recorded_utc": _utc(),
        "classification": "PUBLIC_RESEARCH_NO_PHI",
        "source": {
            "attempt_8_refusal_sha256": _sha(
                Path("platform/evidence/ASR-BASE-MODEL-PACKET-2026-002G-ATTEMPT-8-ARTIFACT-STAGING-STALL-REFUSAL.json")
            ),
            "selection_sha256": selection["public_row_list_sha256"],
            "staging_module_sha256": _sha(Path(__file__)),
        },
        "bucket": BUCKET,
        "kms_key_arn": kms_key_arn,
        "prefix": f"s3://{BUCKET}/{prefix}",
        "pilot_bundle": {
            "identity_sha256": expected_bundle_sha256,
            "receipt_sha256": bundle["receipt_sha256"],
            "object": next(item for item in all_objects if item["key"] == bundle_key),
        },
        "objects": all_objects,
        "object_count": len(all_objects),
        "object_bytes": sum(item["bytes"] for item in all_objects),
        "transfer": transfer,
        "timed_window": {
            "artifact_stage_mode": "VERIFY_ONLY",
            "in_attempt_upload_bytes": 0,
            "estimated_fast_stage_seconds": 7200,
            "cleanup_reserve_seconds": MIN_WINDOW_RESERVE_SECONDS,
            "deadline_seconds": 10800,
        },
        "scope": {
            "aws_mutations": ["create-only S3 objects under the exact research prefix"],
            "endpoints_created": 0,
            "gpu_started": False,
            "production_touched": False,
            "approved_asr_touched": False,
        },
    }
    validate_prestage_proof(proof, expected_bundle_sha256=expected_bundle_sha256)
    write_exclusive(output, canonical_json(proof))
    return {**proof, "sha256": _sha(output)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-bundle-sha256", required=True)
    parser.add_argument("--kms-key-arn", required=True)
    parser.add_argument("--model-cache", type=Path)
    args = parser.parse_args()
    try:
        value = prestage(
            selection_path=args.selection,
            workdir=args.workdir,
            output=args.output,
            expected_bundle_sha256=args.expected_bundle_sha256,
            kms_key_arn=args.kms_key_arn,
            model_cache=args.model_cache,
        )
    except Exception as exc:
        print(json.dumps({"status": "REFUSED", "reason_code": getattr(exc, "reason_code", type(exc).__name__)}, sort_keys=True))
        return 2
    print(json.dumps(value, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
