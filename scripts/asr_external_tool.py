#!/usr/bin/env python3
"""Bounded, sanitized diagnostics for every ASR-pilot external tool call."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Sequence


MAX_CAPTURE_BYTES = 4096
SECRET_KEY_RE = re.compile(r"(?i)(password|secret|token|authorization|credential|api[-_]?key)")
SECRET_VALUE_RE = re.compile(r"(?i)(bearer\s+|basic\s+)[A-Za-z0-9._~+/=-]+")
PRESIGNED_QUERY_VALUE_RE = re.compile(
    r"(?i)((?:X-Amz-(?:Algorithm|Credential|Date|Expires|Security-Token|Signature|SignedHeaders)|"
    r"versionId)=)[^&\s]+"
)
_DEFAULT_JOURNAL_PATH: Path | None = None


class ExternalToolTimeout(RuntimeError):
    def __init__(self, diagnostic: dict[str, Any]):
        super().__init__("external tool exceeded its bounded timeout")
        self.diagnostic = diagnostic


def configure_external_tool_journal(path: Path | None) -> Path | None:
    global _DEFAULT_JOURNAL_PATH
    prior = _DEFAULT_JOURNAL_PATH
    _DEFAULT_JOURNAL_PATH = path
    return prior


def sanitize_bytes(raw: bytes | str | None) -> str:
    if raw is None:
        return ""
    text = raw if isinstance(raw, str) else raw.decode(errors="replace")
    homes = {os.environ.get("HOME"), str(Path.home())}
    for home in sorted((value for value in homes if value), key=len, reverse=True):
        text = text.replace(home, "<HOME>")
    text = SECRET_VALUE_RE.sub(lambda match: f"{match.group(1)}<REDACTED>", text)
    text = PRESIGNED_QUERY_VALUE_RE.sub(r"\1<REDACTED>", text)
    text = re.sub(
        r"(?i)((?:password|secret|token|authorization|credential|api[-_]?key)\s*[:=]\s*)\S+",
        r"\1<REDACTED>",
        text,
    )
    return " ".join(text.split())[:MAX_CAPTURE_BYTES]


def sanitize_command(command: Sequence[str]) -> list[str]:
    values: list[str] = []
    redact_next = False
    for raw in command:
        value = str(raw)
        if redact_next:
            values.append("<REDACTED>")
            redact_next = False
            continue
        key = value.split("=", 1)[0]
        if SECRET_KEY_RE.search(key):
            if "=" in value:
                values.append(f"{key}=<REDACTED>")
            else:
                values.append(value)
                redact_next = True
            continue
        values.append(sanitize_bytes(value))
    return values


def run_external(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    input: bytes | None = None,
    timeout: int | float,
    env: dict[str, str] | None = None,
    text: bool = False,
    journal_path: Path | None = None,
) -> tuple[subprocess.CompletedProcess[Any], dict[str, Any]]:
    """Run one command and return a safe, bounded diagnostic for success or failure."""
    started = time.monotonic()
    try:
        completed = subprocess.run(
            list(command),
            cwd=cwd,
            input=input,
            capture_output=True,
            check=False,
            timeout=timeout,
            env=env,
            text=text,
        )
    except subprocess.TimeoutExpired as exc:
        stdout_raw = _bytes(exc.stdout)
        stderr_raw = _bytes(exc.stderr)
        diagnostic = {
            "status": "TIMEOUT",
            "command": sanitize_command(command),
            "timeout_seconds": timeout,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "stdout_bytes": len(stdout_raw),
            "stderr_bytes": len(stderr_raw),
            "stdout_sha256": hashlib.sha256(stdout_raw).hexdigest(),
            "stderr_sha256": hashlib.sha256(stderr_raw).hexdigest(),
            "stdout_sanitized": sanitize_bytes(exc.stdout),
            "stderr_sanitized": sanitize_bytes(exc.stderr),
            "environment_values_recorded": False,
        }
        append_diagnostic(journal_path or _DEFAULT_JOURNAL_PATH, diagnostic)
        raise ExternalToolTimeout(diagnostic) from exc
    stdout = _bytes(completed.stdout)
    stderr = _bytes(completed.stderr)
    diagnostic = {
        "status": "PASS" if completed.returncode == 0 else "NONZERO_EXIT",
        "command": sanitize_command(command),
        "returncode": completed.returncode,
        "timeout_seconds": timeout,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "stdout_bytes": len(stdout),
        "stderr_bytes": len(stderr),
        "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
        "stdout_sanitized": sanitize_bytes(completed.stdout),
        "stderr_sanitized": sanitize_bytes(completed.stderr),
        "environment_values_recorded": False,
    }
    append_diagnostic(journal_path or _DEFAULT_JOURNAL_PATH, diagnostic)
    return completed, diagnostic


def append_diagnostic(path: Path | None, diagnostic: dict[str, Any]) -> None:
    if path is None:
        return
    path.mkdir(parents=True, exist_ok=True)
    sequence = len(list(path.glob("*.json"))) + 1
    body = json.dumps(
        {**diagnostic, "sequence": sequence},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode() + b"\n"
    target = path / f"{sequence:04d}.json"
    with target.open("xb") as stream:
        stream.write(body)


def _bytes(value: bytes | str | None) -> bytes:
    if value is None:
        return b""
    return value.encode() if isinstance(value, str) else value
