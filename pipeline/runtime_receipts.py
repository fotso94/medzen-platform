"""Immutable per-stage runtime receipts governed by the standing v1 policy."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "MEDZEN_RUNTIME_STAGE_RECEIPT_V1"
POLICY_PATH = "platform/runtime-receipt-policy-v1.yaml"
TERMINAL_STATUSES = {
    "PASS",
    "REFUSED",
    "INCOMPLETE_MEASUREMENT",
    "NOT_RUN",
}
STAGES = (
    "local_bindings",
    "deadline",
    "dra_stable_readiness",
    "sampler_self_test",
    "transcription",
    "gpu_memory_measurement",
    "proof_summary",
    "cleanup",
)
DEPENDENCIES = {
    "deadline": ("local_bindings",),
    "dra_stable_readiness": ("deadline",),
    "sampler_self_test": ("dra_stable_readiness",),
    "transcription": ("sampler_self_test",),
    "gpu_memory_measurement": ("transcription",),
    "proof_summary": ("transcription",),
    "cleanup": ("deadline",),
}
_SAFE_NAME = re.compile(r"[a-z][a-z0-9_]*")


class ReceiptRefusal(RuntimeError):
    """Raised when a receipt would be ambiguous, mutable, or out of order."""


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _atomic_exclusive_write(path: Path, encoded: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ReceiptRefusal(f"refusing to overwrite immutable receipt: {path.name}")
    temporary = path.parent / f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = None
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise ReceiptRefusal(
                f"refusing to overwrite immutable receipt: {path.name}"
            ) from exc
        os.unlink(temporary)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()


class ReceiptStore:
    """Persist and validate write-once stage receipts in one execution directory."""

    def __init__(self, directory: Path, *, policy_path: Path):
        if not policy_path.is_file():
            raise ReceiptRefusal("runtime receipt policy is absent")
        self.directory = directory
        self.policy_path = policy_path
        self.policy_sha256 = sha256_file(policy_path)

    def path(self, stage: str) -> Path:
        if stage not in STAGES or _SAFE_NAME.fullmatch(stage) is None:
            raise ReceiptRefusal(f"unknown runtime receipt stage: {stage}")
        return self.directory / f"{stage}.json"

    def load(self, stage: str) -> dict[str, Any]:
        path = self.path(stage)
        try:
            value = json.loads(path.read_bytes())
        except Exception as exc:
            raise ReceiptRefusal(f"{stage} receipt is absent or malformed") from exc
        if value.get("schema") != SCHEMA or value.get("stage") != stage:
            raise ReceiptRefusal(f"{stage} receipt identity differs")
        if value.get("status") not in TERMINAL_STATUSES:
            raise ReceiptRefusal(f"{stage} receipt status is unknown")
        policy = value.get("policy", {})
        if policy != {"path": POLICY_PATH, "sha256": self.policy_sha256}:
            raise ReceiptRefusal(f"{stage} receipt policy binding differs")
        return value

    def require_pass(self, stage: str) -> dict[str, Any]:
        value = self.load(stage)
        if value["status"] != "PASS":
            raise ReceiptRefusal(f"{stage} receipt is not PASS")
        return value

    def persist(
        self,
        stage: str,
        status: str,
        payload: dict[str, Any],
        *,
        dependencies: tuple[str, ...] | None = None,
        recorded_utc: str | None = None,
    ) -> dict[str, Any]:
        self.path(stage)
        if status not in TERMINAL_STATUSES:
            raise ReceiptRefusal(f"unknown runtime receipt status: {status}")
        required = DEPENDENCIES.get(stage, ()) if dependencies is None else dependencies
        dependency_hashes: dict[str, str] = {}
        for dependency in required:
            self.require_pass(dependency)
            dependency_hashes[dependency] = sha256_file(self.path(dependency))
        receipt = {
            "schema": SCHEMA,
            "stage": stage,
            "status": status,
            "recorded_utc": recorded_utc or utc_now(),
            "policy": {"path": POLICY_PATH, "sha256": self.policy_sha256},
            "dependencies": dependency_hashes,
            "contains_audio_transcript_logs_credentials_or_phi": False,
            "payload": payload,
        }
        encoded = canonical_json(receipt)
        _atomic_exclusive_write(self.path(stage), encoded)
        return {**receipt, "receipt_sha256": sha256_bytes(encoded)}
