#!/usr/bin/env python3
"""Canonical never-before-run pilot workload command and its static audit."""

from __future__ import annotations

import hashlib
import re
import shlex
from typing import Any


PYTHON = "/opt/venv/bin/python"
PILOT_WORKLOAD_COMMAND = ("/bin/sh", "-ec")
LISTENER_TIMEOUT_SECONDS = 900
JOB_ACTIVE_DEADLINE_SECONDS = 9000
JOB_TERMINATION_GRACE_SECONDS = 30

_LISTENER_PROGRAM = """import pathlib,socket,sys,time
ready=pathlib.Path('/output/inbound-listener-ready')
release=pathlib.Path('/input/network-release')
deadline=time.monotonic()+900
s=socket.socket()
try:
    s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
    s.bind(('0.0.0.0',8080))
    s.listen(1)
    ready.write_text('READY\\n',encoding='utf-8')
    while not release.exists():
        if time.monotonic()>=deadline:
            raise SystemExit(71)
        time.sleep(1)
finally:
    try:
        s.close()
    except OSError:
        pass
"""

PILOT_WORKLOAD_SCRIPT = " && ".join(
    (
        shlex.join(
            (
                PYTHON,
                "-m",
                "medzen_asr_eval",
                "network-probe",
                "--binding",
                "/input/network-binding.json",
                "--receipt",
                "/output/network-probe.json",
            )
        ),
        shlex.join((PYTHON, "-c", _LISTENER_PROGRAM)),
        "exec "
        + shlex.join(
            (
                PYTHON,
                "-m",
                "medzen_asr_eval",
                "pilot",
                "--rows",
                "/input/runtime-rows.json",
                "--model-root",
                "/input/models",
                "--model-binding",
                "/input/model-bindings.json",
                "--conditioning",
                "/opt/medzen/assets/language-conditioning-v1.json",
                "--receipt-root",
                "/output/rows",
                "--aggregate-receipt",
                "/output/aggregate.json",
            )
        ),
    )
)

PILOT_ENVIRONMENT = (
    {"name": "HOME", "value": "/tmp"},
    {"name": "PATH", "value": "/opt/venv/bin:/usr/local/bin:/usr/bin:/bin"},
    {"name": "PYTHONPATH", "value": "/opt/medzen"},
    {"name": "HF_HUB_OFFLINE", "value": "1"},
    {"name": "HF_HUB_DISABLE_XET", "value": "1"},
    {"name": "FAIRSEQ2_ASSET_DIR", "value": "/opt/medzen/assets"},
    {"name": "FAIRSEQ2_CACHE_DIR", "value": "/tmp/fairseq2-cache"},
)


class PilotWorkloadRefusal(RuntimeError):
    pass


def canonical_workload_argv() -> tuple[str, ...]:
    return (*PILOT_WORKLOAD_COMMAND, PILOT_WORKLOAD_SCRIPT)


def workload_argv_sha256() -> str:
    return hashlib.sha256(
        b"\0".join(value.encode("utf-8") for value in canonical_workload_argv())
    ).hexdigest()


def audit_pilot_workload() -> dict[str, Any]:
    body = PILOT_WORKLOAD_SCRIPT
    if body.count(PYTHON) != 3 or " python " in f" {body} ":
        raise PilotWorkloadRefusal("pilot Python executable is not absolute and singular")
    if not body.endswith("--aggregate-receipt /output/aggregate.json") or " && exec " not in body:
        raise PilotWorkloadRefusal("pilot process is not final exec after both gates")
    if "time.monotonic()+900" not in body or "SystemExit(71)" not in body:
        raise PilotWorkloadRefusal("isolation listener lacks a bounded distinct timeout")
    if any(re.search(pattern, body) for pattern in (r"\$HOME", r"\$USER", r"~/", r"getpwnam")):
        raise PilotWorkloadRefusal("pilot command depends on an ambient account environment")
    names = [item["name"] for item in PILOT_ENVIRONMENT]
    if len(names) != len(set(names)) or {"HOME", "PATH", "PYTHONPATH"} - set(names):
        raise PilotWorkloadRefusal("pilot environment is incomplete or ambiguous")
    return {
        "status": "PASS_PILOT_WORKLOAD_STATIC_AUDIT",
        "historical_live_pass": False,
        "canonical_argv_sha256": workload_argv_sha256(),
        "absolute_python_invocations": body.count(PYTHON),
        "account_name_lookups": 0,
        "ambient_environment_reads": 0,
        "explicit_environment_keys": names,
        "listener_timeout_seconds": LISTENER_TIMEOUT_SECONDS,
        "listener_timeout_exit_code": 71,
        "job_active_deadline_seconds": JOB_ACTIVE_DEADLINE_SECONDS,
        "termination_grace_seconds": JOB_TERMINATION_GRACE_SECONDS,
        "network_probe_before_torch": True,
        "final_process_uses_exec": True,
        "receipts_written_by_runtime": True,
    }
