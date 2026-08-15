from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.asr_external_tool import (
    ExternalToolTimeout,
    run_external,
    sanitize_command,
    sanitize_head_tail,
)


def test_external_tool_retains_bounded_sanitized_failure_diagnostic(tmp_path: Path) -> None:
    command = [
        sys.executable,
        "-c",
        "import sys; print('ok'); print('token=very-secret', file=sys.stderr); raise SystemExit(7)",
    ]
    completed, diagnostic = run_external(
        command, timeout=10, journal_path=tmp_path / "journal"
    )
    assert completed.returncode == 7
    assert diagnostic["status"] == "NONZERO_EXIT"
    assert diagnostic["returncode"] == 7
    assert diagnostic["stdout_sanitized"] == "ok"
    assert "very-secret" not in diagnostic["stderr_sanitized"]
    assert "<REDACTED>" in diagnostic["stderr_sanitized"]
    persisted = json.loads((tmp_path / "journal/0001.json").read_bytes())
    assert persisted["stderr_sha256"] == diagnostic["stderr_sha256"]
    assert persisted["environment_values_recorded"] is False


def test_external_tool_timeout_is_diagnosed_and_journaled(tmp_path: Path) -> None:
    with pytest.raises(ExternalToolTimeout) as captured:
        run_external(
            [sys.executable, "-c", "import time; print('before'); time.sleep(5)"],
            timeout=0.05,
            journal_path=tmp_path / "journal",
        )
    assert captured.value.diagnostic["status"] == "TIMEOUT"
    assert json.loads((tmp_path / "journal/0001.json").read_bytes())["status"] == "TIMEOUT"


def test_command_arguments_with_secret_keys_are_redacted() -> None:
    assert sanitize_command(["tool", "--password", "secret-value", "token=abc", "visible"]) == [
        "tool", "--password", "<REDACTED>", "token=<REDACTED>", "visible"
    ]


def test_large_safe_preamble_cannot_evict_sanitized_terminal_tail() -> None:
    raw = (
        (b"safe-preamble " * 600)
        + b"token=must-not-survive "
        + b"RuntimeError: terminal model-load failure\n"
    )
    value = sanitize_head_tail(raw)
    assert value["window_policy"] == "SANITIZED_HEAD_AND_TAIL"
    assert value["truncated"] is True
    assert "terminal model-load failure" not in value["head_sanitized"]
    assert "terminal model-load failure" in value["tail_sanitized"]
    assert "must-not-survive" not in value["head_sanitized"]
    assert "must-not-survive" not in value["tail_sanitized"]
