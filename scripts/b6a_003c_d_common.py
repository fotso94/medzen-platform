"""Shared fail-closed bindings for the unapproved B6A packet 003C-D."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


AUTH_ID = "B6A-AWS-AUTH-2026-003C-D"
PACKET_ID = "B6A-AWS-CHANGE-PACKET-2026-003C-D"
MAX_WINDOW_SECONDS = 6520
WORKLOAD_SHA256 = "9e51c009ea995c15261505b2416611a8bbf2e8071e75758527d9e180f9be8f68"
AUDIO_SHA256 = "3e7b78cbf65b5b857d0bd2ea6b2568ce74c523be2b319ade8930c9ac6a7630c3"


class BindingRefusal(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def authorization(path: Path, packet_sha256: str, repo_root: Path) -> dict[str, Any]:
    if re.fullmatch(r"[0-9a-f]{64}", packet_sha256) is None:
        raise BindingRefusal("exact 003C-D packet SHA-256 is required")
    try:
        record = json.loads(path.read_bytes())
    except Exception as exc:
        raise BindingRefusal("003C-D authorization is absent or unreadable") from exc
    if record.get("id") != AUTH_ID or record.get("status") != "owner-approved":
        raise BindingRefusal("003C-D is not owner-approved")
    if record.get("packet") != {"id": PACKET_ID, "sha256": packet_sha256}:
        raise BindingRefusal("003C-D authorization packet binding differs")
    if record.get("aws_scope", {}).get("maximum_window_seconds") != MAX_WINDOW_SECONDS:
        raise BindingRefusal("003C-D remaining GPU allowance binding differs")
    if record.get("independent_iam_review", {}).get("status") != "PASS":
        raise BindingRefusal("independent IAM review is absent")
    resources = record.get("bound_resources", {})
    if resources.get("workload_render_sha256") != WORKLOAD_SHA256:
        raise BindingRefusal("003C-D workload render binding differs")
    if resources.get("synthetic_audio_sha256") != AUDIO_SHA256:
        raise BindingRefusal("003C-D synthetic audio binding differs")
    sources = record.get("source_bindings")
    if not isinstance(sources, dict) or not sources:
        raise BindingRefusal("003C-D source bindings are absent")
    for relative, expected in sorted(sources.items()):
        if not isinstance(relative, str) or relative.startswith("/") or ".." in Path(relative).parts:
            raise BindingRefusal("003C-D source path is unsafe")
        source = repo_root / relative
        if not source.is_file() or sha256_file(source) != expected:
            raise BindingRefusal(f"003C-D source binding differs: {relative}")
    return record
