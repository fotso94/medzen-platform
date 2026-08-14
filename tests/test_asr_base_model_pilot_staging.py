from __future__ import annotations

import base64
import hashlib
import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.asr_base_model_pilot_staging import (
    ManagedMultipartCreateOnlyStore,
    StagingRefusal,
    validate_prestage_proof,
    validate_window_budget,
)


class MissingObject(Exception):
    def __init__(self):
        self.response = {
            "Error": {"Code": "404"},
            "ResponseMetadata": {"HTTPStatusCode": 404},
        }


class Body:
    def __init__(self, value: bytes):
        self.stream = io.BytesIO(value)

    def read(self, size: int = -1) -> bytes:
        return self.stream.read(size)


class FakeMultipartS3:
    def __init__(self, *, fail_first_part_once: bool = False, clock=None):
        self.fail_first_part_once = fail_first_part_once
        self.clock = clock
        self.failed = False
        self.uploads: dict[str, dict[str, Any]] = {}
        self.objects: dict[tuple[str, str], dict[str, Any]] = {}
        self.aborted = 0

    def head_object(self, **request: Any) -> dict[str, Any]:
        value = self.objects.get((request["Bucket"], request["Key"]))
        if value is None:
            raise MissingObject()
        if "VersionId" in request and value["VersionId"] != request["VersionId"]:
            raise MissingObject()
        return {key: value[key] for key in (
            "ContentLength", "Metadata", "ChecksumSHA256", "ChecksumType",
            "ServerSideEncryption", "VersionId",
        )}

    def get_object(self, **request: Any) -> dict[str, Any]:
        value = self.objects[(request["Bucket"], request["Key"])]
        return {"Body": Body(value["Body"])}

    def create_multipart_upload(self, **request: Any) -> dict[str, Any]:
        upload_id = f"upload-{len(self.uploads) + 1}"
        self.uploads[upload_id] = {"request": request, "parts": {}}
        return {"UploadId": upload_id}

    def upload_part(self, **request: Any) -> dict[str, Any]:
        if self.clock is not None:
            self.clock.value += self.clock.upload_seconds
        if self.fail_first_part_once and request["PartNumber"] == 1 and not self.failed:
            self.failed = True
            raise OSError("synthetic transient failure")
        body = bytes(request["Body"])
        checksum = base64.b64encode(hashlib.sha256(body).digest()).decode()
        assert request["ChecksumSHA256"] == checksum
        self.uploads[request["UploadId"]]["parts"][request["PartNumber"]] = body
        return {"ETag": f'"part-{request["PartNumber"]}"', "ChecksumSHA256": checksum}

    def complete_multipart_upload(self, **request: Any) -> dict[str, Any]:
        upload = self.uploads[request["UploadId"]]
        parts = upload["parts"]
        body = b"".join(parts[number] for number in sorted(parts))
        part_digests = b"".join(hashlib.sha256(parts[number]).digest() for number in sorted(parts))
        checksum = base64.b64encode(hashlib.sha256(part_digests).digest()).decode() + f"-{len(parts)}"
        key = (request["Bucket"], request["Key"])
        if request.get("IfNoneMatch") == "*" and key in self.objects:
            raise RuntimeError("precondition failed")
        version = f"version-{len(self.objects) + 1}"
        self.objects[key] = {
            "Body": body,
            "ContentLength": len(body),
            "Metadata": upload["request"]["Metadata"],
            "ChecksumSHA256": checksum,
            "ChecksumType": "COMPOSITE",
            "ServerSideEncryption": "aws:kms",
            "VersionId": version,
        }
        return {"VersionId": version}

    def abort_multipart_upload(self, **request: Any) -> None:
        self.aborted += 1


class Clock:
    def __init__(self, *, upload_seconds: float = 0):
        self.value = 0.0
        self.upload_seconds = upload_seconds

    def __call__(self) -> float:
        return self.value


def proof(*, remaining: int = 0, measured: float = 10_000_000) -> dict[str, Any]:
    digest = "1" * 64
    item = {
        "key": "research/asr-base-model/pilot/" + "2" * 64 + "/pilot-bundle.json",
        "sha256": digest,
        "bytes": 10,
        "version_id": "version-1",
        "s3_checksum_sha256": base64.b64encode(bytes.fromhex(digest)).decode(),
        "checksum_type": "FULL_OBJECT",
    }
    return {
        "schema_version": 1,
        "status": "PASS_COMPLETE_MODEL_BUNDLE_PRESTAGED",
        "classification": "PUBLIC_RESEARCH_NO_PHI",
        "pilot_bundle": {"identity_sha256": "2" * 64, "object": item},
        "objects": [item],
        "object_count": 1,
        "object_bytes": 10,
        "transfer": {"measured_uplink_bits_per_second": measured},
        "timed_window": {
            "artifact_stage_mode": "VERIFY_ONLY",
            "in_attempt_upload_bytes": remaining,
            "estimated_fast_stage_seconds": 7200,
            "deadline_seconds": 10800,
        },
    }


def test_multipart_create_only_retries_parts_and_persists_byte_progress(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"a" * (11 * 1024 * 1024))
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    fake = FakeMultipartS3(fail_first_part_once=True)
    store = ManagedMultipartCreateOnlyStore(
        fake,
        "arn:aws:kms:eu-central-1:558069890522:key/test",
        tmp_path / "progress.json",
        part_bytes=5 * 1024 * 1024,
        sleeper=lambda _: None,
    )

    version = store.upload_create_only(source, "medzen-speech", "research/test", digest)

    assert version == "version-1"
    assert store.part_retries == 1
    assert store.uploaded_bytes == source.stat().st_size
    assert fake.aborted == 0
    heartbeat = json.loads((tmp_path / "progress.json").read_bytes())
    assert heartbeat["status"] == "OBJECT_COMPLETE"
    assert heartbeat["completed_bytes"] == source.stat().st_size
    assert fake.objects[("medzen-speech", "research/test")]["Body"] == source.read_bytes()


def test_existing_exact_object_is_reused_without_mutation(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"immutable")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    fake = FakeMultipartS3()
    store = ManagedMultipartCreateOnlyStore(
        fake,
        "arn:aws:kms:eu-central-1:558069890522:key/test",
        tmp_path / "progress.json",
        part_bytes=5 * 1024 * 1024,
        sleeper=lambda _: None,
    )
    version = store.upload_create_only(source, "medzen-speech", "research/test", digest)
    second = store.upload_create_only(source, "medzen-speech", "research/test", digest)
    assert second == version
    assert store.uploaded_objects == 1
    assert store.reused_objects == 1
    assert len(fake.objects) == 1


def test_zero_progress_watchdog_aborts_the_upload(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"a" * (5 * 1024 * 1024))
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    clock = Clock(upload_seconds=11)
    fake = FakeMultipartS3(clock=clock)
    store = ManagedMultipartCreateOnlyStore(
        fake,
        "arn:aws:kms:eu-central-1:558069890522:key/test",
        tmp_path / "progress.json",
        part_bytes=5 * 1024 * 1024,
        zero_progress_seconds=10,
        clock=clock,
        sleeper=lambda _: None,
    )
    with pytest.raises(StagingRefusal) as refused:
        store.upload_create_only(source, "medzen-speech", "research/test", digest)
    assert refused.value.reason_code == "MULTIPART_PART_RETRIES_EXHAUSTED"
    assert fake.aborted == 1
    assert json.loads((tmp_path / "progress.json").read_bytes())["status"] == "UPLOAD_REFUSED"


def test_watchdog_resets_at_each_object_boundary(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"a" * (5 * 1024 * 1024))
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    clock = Clock(upload_seconds=1)
    clock.value = 1_000
    fake = FakeMultipartS3(clock=clock)
    store = ManagedMultipartCreateOnlyStore(
        fake,
        "arn:aws:kms:eu-central-1:558069890522:key/test",
        tmp_path / "progress.json",
        part_bytes=5 * 1024 * 1024,
        zero_progress_seconds=10,
        clock=clock,
        sleeper=lambda _: None,
    )
    # Simulate a long model/audio preparation interval between objects.
    clock.value += 300
    assert store.upload_create_only(source, "medzen-speech", "research/test", digest) == "version-1"


def test_prestage_proof_requires_zero_in_attempt_upload_bytes() -> None:
    assert validate_prestage_proof(proof(), expected_bundle_sha256="2" * 64)["status"] == "PASS_PRESTAGE_PROOF_STRUCTURE"
    with pytest.raises(StagingRefusal) as refused:
        validate_prestage_proof(proof(remaining=1), expected_bundle_sha256="2" * 64)
    assert refused.value.reason_code == "PRESTAGE_PROOF_MALFORMED"


def test_uplink_budget_refuses_an_unwinnable_window() -> None:
    with pytest.raises(StagingRefusal) as refused:
        validate_window_budget(
            proof(remaining=13_021_689_920, measured=10_000_000),
            deadline_seconds=10800,
            expected_bundle_sha256="2" * 64,
        )
    assert refused.value.reason_code == "WINDOW_BUDGET_INFEASIBLE"


def test_window_budget_refuses_when_fast_stages_leave_no_cleanup_reserve() -> None:
    value = proof()
    value["timed_window"]["estimated_fast_stage_seconds"] = 10_000
    with pytest.raises(StagingRefusal) as refused:
        validate_window_budget(
            value,
            deadline_seconds=10800,
            expected_bundle_sha256="2" * 64,
        )
    assert refused.value.reason_code == "WINDOW_BUDGET_INFEASIBLE"


def test_attempt_nine_plan_and_executor_have_no_artifact_upload_path() -> None:
    live = (ROOT / "scripts/asr_base_model_pilot_live.py").read_text(encoding="utf-8")
    artifact = live[
        live.index("    def artifact_stage(") : live.index(
            "    def _endpoint_call_inventory("
        )
    ]
    assert "stage_assets(" not in artifact
    assert "upload_create_only(" not in artifact
    assert "verify_prestaged_bundle(" in artifact
