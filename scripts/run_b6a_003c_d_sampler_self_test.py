#!/usr/bin/env python3
"""Prove the exact GPU sampler through SSM before deploying the model."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.runtime_receipts import ReceiptStore
from scripts import run_b6a_003c_b_proof as b_proof
from scripts.run_b6a_003c_c_proof import wait_for_stable_dra


POLICY = ROOT / "platform/runtime-receipt-policy-v1.yaml"
COMMAND = ROOT / "scripts/b6a_003c_d_ssm_sampler.sh"
REGION = "eu-central-1"
PROFILE = "medzen"
PASS_RE = re.compile(
    r"^MEDZEN_SAMPLER_SELF_TEST_V1 status=PASS samples=120 gpu_index=0 "
    r"min_used_mib=(\d+) peak_used_mib=(\d+) total_mib=(\d+)$"
)


class SamplerRefusal(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _json(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(command, check=False, text=True, capture_output=True)
    if completed.returncode != 0:
        raise SamplerRefusal("KUBERNETES_NODE_QUERY_FAILED")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise SamplerRefusal("KUBERNETES_NODE_RESPONSE_MALFORMED") from exc


def instance_for_node(kubeconfig: Path, node_name: str) -> str:
    node = _json([
        "kubectl", "--kubeconfig", str(kubeconfig), "get", "node", node_name,
        "-o", "json",
    ])
    provider = node.get("spec", {}).get("providerID")
    match = re.fullmatch(r"aws:///[^/]+/(i-[0-9a-f]{17})", str(provider))
    if match is None:
        raise SamplerRefusal("GPU_INSTANCE_ID_ABSENT")
    return match.group(1)


def wait_online(
    ssm: Any,
    instance_id: str,
    *,
    timeout_seconds: int = 180,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> None:
    stop = clock() + timeout_seconds
    while clock() < stop:
        response = ssm.describe_instance_information(
            Filters=[{"Key": "InstanceIds", "Values": [instance_id]}]
        )
        items = response.get("InstanceInformationList", [])
        if len(items) == 1 and items[0].get("InstanceId") == instance_id:
            if items[0].get("PingStatus") == "Online":
                return
            if items[0].get("PingStatus") not in {"ConnectionLost", "Inactive"}:
                raise SamplerRefusal("SSM_PING_STATUS_UNKNOWN")
        elif len(items) > 1:
            raise SamplerRefusal("SSM_INSTANCE_AMBIGUOUS")
        sleeper(5)
    raise SamplerRefusal("SSM_INSTANCE_NOT_ONLINE")


def parse_pass_summary(stdout: str) -> dict[str, int]:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    matches = [PASS_RE.fullmatch(line) for line in lines]
    matched = [item for item in matches if item is not None]
    if len(lines) != 1 or len(matched) != 1:
        raise SamplerRefusal("SSM_SELF_TEST_OUTPUT_INVALID")
    minimum, peak, total = (int(value) for value in matched[0].groups())
    if not 0 <= minimum <= peak <= total or total == 0:
        raise SamplerRefusal("SSM_SELF_TEST_VALUES_INVALID")
    return {
        "sample_count": 120,
        "gpu_index": 0,
        "minimum_used_mib": minimum,
        "peak_used_mib": peak,
        "total_mib": total,
    }


def run_ssm_self_test(
    ssm: Any,
    *,
    instance_id: str,
    pod_uid: str,
    command_text: str,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    digest = b_proof.DRA_IMAGE.rsplit("@", 1)[1]
    exports = (
        f"export MEDZEN_DRA_POD_UID='{pod_uid}'\n"
        f"export MEDZEN_DRA_IMAGE_DIGEST='{digest}'\n"
    )
    response = ssm.send_command(
        InstanceIds=[instance_id],
        DocumentName="AWS-RunShellScript",
        DocumentVersion="1",
        TimeoutSeconds=180,
        Comment="MedZen B6A 003C-D pre-deploy numeric GPU sampler self-test",
        Parameters={"commands": [exports + command_text]},
        CloudWatchOutputConfig={"CloudWatchOutputEnabled": False},
    )
    command_id = response.get("Command", {}).get("CommandId")
    if re.fullmatch(r"[0-9a-f-]{36}", str(command_id)) is None:
        raise SamplerRefusal("SSM_COMMAND_ID_INVALID")
    for _ in range(75):
        invocation = ssm.get_command_invocation(
            CommandId=command_id, InstanceId=instance_id
        )
        status = invocation.get("Status")
        if status == "Success":
            summary = parse_pass_summary(invocation.get("StandardOutputContent", ""))
            return {"command_id": command_id, **summary}
        if status in {"Cancelled", "Cancelling", "Failed", "TimedOut", "Undeliverable", "Terminated"}:
            raise SamplerRefusal(f"SSM_SELF_TEST_{status.upper()}")
        if status not in {"Pending", "InProgress", "Delayed"}:
            raise SamplerRefusal("SSM_COMMAND_STATUS_UNKNOWN")
        sleeper(3)
    raise SamplerRefusal("SSM_SELF_TEST_TIMEOUT")


def _client() -> Any:
    import boto3

    return boto3.Session(profile_name=PROFILE, region_name=REGION).client("ssm")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kubeconfig", type=Path, required=True)
    parser.add_argument("--receipts-dir", type=Path, required=True)
    args = parser.parse_args()
    store = ReceiptStore(args.receipts_dir, policy_path=POLICY)
    stage = "dra_stable_readiness"
    try:
        store.require_pass("deadline")
        readiness = wait_for_stable_dra(kubeconfig=args.kubeconfig)
        store.persist(stage, "PASS", readiness)
        stage = "sampler_self_test"
        instance_id = instance_for_node(args.kubeconfig, readiness["gpu_node"])
        ssm = _client()
        wait_online(ssm, instance_id)
        result = run_ssm_self_test(
            ssm,
            instance_id=instance_id,
            pod_uid=readiness["dra_pod_uid"],
            command_text=COMMAND.read_text(),
        )
        receipt = store.persist(stage, "PASS", {
            **result,
            "instance_id": instance_id,
            "gpu_node": readiness["gpu_node"],
            "dra_pod_uid": readiness["dra_pod_uid"],
            "dra_image": b_proof.DRA_IMAGE,
            "command_path": "scripts/b6a_003c_d_ssm_sampler.sh",
            "command_sha256": hashlib.sha256(COMMAND.read_bytes()).hexdigest(),
            "execution_context": "ssm_to_gpu_node_crictl_exec_dra_gpus_chroot_driver_root",
            "model_deployed_before_self_test": False,
            "raw_stdout_or_stderr_preserved": False,
        })
        print(json.dumps(receipt, sort_keys=True))
        return 0
    except Exception as exc:
        code = exc.code if isinstance(exc, SamplerRefusal) else type(exc).__name__
        if not store.path(stage).exists():
            store.persist(stage, "REFUSED", {
                "code": code,
                "model_deployed_before_self_test": False,
                "raw_stdout_or_stderr_preserved": False,
            }, dependencies=("deadline",) if stage == "dra_stable_readiness" else None)
        print(json.dumps({"status": "REFUSED", "stage": stage, "code": code}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
