from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.run_b6a_003c_d_sampler_self_test import (
    SamplerRefusal,
    instance_for_node,
    parse_pass_summary,
    run_ssm_self_test,
    wait_online,
)


INSTANCE = "i-0123456789abcdef0"
POD_UID = "12345678-1234-1234-1234-123456789abc"


class SSM:
    def __init__(self, *, output=""):
        self.output = output or (
            "MEDZEN_SAMPLER_SELF_TEST_V1 status=PASS samples=120 gpu_index=0 "
            "min_used_mib=4 peak_used_mib=17 total_mib=23034\n"
        )
        self.sent = None

    def describe_instance_information(self, **kwargs):
        return {"InstanceInformationList": [{
            "InstanceId": INSTANCE, "PingStatus": "Online"
        }]}

    def send_command(self, **kwargs):
        self.sent = kwargs
        return {"Command": {"CommandId": "12345678-1234-1234-1234-123456789abc"}}

    def get_command_invocation(self, **kwargs):
        return {"Status": "Success", "StandardOutputContent": self.output}


def test_numeric_summary_accepts_only_one_exact_safe_line():
    assert parse_pass_summary(
        "MEDZEN_SAMPLER_SELF_TEST_V1 status=PASS samples=120 gpu_index=0 "
        "min_used_mib=4 peak_used_mib=17 total_mib=23034\n"
    )["sample_count"] == 120
    with pytest.raises(SamplerRefusal, match="OUTPUT_INVALID"):
        parse_pass_summary("raw driver output\n")
    with pytest.raises(SamplerRefusal, match="VALUES_INVALID"):
        parse_pass_summary(
            "MEDZEN_SAMPLER_SELF_TEST_V1 status=PASS samples=120 gpu_index=0 "
            "min_used_mib=18 peak_used_mib=17 total_mib=23034\n"
        )


def test_ssm_self_test_has_no_remote_output_sink_and_binds_exact_context():
    ssm = SSM()
    command = (ROOT / "scripts/b6a_003c_d_ssm_sampler.sh").read_text()
    result = run_ssm_self_test(
        ssm,
        instance_id=INSTANCE,
        pod_uid=POD_UID,
        command_text=command,
        sleeper=lambda _: None,
    )
    assert result["sample_count"] == 120
    assert ssm.sent["InstanceIds"] == [INSTANCE]
    assert ssm.sent["DocumentName"] == "AWS-RunShellScript"
    assert ssm.sent["CloudWatchOutputConfig"] == {"CloudWatchOutputEnabled": False}
    assert "OutputS3" not in json.dumps(ssm.sent)
    sent_command = ssm.sent["Parameters"]["commands"][0]
    assert POD_UID in sent_command
    assert "/busybox/chroot /driver-root /usr/bin/nvidia-smi" in sent_command


def test_ssm_node_must_be_exactly_online_without_ambiguity():
    ssm = SSM()
    wait_online(ssm, INSTANCE, timeout_seconds=1, sleeper=lambda _: None)
    ssm.describe_instance_information = lambda **_: {
        "InstanceInformationList": [
            {"InstanceId": INSTANCE, "PingStatus": "Online"},
            {"InstanceId": "i-11111111111111111", "PingStatus": "Online"},
        ]
    }
    with pytest.raises(SamplerRefusal, match="AMBIGUOUS"):
        wait_online(ssm, INSTANCE, timeout_seconds=1, sleeper=lambda _: None)


def test_gpu_node_provider_id_maps_to_one_instance(monkeypatch, tmp_path):
    def run(command, **kwargs):
        return subprocess.CompletedProcess(
            command, 0, json.dumps({"spec": {"providerID": f"aws:///eu-central-1a/{INSTANCE}"}}), ""
        )

    monkeypatch.setattr(subprocess, "run", run)
    assert instance_for_node(tmp_path / "kubeconfig", "gpu-node") == INSTANCE


def test_gpu_window_self_tests_before_model_proof_and_cleanup_is_preinstalled():
    text = (ROOT / "scripts/run_b6a_003c_d_gpu_window.sh").read_text()
    trap = text.index("trap cleanup EXIT INT TERM")
    arm = text.index("003c_d_deadline.py arm")
    scale = text.index("desiredSize=1")
    sampler = text.index("run_b6a_003c_d_sampler_self_test.py")
    proof = text.index("run_b6a_003c_d_proof.py")
    assert trap < arm < scale < sampler < proof
    assert "--window-seconds 6520" in text


def test_node_sampler_runs_120_numeric_reads_in_driver_root_context():
    text = (ROOT / "scripts/b6a_003c_d_ssm_sampler.sh").read_text()
    assert "iteration < 120" in text
    assert "samples -eq 120" in text
    assert "/busybox/chroot /driver-root /usr/bin/nvidia-smi" in text
    assert "--query-gpu=index,memory.used,memory.total" in text
