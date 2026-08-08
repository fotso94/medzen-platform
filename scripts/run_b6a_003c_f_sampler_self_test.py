#!/usr/bin/env python3
"""Run the proven sampler while tolerating bounded SSM invocation discovery."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.runtime_receipts_v2 import ReceiptStore
from scripts import run_b6a_003c_e_sampler_self_test as base


POLICY = ROOT / "platform/runtime-receipt-policy-v2.yaml"
COMMAND = ROOT / "scripts/b6a_003c_e_ssm_sampler.sh"
MAXIMUM_POLLS = 60
POLL_SECONDS = 3


def _client_error_code(error: Exception) -> str | None:
    response = getattr(error, "response", None)
    if not isinstance(response, dict):
        return None
    detail = response.get("Error")
    if not isinstance(detail, dict):
        return None
    code = detail.get("Code")
    return code if isinstance(code, str) and code else None


def run_ssm_self_test(
    ssm: Any,
    *,
    instance_id: str,
    pod_uid: str,
    command_text: str,
    sleeper: Callable[[float], None] = time.sleep,
    maximum_polls: int = MAXIMUM_POLLS,
) -> dict[str, Any]:
    digest = base.b_proof.DRA_IMAGE.rsplit("@", 1)[1]
    exports = (
        f"export MEDZEN_DRA_POD_UID='{pod_uid}'\n"
        f"export MEDZEN_DRA_IMAGE_DIGEST='{digest}'\n"
    )
    command = (
        exports
        + "/usr/bin/bash -s <<'MEDZEN_SAMPLER_SCRIPT'\n"
        + command_text.rstrip()
        + "\nMEDZEN_SAMPLER_SCRIPT\n"
    )
    response = ssm.send_command(
        InstanceIds=[instance_id],
        DocumentName="AWS-RunShellScript",
        DocumentVersion="1",
        TimeoutSeconds=180,
        Comment="MedZen B6A 003C-F pre-deploy numeric GPU sampler self-test",
        Parameters={"commands": [command]},
        CloudWatchOutputConfig={"CloudWatchOutputEnabled": False},
    )
    command_id = response.get("Command", {}).get("CommandId")
    if base.re.fullmatch(r"[0-9a-f-]{36}", str(command_id)) is None:
        raise base.SamplerRefusal("SSM_COMMAND_ID_INVALID")
    if not 1 <= maximum_polls <= MAXIMUM_POLLS:
        raise base.SamplerCommandRefusal(
            "SSM_POLL_LIMIT_INVALID",
            command_id=command_id,
            status="InvalidPollLimit",
            stdout="",
            stderr="",
        )

    for _ in range(maximum_polls):
        try:
            invocation = ssm.get_command_invocation(
                CommandId=command_id, InstanceId=instance_id
            )
        except Exception as exc:
            code = _client_error_code(exc)
            if code == "InvocationDoesNotExist":
                sleeper(POLL_SECONDS)
                continue
            raise base.SamplerCommandRefusal(
                "SSM_INVOCATION_LOOKUP_FAILED",
                command_id=command_id,
                status=f"LookupError:{code or type(exc).__name__}",
                stdout="",
                stderr="",
            ) from exc

        status = invocation.get("Status")
        stdout = invocation.get("StandardOutputContent", "")
        stderr = invocation.get("StandardErrorContent", "")
        if status == "Success":
            if stderr.strip():
                raise base.SamplerCommandRefusal(
                    "SSM_SELF_TEST_STDERR_NONEMPTY",
                    command_id=command_id,
                    status=status,
                    stdout=stdout,
                    stderr=stderr,
                )
            try:
                summary = base.parse_pass_summary(stdout)
            except base.SamplerRefusal as exc:
                raise base.SamplerCommandRefusal(
                    exc.code,
                    command_id=command_id,
                    status=status,
                    stdout=stdout,
                    stderr=stderr,
                ) from exc
            return {
                "command_id": command_id,
                "raw_stdout": stdout,
                "raw_stderr": stderr,
                **summary,
            }
        if status in {
            "Cancelled",
            "Cancelling",
            "Failed",
            "TimedOut",
            "Undeliverable",
            "Terminated",
        }:
            raise base.SamplerCommandRefusal(
                f"SSM_SELF_TEST_{status.upper()}",
                command_id=command_id,
                status=status,
                stdout=stdout,
                stderr=stderr,
            )
        if status not in {"Pending", "InProgress", "Delayed"}:
            raise base.SamplerCommandRefusal(
                "SSM_COMMAND_STATUS_UNKNOWN",
                command_id=command_id,
                status=str(status),
                stdout=stdout,
                stderr=stderr,
            )
        sleeper(POLL_SECONDS)

    raise base.SamplerCommandRefusal(
        "SSM_INVOCATION_DISCOVERY_OR_EXECUTION_TIMEOUT",
        command_id=command_id,
        status="TimedOut",
        stdout="",
        stderr="",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kubeconfig", type=Path, required=True)
    parser.add_argument("--receipts-dir", type=Path, required=True)
    args = parser.parse_args()
    store = ReceiptStore(args.receipts_dir, policy_path=POLICY)
    stage = "dra_stable_readiness"
    try:
        store.require_pass("deadline")
        readiness = base.wait_for_stable_dra(kubeconfig=args.kubeconfig)
        store.persist(stage, "PASS", readiness)
        stage = "sampler_self_test"
        instance_id = base.instance_for_node(args.kubeconfig, readiness["gpu_node"])
        ssm = base._client()
        base.wait_online(ssm, instance_id)
        result = run_ssm_self_test(
            ssm,
            instance_id=instance_id,
            pod_uid=readiness["dra_pod_uid"],
            command_text=COMMAND.read_text(),
        )
        receipt = store.persist(
            stage,
            "PASS",
            {
                **result,
                "pre_artifact_facts": {
                    "model_artifact_present_on_node": False,
                    "audio_artifact_present_on_node": False,
                    "model_or_audio_workload_applied": False,
                },
                "instance_id": instance_id,
                "gpu_node": readiness["gpu_node"],
                "dra_pod_uid": readiness["dra_pod_uid"],
                "dra_image": base.b_proof.DRA_IMAGE,
                "command_path": "scripts/b6a_003c_e_ssm_sampler.sh",
                "command_sha256": hashlib.sha256(COMMAND.read_bytes()).hexdigest(),
                "execution_context": "ssm_to_gpu_node_nerdctl_exec_dra_gpus_chroot_driver_root",
                "invocation_discovery_policy": "retry_InvocationDoesNotExist_60x3s",
                "model_deployed_before_self_test": False,
                "raw_stdout_or_stderr_preserved": True,
            },
        )
        print(json.dumps(receipt, sort_keys=True))
        return 0
    except Exception as exc:
        code = exc.code if isinstance(exc, base.SamplerRefusal) else type(exc).__name__
        if not store.path(stage).exists():
            payload = {
                "code": code,
                "model_deployed_before_self_test": False,
                "raw_stdout_or_stderr_preserved": isinstance(
                    exc, base.SamplerCommandRefusal
                ),
            }
            if isinstance(exc, base.SamplerCommandRefusal):
                payload.update(
                    {
                        "command_id": exc.command_id,
                        "command_status": exc.status,
                        "command_path": "scripts/b6a_003c_e_ssm_sampler.sh",
                        "command_sha256": hashlib.sha256(COMMAND.read_bytes()).hexdigest(),
                        "pre_artifact_facts": {
                            "model_artifact_present_on_node": False,
                            "audio_artifact_present_on_node": False,
                            "model_or_audio_workload_applied": False,
                        },
                        "raw_stdout": exc.stdout,
                        "raw_stderr": exc.stderr,
                    }
                )
            store.persist(
                stage,
                "REFUSED",
                payload,
                dependencies=("deadline",) if stage == "dra_stable_readiness" else None,
            )
        print(json.dumps({"status": "REFUSED", "stage": stage, "code": code}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
