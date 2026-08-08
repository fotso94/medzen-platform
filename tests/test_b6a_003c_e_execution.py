from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline.runtime_receipts_v2 import ReceiptStore
from scripts import b6a_003c_e_common as common
from scripts.run_b6a_003c_e_sampler_self_test import (
    SamplerCommandRefusal,
    run_ssm_self_test,
)


INSTANCE = "i-0123456789abcdef0"
POD_UID = "12345678-1234-1234-1234-123456789abc"
PASS_LINE = (
    "MEDZEN_SAMPLER_SELF_TEST_V1 status=PASS samples=120 gpu_index=0 "
    "min_used_mib=0 peak_used_mib=0 total_mib=23034\n"
)


class SSM:
    def __init__(self, *, status: str = "Success", stdout: str = PASS_LINE, stderr: str = ""):
        self.status = status
        self.stdout = stdout
        self.stderr = stderr
        self.sent = None

    def send_command(self, **kwargs):
        self.sent = kwargs
        return {"Command": {"CommandId": "12345678-1234-1234-1234-123456789abc"}}

    def get_command_invocation(self, **kwargs):
        return {
            "Status": self.status,
            "StandardOutputContent": self.stdout,
            "StandardErrorContent": self.stderr,
        }


def test_003c_e_executes_proven_sampler_with_explicit_bash_and_preserves_output() -> None:
    ssm = SSM()
    command = (ROOT / "scripts/b6a_003c_e_ssm_sampler.sh").read_text()
    result = run_ssm_self_test(
        ssm,
        instance_id=INSTANCE,
        pod_uid=POD_UID,
        command_text=command,
        sleeper=lambda _: None,
    )
    assert result["sample_count"] == 120
    assert result["raw_stdout"] == PASS_LINE
    assert result["raw_stderr"] == ""
    sent = ssm.sent["Parameters"]["commands"][0]
    assert "/usr/bin/bash -s <<'MEDZEN_SAMPLER_SCRIPT'" in sent
    assert "/usr/local/bin/nerdctl" in sent
    assert ssm.sent["CloudWatchOutputConfig"] == {"CloudWatchOutputEnabled": False}


def test_003c_e_failed_ssm_command_preserves_bounded_pre_artifact_diagnostics(tmp_path) -> None:
    ssm = SSM(
        status="Failed",
        stdout="MEDZEN_SAMPLER_SELF_TEST_V1 status=REFUSED code=NERDCTL_ABSENT\n",
        stderr="failed to run commands: exit status 1",
    )
    with pytest.raises(SamplerCommandRefusal) as raised:
        run_ssm_self_test(
            ssm,
            instance_id=INSTANCE,
            pod_uid=POD_UID,
            command_text="exit 1",
            sleeper=lambda _: None,
        )
    error = raised.value
    store = ReceiptStore(
        tmp_path, policy_path=ROOT / "platform/runtime-receipt-policy-v2.yaml"
    )
    receipt = store.persist(
        "sampler_self_test",
        "REFUSED",
        {
            "pre_artifact_facts": {
                "model_artifact_present_on_node": False,
                "audio_artifact_present_on_node": False,
                "model_or_audio_workload_applied": False,
            },
            "command_id": error.command_id,
            "command_path": "scripts/b6a_003c_e_ssm_sampler.sh",
            "command_sha256": "a" * 64,
            "raw_stdout": error.stdout,
            "raw_stderr": error.stderr,
        },
        dependencies=(),
    )
    assert receipt["payload"]["raw_stdout"].endswith("NERDCTL_ABSENT\n")
    assert receipt["payload"]["raw_stderr"] == "failed to run commands: exit status 1"


def test_003c_e_window_is_deadline_first_and_uses_remaining_allowance() -> None:
    text = (ROOT / "scripts/run_b6a_003c_e_gpu_window.sh").read_text()
    assert text.index("trap cleanup EXIT INT TERM") < text.index("003c_e_deadline.py arm")
    assert text.index("003c_e_deadline.py arm") < text.index("desiredSize=1")
    assert text.index("run_b6a_003c_e_sampler_self_test.py") < text.index(
        "run_b6a_003c_e_proof.py"
    )
    assert "--window-seconds 5109" in text
    assert common.MAX_WINDOW_SECONDS == 5109


def test_003c_e_authorization_fails_closed_without_independent_review(tmp_path) -> None:
    record = {
        "id": common.AUTH_ID,
        "status": "owner-approved",
        "packet": {"id": common.PACKET_ID, "sha256": "a" * 64},
        "aws_scope": {"maximum_window_seconds": 5109},
        "independent_review": {"status": "NOT_REVIEWED"},
        "bound_resources": {
            "workload_render_sha256": common.WORKLOAD_SHA256,
            "synthetic_audio_sha256": common.AUDIO_SHA256,
        },
        "source_bindings": {"scripts/b6a_003c_e_ssm_sampler.sh": "b" * 64},
    }
    path = tmp_path / "authorization.json"
    path.write_text(json.dumps(record))
    with pytest.raises(common.BindingRefusal, match="independent 003C-E review"):
        common.authorization(path, "a" * 64, ROOT)
