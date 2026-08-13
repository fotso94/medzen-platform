#!/usr/bin/env python3
"""Bind live-node commands to immutable, successful historical evidence.

The sampler command is not redesigned here.  Its in-container argv is copied
from the B6A script whose bytes are bound by the successful live receipt.  The
validator proves the receipt, script and executor argv agree before a run may
create its deadline action.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from pathlib import Path
from typing import Any


B6A_SAMPLER_SCRIPT = Path("scripts/b6a_003c_e_ssm_sampler.sh")
B6A_SAMPLER_RECEIPT = Path(
    "platform/evidence/receipts/B6A-2026-003C-F-LIVE/sampler_self_test.json"
)
B6A_SAMPLER_SCRIPT_SHA256 = (
    "b6aa0e0621fca7fc6ee9e9a2bb9f59ff543efbb71b06a35e5497919d8a573d96"
)
B6A_SAMPLER_RECEIPT_SHA256 = (
    "8848c206ecbf459e5e0ffd754352b8eb3086d0b1a750e40c471f890ad8cebde1"
)

# This is the exact in-container argv from the receipt-bound B6A script.  The
# executor may add only its kubectl transport and the 120-sample loop around
# this argv; it may not re-derive or shorten the invocation.
B6A_PROVEN_NVIDIA_SMI_ARGV = (
    "/busybox/chroot",
    "/driver-root",
    "/usr/bin/nvidia-smi",
    "--query-gpu=index,memory.used,memory.total",
    "--format=csv,noheader,nounits",
)
B6A_PROVEN_NVIDIA_SMI_COMMAND = " ".join(B6A_PROVEN_NVIDIA_SMI_ARGV)


class ProvenCommandRefusal(RuntimeError):
    def __init__(self, reason_code: str, detail: str):
        super().__init__(detail)
        self.reason_code = reason_code
        self.detail = detail


def _sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def canonical_argv_bytes(argv: tuple[str, ...] | list[str]) -> bytes:
    """Encode argv boundaries unambiguously for byte-level comparison."""
    return b"\0".join(item.encode("utf-8") for item in argv)


def canonical_argv_sha256(argv: tuple[str, ...] | list[str]) -> str:
    return _sha256(canonical_argv_bytes(argv))


def _historical_sampler_argv(script: bytes) -> tuple[str, ...]:
    text = script.decode("utf-8")
    match = re.search(
        r"/busybox/chroot\s+/driver-root\s+/usr/bin/nvidia-smi\s+"
        r"--query-gpu=index,memory\.used,memory\.total\s+"
        r"--format=csv,noheader,nounits",
        text.replace("\\\n", " "),
    )
    if match is None:
        raise ProvenCommandRefusal(
            "PROVEN_SAMPLER_INVOCATION_ABSENT",
            "the receipt-bound B6A script no longer contains its proven invocation",
        )
    if len(re.findall(r"/busybox/chroot\s+/driver-root\s+/usr/bin/nvidia-smi", text.replace("\\\n", " "))) != 1:
        raise ProvenCommandRefusal(
            "PROVEN_SAMPLER_INVOCATION_AMBIGUOUS",
            "the receipt-bound B6A script contains an ambiguous sampler invocation",
        )
    return tuple(shlex.split(match.group(0)))


def validate_proven_command_bindings(
    root: Path,
    binding: Any,
) -> dict[str, Any]:
    """Fail closed unless live argv equals the receipt-bound historical argv."""
    if not isinstance(binding, dict):
        raise ProvenCommandRefusal(
            "PROVEN_COMMAND_BINDING_ABSENT",
            "the historical live-node command binding is absent",
        )
    sampler = binding.get("sampler")
    if not isinstance(sampler, dict):
        raise ProvenCommandRefusal(
            "PROVEN_SAMPLER_BINDING_ABSENT",
            "the historical sampler binding is absent",
        )
    expected = {
        "script_path": str(B6A_SAMPLER_SCRIPT),
        "script_sha256": B6A_SAMPLER_SCRIPT_SHA256,
        "receipt_path": str(B6A_SAMPLER_RECEIPT),
        "receipt_sha256": B6A_SAMPLER_RECEIPT_SHA256,
        "canonical_inner_argv": list(B6A_PROVEN_NVIDIA_SMI_ARGV),
        "canonical_inner_argv_sha256": canonical_argv_sha256(
            B6A_PROVEN_NVIDIA_SMI_ARGV
        ),
    }
    if any(sampler.get(key) != value for key, value in expected.items()):
        raise ProvenCommandRefusal(
            "PROVEN_SAMPLER_BINDING_DIFFERS",
            "the sampler binding differs from the immutable B6A proof",
        )
    script_path = root / B6A_SAMPLER_SCRIPT
    receipt_path = root / B6A_SAMPLER_RECEIPT
    if not script_path.is_file() or not receipt_path.is_file():
        raise ProvenCommandRefusal(
            "PROVEN_SAMPLER_EVIDENCE_ABSENT",
            "the B6A sampler script or successful receipt is absent",
        )
    script = script_path.read_bytes()
    receipt_body = receipt_path.read_bytes()
    if _sha256(script) != B6A_SAMPLER_SCRIPT_SHA256:
        raise ProvenCommandRefusal(
            "PROVEN_SAMPLER_SCRIPT_HASH_DIFFERS",
            "the B6A sampler script hash differs",
        )
    if _sha256(receipt_body) != B6A_SAMPLER_RECEIPT_SHA256:
        raise ProvenCommandRefusal(
            "PROVEN_SAMPLER_RECEIPT_HASH_DIFFERS",
            "the B6A sampler receipt hash differs",
        )
    receipt = json.loads(receipt_body)
    payload = receipt.get("payload", {})
    if (
        receipt.get("status") != "PASS"
        or receipt.get("stage") != "sampler_self_test"
        or payload.get("command_path") != str(B6A_SAMPLER_SCRIPT)
        or payload.get("command_sha256") != B6A_SAMPLER_SCRIPT_SHA256
        or payload.get("execution_context")
        != "ssm_to_gpu_node_nerdctl_exec_dra_gpus_chroot_driver_root"
        or payload.get("sample_count") != 120
    ):
        raise ProvenCommandRefusal(
            "PROVEN_SAMPLER_RECEIPT_PAYLOAD_DIFFERS",
            "the B6A successful sampler receipt payload differs",
        )
    historical_argv = _historical_sampler_argv(script)
    if canonical_argv_bytes(historical_argv) != canonical_argv_bytes(
        B6A_PROVEN_NVIDIA_SMI_ARGV
    ):
        raise ProvenCommandRefusal(
            "PROVEN_SAMPLER_EXECUTOR_ARGV_DIFFERS",
            "the executor sampler argv is not byte-identical to the historical argv",
        )
    return {
        "status": "PASS_PROVEN_LIVE_NODE_COMMAND_BINDINGS",
        "sampler": {
            "status": "PASS_BYTE_IDENTICAL_HISTORICAL_ARGV",
            "script_path": str(B6A_SAMPLER_SCRIPT),
            "script_sha256": _sha256(script),
            "receipt_path": str(B6A_SAMPLER_RECEIPT),
            "receipt_sha256": _sha256(receipt_body),
            "canonical_inner_argv": list(historical_argv),
            "canonical_inner_argv_sha256": canonical_argv_sha256(
                historical_argv
            ),
            "sample_count": 120,
        },
        "node_local_input_stage": "NOT_HISTORICALLY_PROVEN",
        "pilot_workload": "NOT_HISTORICALLY_PROVEN",
        "unproven_commands_reclassified_as_proven": False,
    }


def sampler_shell_command() -> str:
    """Wrap the proven argv in the historically proven 120-sample behavior."""
    command = shlex.join(B6A_PROVEN_NVIDIA_SMI_ARGV)
    return (
        "set -u; i=0; "
        "while [ \"$i\" -lt 120 ]; do "
        f"{command}; "
        "rc=$?; [ \"$rc\" -eq 0 ] || exit \"$rc\"; "
        "i=$((i+1)); [ \"$i\" -eq 120 ] || /busybox/sleep 1; "
        "done"
    )
