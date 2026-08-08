from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import b6a_003c_f_common as common
from scripts.run_b6a_003c_e_sampler_self_test import SamplerCommandRefusal
from scripts.run_b6a_003c_f_sampler_self_test import run_ssm_self_test


INSTANCE = "i-0123456789abcdef0"
POD_UID = "12345678-1234-1234-1234-123456789abc"
COMMAND_ID = "12345678-1234-1234-1234-123456789abc"
PASS_LINE = (
    "MEDZEN_SAMPLER_SELF_TEST_V1 status=PASS samples=120 gpu_index=0 "
    "min_used_mib=0 peak_used_mib=0 total_mib=23034\n"
)


class DiscoveryError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class SSM:
    def __init__(self, responses):
        self.responses = iter(responses)

    def send_command(self, **kwargs):
        return {"Command": {"CommandId": COMMAND_ID}}

    def get_command_invocation(self, **kwargs):
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return value


def success():
    return {
        "Status": "Success",
        "StandardOutputContent": PASS_LINE,
        "StandardErrorContent": "",
    }


def run(ssm, *, maximum_polls=60, sleeps=None):
    sleeps = [] if sleeps is None else sleeps
    result = run_ssm_self_test(
        ssm,
        instance_id=INSTANCE,
        pod_uid=POD_UID,
        command_text="true",
        sleeper=sleeps.append,
        maximum_polls=maximum_polls,
    )
    return result, sleeps


def test_003c_f_retries_delayed_invocation_discovery_then_passes():
    result, sleeps = run(
        SSM([DiscoveryError("InvocationDoesNotExist"), DiscoveryError("InvocationDoesNotExist"), success()])
    )
    assert result["command_id"] == COMMAND_ID
    assert result["sample_count"] == 120
    assert sleeps == [3, 3]


def test_003c_f_permanent_discovery_timeout_preserves_command_id():
    with pytest.raises(SamplerCommandRefusal) as raised:
        run(SSM([DiscoveryError("InvocationDoesNotExist")] * 3), maximum_polls=3)
    assert raised.value.code == "SSM_INVOCATION_DISCOVERY_OR_EXECUTION_TIMEOUT"
    assert raised.value.command_id == COMMAND_ID
    assert raised.value.status == "TimedOut"


def test_003c_f_unexpected_lookup_error_fails_closed_with_command_id():
    with pytest.raises(SamplerCommandRefusal) as raised:
        run(SSM([DiscoveryError("AccessDeniedException")]))
    assert raised.value.code == "SSM_INVOCATION_LOOKUP_FAILED"
    assert raised.value.command_id == COMMAND_ID
    assert raised.value.status == "LookupError:AccessDeniedException"


def test_003c_f_unknown_status_fails_closed_with_command_id():
    with pytest.raises(SamplerCommandRefusal) as raised:
        run(SSM([{"Status": "Mystery"}]))
    assert raised.value.code == "SSM_COMMAND_STATUS_UNKNOWN"
    assert raised.value.command_id == COMMAND_ID


def test_003c_f_launcher_binds_import_path_deadline_sampler_and_proof_order():
    text = (ROOT / "scripts/run_b6a_003c_f_gpu_window.sh").read_text()
    assert 'export PYTHONPATH="$repo_root${PYTHONPATH:+:$PYTHONPATH}"' in text
    assert text.index("trap cleanup EXIT INT TERM") < text.index("003c_f_deadline.py arm")
    assert text.index("003c_f_deadline.py arm") < text.index("desiredSize=1")
    assert text.index("run_b6a_003c_f_sampler_self_test.py") < text.index(
        "run_b6a_003c_e_proof.py"
    )
    assert "--window-seconds 4610" in text
    assert common.MAX_WINDOW_SECONDS == 4610


def test_003c_f_authorization_fails_closed_without_independent_review(tmp_path):
    record = {
        "id": common.AUTH_ID,
        "status": "owner-approved",
        "packet": {"id": common.PACKET_ID, "sha256": "a" * 64},
        "aws_scope": {"maximum_window_seconds": 4610},
        "independent_review": {"status": "NOT_REVIEWED"},
        "bound_resources": {
            "workload_render_sha256": common.WORKLOAD_SHA256,
            "synthetic_audio_sha256": common.AUDIO_SHA256,
        },
        "source_bindings": {"scripts/b6a_003c_e_ssm_sampler.sh": "b" * 64},
    }
    path = tmp_path / "authorization.json"
    path.write_text(json.dumps(record))
    with pytest.raises(common.BindingRefusal, match="independent 003C-F review"):
        common.authorization(path, "a" * 64, ROOT)
