"""Single write-once receipt engine for the consolidated B6.6 window."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


SCHEMA = "MEDZEN_B6_INTEGRATION_RECEIPT_V2"
EXECUTION_STAGES = (
    "stage0",
    "deadline",
    "workers_ready",
    "dra_ready",
    "rag_ready",
    "asr_ready",
    "tts_ready",
    "llm_ready",
    "orchestrator_ready",
    "controller_window",
    "controller_ready",
    "pre_endpoint_images",
    "terraform_window",
    "endpoints_ready",
    "alb_ready",
    "fargate_probe",
    "alb_tag_mutation_warning",
    "file_proof",
    "websocket_proof",
    "cancellation_proof",
    "failure_drills",
    "isolation_proof",
)
WINDOW_STAGES = (*EXECUTION_STAGES, "cleanup")
STAGE_A_EXECUTION_STAGES = (
    "stage_a_preflight",
    "stage_a_terraform",
    "stage_a_endpoints",
    "stage_a_probe_1",
    "stage_a_probe_2",
    "stage_a_probe_3",
)
STAGE_A_STAGES = (*STAGE_A_EXECUTION_STAGES, "stage_a_cleanup", "stage_a")
AUXILIARY_STAGES = ("persistent_secret_bridge", "runner_exception", "cold_rehearsal")
ALL_STAGES = (*WINDOW_STAGES, *STAGE_A_STAGES, *AUXILIARY_STAGES)
STATUSES = {"PASS", "REFUSED", "WARNING_NON_FATAL"}
FORBIDDEN_KEYS = {
    "audio",
    "audio_bytes",
    "transcript",
    "reply",
    "citation_text",
    "authorization",
    "bearer",
    "token",
    "secret_value",
    "stdout",
    "stderr",
}
SAFE_STAGE = re.compile(r"[a-z][a-z0-9_]*")
SAFE_SHA256 = re.compile(r"[0-9a-f]{64}")


class ReceiptRefusal(RuntimeError):
    pass


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _walk(value: Any, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str) or key.lower() in FORBIDDEN_KEYS:
                raise ReceiptRefusal(
                    f"receipt field is prohibited: {'.'.join(path + (str(key),))}"
                )
            _walk(child, path + (key,))
    elif isinstance(value, list):
        for child in value:
            _walk(child, path)
    elif isinstance(value, str):
        lowered = value.lower()
        if "bearer " in lowered or "authorization:" in lowered:
            raise ReceiptRefusal("credential-like receipt value is prohibited")


def write_once(path: Path, value: dict[str, Any]) -> str:
    _walk(value)
    encoded = canonical_json(value)
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
            raise ReceiptRefusal(
                f"refusing to overwrite immutable receipt: {path.name}"
            ) from exc
        temporary.unlink()
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()
    return sha256_bytes(encoded)


class ReceiptStore:
    def __init__(self, directory: Path, *, clock: Callable[[], str] = utc_now):
        self.directory = directory
        self.clock = clock

    def path(self, stage: str) -> Path:
        if stage not in ALL_STAGES or SAFE_STAGE.fullmatch(stage) is None:
            raise ReceiptRefusal(f"unknown consolidated B6.6 receipt stage: {stage}")
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

    def hashes(self) -> dict[str, str]:
        return {
            stage: sha256_file(self.path(stage))
            for stage in ALL_STAGES
            if self.path(stage).exists()
        }

    def persist(
        self,
        stage: str,
        status: str,
        payload: dict[str, Any],
        *,
        dependencies: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        self.path(stage)
        if status not in STATUSES:
            raise ReceiptRefusal("unknown consolidated B6.6 receipt status")
        dependency_hashes = dependencies or {}
        if any(
            dependency not in ALL_STAGES
            or SAFE_SHA256.fullmatch(str(digest)) is None
            for dependency, digest in dependency_hashes.items()
        ):
            raise ReceiptRefusal("receipt dependency binding is malformed")
        value = {
            "schema": SCHEMA,
            "stage": stage,
            "status": status,
            "recorded_utc": self.clock(),
            "dependencies": dict(sorted(dependency_hashes.items())),
            "contains_audio_transcript_reply_citations_credentials_or_phi": False,
            "payload": payload,
        }
        digest = write_once(self.path(stage), value)
        return {**value, "receipt_sha256": digest}
