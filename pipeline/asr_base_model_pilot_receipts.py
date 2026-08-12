"""Write-once stage receipts for the ASR base-model pilot successor."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "MEDZEN_ASR_BASE_MODEL_PILOT_STAGE_V1"
STAGES = (
    "deadline_identity_and_acceptance",
    "input_freeze_and_no_phi",
    "cost_and_zero_state",
    "image_publication_and_scan",
    "artifact_stage",
    "private_endpoint_and_policy_gate",
    "gpu_and_sampler_gate",
    "node_local_input_stage",
    "pilot_rows",
    "aggregate_report",
    "cleanup_and_expiry",
)
DEPENDENCIES = {
    STAGES[index]: (STAGES[index - 1],) for index in range(1, len(STAGES) - 1)
}
TERMINAL = {"PASS", "REFUSED", "INCOMPLETE_MEASUREMENT", "NOT_RUN"}


class PilotReceiptRefusal(RuntimeError):
    pass


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def write_exclusive(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise PilotReceiptRefusal(f"refusing to overwrite receipt: {path.name}")
    temporary = path.parent / f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
        temporary.unlink()
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


class ReceiptStore:
    def __init__(self, directory: Path, *, packet_sha256: str, authorization_sha256: str):
        self.directory = directory
        self.packet_sha256 = packet_sha256
        self.authorization_sha256 = authorization_sha256
        if any(len(value) != 64 or any(char not in "0123456789abcdef" for char in value)
               for value in (packet_sha256, authorization_sha256)):
            raise PilotReceiptRefusal("packet or authorization hash is malformed")

    def path(self, stage: str) -> Path:
        if stage not in STAGES:
            raise PilotReceiptRefusal(f"unknown stage: {stage}")
        return self.directory / f"{stage}.json"

    def load(self, stage: str) -> dict[str, Any]:
        try:
            value = json.loads(self.path(stage).read_bytes())
        except Exception as exc:
            raise PilotReceiptRefusal(f"{stage} receipt is absent or malformed") from exc
        if value.get("schema") != SCHEMA or value.get("stage") != stage or value.get("status") not in TERMINAL:
            raise PilotReceiptRefusal(f"{stage} receipt identity differs")
        if value.get("packet_sha256") != self.packet_sha256 or value.get("authorization_sha256") != self.authorization_sha256:
            raise PilotReceiptRefusal(f"{stage} receipt binding differs")
        return value

    def persist(self, stage: str, status: str, payload: dict[str, Any], *,
                dependencies: tuple[str, ...] | None = None) -> dict[str, Any]:
        if status not in TERMINAL:
            raise PilotReceiptRefusal("unknown receipt status")
        required = DEPENDENCIES.get(stage, ()) if dependencies is None else dependencies
        dependency_hashes = {}
        for dependency in required:
            value = self.load(dependency)
            if value["status"] != "PASS":
                raise PilotReceiptRefusal(f"{dependency} is not PASS")
            dependency_hashes[dependency] = sha256_file(self.path(dependency))
        receipt = {
            "schema": SCHEMA,
            "stage": stage,
            "status": status,
            "recorded_utc": utc_now(),
            "packet_sha256": self.packet_sha256,
            "authorization_sha256": self.authorization_sha256,
            "dependencies": dependency_hashes,
            "contains_credentials_phi_audio_reference_or_prediction": False,
            "payload": payload,
        }
        encoded = canonical_json(receipt)
        write_exclusive(self.path(stage), encoded)
        return {**receipt, "receipt_sha256": hashlib.sha256(encoded).hexdigest()}
