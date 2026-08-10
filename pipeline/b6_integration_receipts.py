"""Write-once, PHI-safe stage receipts for the B6.6 integration window."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "MEDZEN_B6_INTEGRATION_STAGE_RECEIPT_V1"
STAGES = (
    "local_bindings",
    "deadline",
    "workers_ready",
    "terraform_window",
    "endpoints_ready",
    "controller_ready",
    "dra_ready",
    "rag_ready",
    "asr_ready",
    "tts_ready",
    "llm_ready",
    "orchestrator_ready",
    "fargate_probe",
    "alb_ready",
    "alb_tag_mutation_warning",
    "file_proof",
    "websocket_proof",
    "cancellation_proof",
    "failure_drills",
    "isolation_proof",
    "cleanup",
    "cleanup_recovery",
)
DEPENDENCIES = {
    "deadline": ("local_bindings",),
    "workers_ready": ("deadline",),
    "terraform_window": ("workers_ready",),
    "endpoints_ready": ("terraform_window",),
    "controller_ready": ("endpoints_ready",),
    "dra_ready": ("controller_ready",),
    "rag_ready": ("dra_ready",),
    "asr_ready": ("rag_ready",),
    "tts_ready": ("asr_ready",),
    "llm_ready": ("tts_ready",),
    "orchestrator_ready": ("llm_ready",),
    "fargate_probe": ("orchestrator_ready",),
    "alb_ready": ("fargate_probe",),
    "alb_tag_mutation_warning": ("alb_ready",),
    "file_proof": ("alb_tag_mutation_warning",),
    "websocket_proof": ("file_proof",),
    "cancellation_proof": ("websocket_proof",),
    "failure_drills": ("cancellation_proof",),
    "isolation_proof": ("failure_drills",),
    "cleanup": ("deadline",),
    "cleanup_recovery": ("deadline",),
}
STATUSES = {"PASS", "WARNING_NON_FATAL", "REFUSED", "INCOMPLETE", "NOT_RUN"}
FORBIDDEN_KEYS = {
    "audio", "audio_bytes", "transcript", "reply", "citation_text",
    "authorization", "bearer", "token", "secret_value", "stdout", "stderr",
}
SAFE_STAGE = re.compile(r"[a-z][a-z0-9_]*")


class ReceiptRefusal(RuntimeError):
    pass


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _walk(value: Any, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str) or key.lower() in FORBIDDEN_KEYS:
                raise ReceiptRefusal(f"receipt field is prohibited: {'.'.join(path + (str(key),))}")
            _walk(child, path + (key,))
    elif isinstance(value, list):
        for child in value:
            _walk(child, path)
    elif isinstance(value, str):
        lowered = value.lower()
        if "bearer " in lowered or "authorization:" in lowered:
            raise ReceiptRefusal("credential-like receipt value is prohibited")


def _write_once(path: Path, encoded: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ReceiptRefusal(f"refusing to overwrite immutable receipt: {path.name}")
    temporary = path.parent / f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise ReceiptRefusal(f"refusing to overwrite immutable receipt: {path.name}") from exc
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
    def __init__(self, directory: Path):
        self.directory = directory

    def path(self, stage: str) -> Path:
        if stage not in STAGES or SAFE_STAGE.fullmatch(stage) is None:
            raise ReceiptRefusal(f"unknown B6.6 receipt stage: {stage}")
        return self.directory / f"{stage}.json"

    def load(self, stage: str) -> dict[str, Any]:
        try:
            value = json.loads(self.path(stage).read_bytes())
        except Exception as exc:
            raise ReceiptRefusal(f"{stage} receipt is absent or malformed") from exc
        if value.get("schema") != SCHEMA or value.get("stage") != stage:
            raise ReceiptRefusal(f"{stage} receipt identity differs")
        if value.get("status") not in STATUSES:
            raise ReceiptRefusal(f"{stage} receipt status differs")
        return value

    def require_pass(self, stage: str) -> dict[str, Any]:
        value = self.load(stage)
        accepted = value["status"] == "PASS" or (
            stage == "alb_tag_mutation_warning"
            and value["status"] == "WARNING_NON_FATAL"
        )
        if not accepted:
            raise ReceiptRefusal(f"{stage} receipt is not PASS")
        return value

    def persist(self, stage: str, status: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.path(stage)
        if status not in STATUSES:
            raise ReceiptRefusal("unknown B6.6 receipt status")
        _walk(payload)
        dependency_hashes: dict[str, str] = {}
        for dependency in DEPENDENCIES.get(stage, ()):
            self.require_pass(dependency)
            dependency_hashes[dependency] = sha256_file(self.path(dependency))
        value = {
            "schema": SCHEMA,
            "stage": stage,
            "status": status,
            "recorded_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "dependencies": dependency_hashes,
            "contains_audio_transcript_reply_citations_credentials_or_phi": False,
            "payload": payload,
        }
        encoded = canonical_json(value)
        _write_once(self.path(stage), encoded)
        return {**value, "receipt_sha256": hashlib.sha256(encoded).hexdigest()}
