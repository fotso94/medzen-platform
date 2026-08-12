"""Pre-PyTorch proof of the packet's private-endpoint-only network boundary."""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any, Callable

from .harness import EvaluationRefusal, write_once


DENIED = (
    ("dl.fbaipublicfiles.com", 443, "meta_public_download"),
    ("example.com", 443, "public_https_control"),
    ("169.254.169.254", 80, "ec2_imds"),
)


def _connect(host: str, port: int, timeout: float = 2.0) -> None:
    with socket.create_connection((host, port), timeout=timeout):
        return


def probe_network(
    binding_path: Path,
    receipt_path: Path,
    *,
    connector: Callable[[str, int, float], None] = _connect,
) -> dict[str, Any]:
    try:
        binding = json.loads(binding_path.read_bytes())
    except Exception as exc:
        raise EvaluationRefusal("network binding is absent or malformed") from exc
    if binding.get("schema_version") != 1 or binding.get("classification") != "OFFLINE_EVALUATION_ONLY":
        raise EvaluationRefusal("network binding classification differs")
    allowed = binding.get("allowed_tcp_443_hosts")
    if not isinstance(allowed, list) or len(allowed) < 3 or len(set(allowed)) != len(allowed):
        raise EvaluationRefusal("private endpoint host bindings are incomplete")
    results = {"allowed": {}, "denied": {}}
    for host in allowed:
        if not isinstance(host, str) or not host.endswith(".amazonaws.com"):
            raise EvaluationRefusal("allowed endpoint hostname is malformed")
        try:
            connector(host, 443, 3.0)
        except Exception as exc:
            raise EvaluationRefusal(f"allowed endpoint is unreachable: {host}") from exc
        results["allowed"][host] = "CONNECTED"
    for host, port, label in DENIED:
        try:
            connector(host, port, 2.0)
        except (OSError, TimeoutError):
            results["denied"][label] = "REFUSED"
        else:
            raise EvaluationRefusal(f"prohibited network destination accepted: {label}")
    receipt = {
        "status": "PASS_NETWORK_ISOLATION_PRE_TORCH",
        "torch_imported": "torch" in __import__("sys").modules,
        "positive_and_negative_proofs": results,
        "inbound_listener_created": False,
    }
    if receipt["torch_imported"]:
        raise EvaluationRefusal("torch was imported before the network gate")
    write_once(receipt_path, receipt)
    return receipt
