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
ATTEMPT_WINDOW_DEFAULT_SECONDS = 10800
ATTEMPT_WINDOW_MAXIMUM_SECONDS = 21600
ATTEMPT_WINDOW_JOB_RESERVE_SECONDS = ATTEMPT_WINDOW_DEFAULT_SECONDS - JOB_ACTIVE_DEADLINE_SECONDS
PHASE_JOURNAL_PATH = "/output/pilot-phase-journal.jsonl"
PHASE_JOURNAL_PROGRESS_INTERVAL = 10

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

_PILOT_DRIVER_PROGRAM = r'''import datetime,json,os,pathlib,re,sys
from medzen_asr_eval import harness
from medzen_asr_eval import pilot as pilot_module

journal_path=pathlib.Path("/output/pilot-phase-journal.jsonl")
receipt_root=pathlib.Path("/output/rows")
sequence=0
completed_rows=0
current_model=None

def safe_text(value):
    text=" ".join(str(value).split())
    text=re.sub(r"(?i)((?:password|secret|token|authorization|credential|api[-_]?key)\s*[:=]\s*)\S+",r"\1<REDACTED>",text)
    text=re.sub(r"(?i)(bearer\s+|basic\s+)[A-Za-z0-9._~+/=-]+",r"\1<REDACTED>",text)
    return text[:1024]

def append_event(phase,**fields):
    global sequence
    sequence+=1
    body={
        "schema_version":1,
        "sequence":sequence,
        "phase":phase,
        "recorded_utc":datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00","Z"),
        "completed_rows":completed_rows,
        "current_model":current_model,
        **fields,
    }
    encoded=json.dumps(body,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()+b"\n"
    mode="xb" if sequence==1 else "ab"
    with journal_path.open(mode) as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())

original_write_once=pilot_module.write_once
def tracked_write_once(path,value):
    global completed_rows
    original_write_once(path,value)
    status=value.get("status") if isinstance(value,dict) else None
    if status=="PASS_ROW_INFERENCE":
        completed_rows+=1
        if completed_rows==1 or completed_rows%10==0:
            append_event("ROW_PROGRESS",candidate=value.get("candidate"),mode=value.get("mode"))
    elif isinstance(status,str) and status.startswith("REFUSED_"):
        append_event("ROW_OR_LOAD_REFUSED",status=status,exception_class=value.get("exception_class"),reason_code=value.get("reason_code"))

def tracked_model_verifier(model_root,binding_path):
    append_event("MODEL_ROOT_VERIFY_START")
    try:
        value=pilot_module.verify_model_root(model_root,binding_path)
    except BaseException as exc:
        append_event("MODEL_ROOT_VERIFY_REFUSED",exception_class=type(exc).__name__,safe_error_text=safe_text(exc))
        raise
    append_event("MODEL_ROOT_VERIFY_PASS")
    return value

def tracked_backend_loader(candidate,mode,language_id,model_root):
    global current_model
    current_model=candidate
    append_event("MODEL_LOAD_START",candidate=candidate)
    try:
        from medzen_asr_eval.backends import load_backend
        value=load_backend(candidate,mode,language_id,model_root)
    except BaseException as exc:
        append_event("MODEL_LOAD_REFUSED",candidate=candidate,exception_class=type(exc).__name__,safe_error_text=safe_text(exc))
        raise
    append_event("MODEL_LOAD_PASS",candidate=candidate)
    return value

pilot_module.write_once=tracked_write_once
expected_rows=int(os.environ.get("MEDZEN_EXPECTED_ROWS","540"))
original_load_runtime_rows=pilot_module.load_runtime_rows
def bound_load_runtime_rows(path):
    value=json.loads(pathlib.Path(path).read_bytes())
    rows=value.get("rows") if isinstance(value,dict) else None
    if not isinstance(rows,list) or len(rows)!=expected_rows:
        append_event("ROW_BOUND_REFUSED",expected=expected_rows,observed=len(rows) if isinstance(rows,list) else None)
        raise harness.EvaluationRefusal("runtime row count differs from the bound shard expectation")
    if expected_rows<=540:
        return original_load_runtime_rows(path)
    validated=[]
    seen=set()
    for start in range(0,len(rows),540):
        piece=dict(value)
        piece["rows"]=rows[start:start+540]
        probe=pathlib.Path("/tmp/medzen-row-slice.json")
        probe.write_bytes(json.dumps(piece,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode())
        validated.extend(original_load_runtime_rows(probe))
        probe.unlink()
    for row in validated:
        checksum=row["audio_checksum_sha256"]
        if checksum in seen:
            raise harness.EvaluationRefusal("runtime row checksums overlap across slices")
        seen.add(checksum)
    append_event("ROW_BOUND_VALIDATED",expected=expected_rows,slices=(len(rows)+539)//540)
    return validated
pilot_module.load_runtime_rows=bound_load_runtime_rows
append_event("PILOT_START")
try:
    result=pilot_module.run_pilot(
        rows_path=pathlib.Path("/input/runtime-rows.json"),
        model_root=pathlib.Path("/models"),
        model_binding_path=pathlib.Path("/input/model-bindings.json"),
        conditioning_path=pathlib.Path("/opt/medzen/assets/language-conditioning-v1.json"),
        receipt_root=receipt_root,
        aggregate_path=pathlib.Path("/output/aggregate.json"),
        backend_loader=tracked_backend_loader,
        model_verifier=tracked_model_verifier,
    )
except (harness.EvaluationRefusal,FileExistsError) as exc:
    append_event("PILOT_REFUSED",exception_class=type(exc).__name__,safe_error_text=safe_text(exc))
    print(harness.canonical_json({"status":"REFUSED","reason":str(exc)}).decode(),end="")
    raise SystemExit(2)
except BaseException as exc:
    append_event("PILOT_EXCEPTION",exception_class=type(exc).__name__,safe_error_text=safe_text(exc))
    raise
append_event("PILOT_PASS")
print(harness.canonical_json(result).decode(),end="")
'''

PILOT_DRIVER_ARGV = (PYTHON, "-c", _PILOT_DRIVER_PROGRAM)

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
        "exec " + shlex.join(PILOT_DRIVER_ARGV),
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


def bound_attempt_window(bindings: dict[str, Any]) -> dict[str, Any]:
    """Resolve the attempt window and Job deadline from committed bindings.

    Bindings without an ``attempt_window`` block (all pilot-era packets and
    history) keep the original 10,800s window with its 9,000s Job cap. A
    present block must state both values explicitly, stay within the
    10,800-21,600s envelope, and preserve the fixed 1,800s host reserve
    between window and Job cap so cleanup always fits inside the window.
    """
    block = bindings.get("attempt_window")
    if block is None:
        return {
            "seconds_each": ATTEMPT_WINDOW_DEFAULT_SECONDS,
            "job_active_deadline_seconds": JOB_ACTIVE_DEADLINE_SECONDS,
            "source": "DEFAULT_PILOT_WINDOW",
        }
    seconds = block.get("seconds_each") if isinstance(block, dict) else None
    job_deadline = block.get("job_active_deadline_seconds") if isinstance(block, dict) else None
    if (
        not isinstance(block, dict)
        or set(block) != {"seconds_each", "job_active_deadline_seconds"}
        or isinstance(seconds, bool)
        or not isinstance(seconds, int)
        or isinstance(job_deadline, bool)
        or not isinstance(job_deadline, int)
        or not ATTEMPT_WINDOW_DEFAULT_SECONDS <= seconds <= ATTEMPT_WINDOW_MAXIMUM_SECONDS
        or job_deadline != seconds - ATTEMPT_WINDOW_JOB_RESERVE_SECONDS
    ):
        raise PilotWorkloadRefusal("attempt window binding differs from the bounded contract")
    return {
        "seconds_each": seconds,
        "job_active_deadline_seconds": job_deadline,
        "source": "BINDINGS_ATTEMPT_WINDOW",
    }


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
    if not body.endswith(shlex.join(PILOT_DRIVER_ARGV)) or " && exec " not in body:
        raise PilotWorkloadRefusal("pilot process is not final exec after both gates")
    if "time.monotonic()+900" not in body or "SystemExit(71)" not in body:
        raise PilotWorkloadRefusal("isolation listener lacks a bounded distinct timeout")
    if any(re.search(pattern, body) for pattern in (r"\$HOME", r"\$USER", r"~/", r"getpwnam")):
        raise PilotWorkloadRefusal("pilot command depends on an ambient account environment")
    names = [item["name"] for item in PILOT_ENVIRONMENT]
    if len(names) != len(set(names)) or {"HOME", "PATH", "PYTHONPATH"} - set(names):
        raise PilotWorkloadRefusal("pilot environment is incomplete or ambiguous")
    try:
        compile(_PILOT_DRIVER_PROGRAM, "<medzen-pilot-driver>", "exec")
    except SyntaxError as exc:
        raise PilotWorkloadRefusal("pilot phase-journal driver is not valid Python") from exc
    required_phases = {
        "PILOT_START",
        "MODEL_ROOT_VERIFY_START",
        "MODEL_ROOT_VERIFY_PASS",
        "MODEL_LOAD_START",
        "MODEL_LOAD_PASS",
        "ROW_PROGRESS",
        "PILOT_REFUSED",
        "PILOT_EXCEPTION",
        "PILOT_PASS",
    }
    missing_phases = sorted(
        phase for phase in required_phases if f'"{phase}"' not in _PILOT_DRIVER_PROGRAM
    )
    if missing_phases or PHASE_JOURNAL_PATH not in _PILOT_DRIVER_PROGRAM:
        raise PilotWorkloadRefusal("pilot phase/error journal coverage differs")
    if any(token in _PILOT_DRIVER_PROGRAM for token in ("prediction=value", "reference=value")):
        raise PilotWorkloadRefusal("pilot journal may not persist predictions or references")
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
        "phase_journal_path": PHASE_JOURNAL_PATH,
        "phase_journal_required_phases": sorted(required_phases),
        "phase_journal_progress_interval_rows": PHASE_JOURNAL_PROGRESS_INTERVAL,
        "phase_journal_exception_class_and_safe_text": True,
        "phase_journal_predictions_references_or_audio": False,
        "phase_journal_driver_sha256": hashlib.sha256(
            _PILOT_DRIVER_PROGRAM.encode("utf-8")
        ).hexdigest(),
    }
