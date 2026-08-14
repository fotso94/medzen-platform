"""Pre-PyTorch proof of the packet's private-endpoint-only network boundary."""

from __future__ import annotations

import ipaddress
import json
import socket
import time
from pathlib import Path
from typing import Any, Callable

from .harness import EvaluationRefusal, write_once


DENIED = (
    ("dl.fbaipublicfiles.com", 443, "meta_public_download"),
    ("example.com", 443, "public_https_control"),
    ("169.254.169.254", 80, "ec2_imds"),
)
POSITIVE_CONVERGENCE_INTERVAL_SECONDS = 5.0
POSITIVE_CONVERGENCE_TIMEOUT_SECONDS = 120.0
CONNECT_TIMEOUT_SECONDS = 3.0

Resolver = Callable[[str, int], list[str]]
Connector = Callable[[str, int, float], None]


def _resolve(host: str, port: int) -> list[str]:
    values: list[str] = []
    for family, socktype, proto, canonical, address in socket.getaddrinfo(
        host, port, type=socket.SOCK_STREAM
    ):
        del family, socktype, proto, canonical
        ip = str(ipaddress.ip_address(address[0]))
        if ip not in values:
            values.append(ip)
    if not values:
        raise socket.gaierror(socket.EAI_NONAME, "hostname returned no addresses")
    return values


def _connect(ip: str, port: int, timeout: float) -> None:
    with socket.create_connection((ip, port), timeout=timeout):
        return


def _elapsed(now: Callable[[], float], started: float) -> float:
    return round(max(0.0, now() - started), 6)


def _safe_error(exc: BaseException) -> dict[str, Any]:
    value = getattr(exc, "errno", None)
    return {
        "exception_class": type(exc).__name__,
        "errno": value if isinstance(value, int) else None,
    }


def _target_attempt(
    host: str,
    port: int,
    *,
    resolver: Resolver,
    connector: Connector,
    now: Callable[[], float],
    require_all_addresses: bool,
) -> dict[str, Any]:
    started = now()
    try:
        resolved = resolver(host, port)
        if not isinstance(resolved, list) or not resolved:
            raise socket.gaierror(socket.EAI_NONAME, "hostname returned no addresses")
        normalized: list[str] = []
        for value in resolved:
            ip = str(ipaddress.ip_address(value))
            if ip not in normalized:
                normalized.append(ip)
    except Exception as exc:
        return {
            "host": host,
            "port": port,
            "resolved_ips": [],
            "address_outcomes": [],
            "status": "RESOLUTION_REFUSED",
            "resolution_error": _safe_error(exc),
            "elapsed_seconds": _elapsed(now, started),
        }

    outcomes: list[dict[str, Any]] = []
    connected = 0
    for ip in normalized:
        address_started = now()
        try:
            connector(ip, port, CONNECT_TIMEOUT_SECONDS)
        except Exception as exc:
            outcomes.append(
                {
                    "ip": ip,
                    "status": "CONNECT_REFUSED",
                    **_safe_error(exc),
                    "elapsed_seconds": _elapsed(now, address_started),
                }
            )
        else:
            connected += 1
            outcomes.append(
                {
                    "ip": ip,
                    "status": "CONNECTED",
                    "exception_class": None,
                    "errno": None,
                    "elapsed_seconds": _elapsed(now, address_started),
                }
            )
            if not require_all_addresses:
                break
    passed = connected == len(normalized) if require_all_addresses else connected > 0
    return {
        "host": host,
        "port": port,
        "resolved_ips": normalized,
        "address_outcomes": outcomes,
        "status": "CONNECTED" if passed else "CONNECT_REFUSED",
        "require_all_addresses": require_all_addresses,
        "elapsed_seconds": _elapsed(now, started),
    }


def _write_refusal(
    receipt_path: Path,
    *,
    reason_code: str,
    reason: str,
    telemetry: dict[str, Any],
) -> None:
    receipt = {
        "schema_version": 2,
        "status": "REFUSED_NETWORK_ISOLATION_PRE_TORCH",
        "reason_code": reason_code,
        "reason": reason,
        "torch_imported": "torch" in __import__("sys").modules,
        "timing_policy": {
            "positive_convergence_interval_seconds": POSITIVE_CONVERGENCE_INTERVAL_SECONDS,
            "positive_convergence_timeout_seconds": POSITIVE_CONVERGENCE_TIMEOUT_SECONDS,
            "per_address_connect_timeout_seconds": CONNECT_TIMEOUT_SECONDS,
        },
        "telemetry": telemetry,
        "contains_credentials_phi_audio_reference_or_prediction": False,
    }
    write_once(receipt_path, receipt)
    raise EvaluationRefusal(f"{reason_code}: {reason}")


def probe_network(
    binding_path: Path,
    receipt_path: Path,
    *,
    resolver: Resolver = _resolve,
    connector: Connector = _connect,
    sleeper: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
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
    if any(not isinstance(host, str) or not host.endswith(".amazonaws.com") for host in allowed):
        raise EvaluationRefusal("allowed endpoint hostname is malformed")
    if "torch" in __import__("sys").modules:
        _write_refusal(
            receipt_path,
            reason_code="TORCH_IMPORTED_BEFORE_NETWORK_GATE",
            reason="torch was imported before the network gate",
            telemetry={"positive_convergence": [], "allowed": {}, "denied": {}},
        )

    first_host = allowed[0]
    convergence_started = monotonic()
    convergence_deadline = convergence_started + POSITIVE_CONVERGENCE_TIMEOUT_SECONDS
    convergence: list[dict[str, Any]] = []
    while True:
        attempt_started_after = _elapsed(monotonic, convergence_started)
        attempt = _target_attempt(
            first_host,
            443,
            resolver=resolver,
            connector=connector,
            now=monotonic,
            require_all_addresses=False,
        )
        convergence.append(
            {
                "attempt": len(convergence) + 1,
                "started_after_seconds": attempt_started_after,
                **attempt,
            }
        )
        if attempt["status"] == "CONNECTED":
            break
        remaining = convergence_deadline - monotonic()
        if remaining <= 0:
            _write_refusal(
                receipt_path,
                reason_code="POSITIVE_NETWORK_CONVERGENCE_TIMEOUT",
                reason=f"allowed endpoint did not become reachable within 120 seconds: {first_host}",
                telemetry={
                    "positive_convergence": convergence,
                    "allowed": {},
                    "denied": {},
                },
            )
        sleeper(min(POSITIVE_CONVERGENCE_INTERVAL_SECONDS, remaining))

    allowed_results: dict[str, Any] = {}
    for host in allowed:
        result = _target_attempt(
            host,
            443,
            resolver=resolver,
            connector=connector,
            now=monotonic,
            require_all_addresses=True,
        )
        allowed_results[host] = result
        if result["status"] != "CONNECTED":
            reason_code = (
                "ALLOWED_ENDPOINT_RESOLUTION_REFUSED"
                if result["status"] == "RESOLUTION_REFUSED"
                else "ALLOWED_ENDPOINT_CONNECT_REFUSED"
            )
            _write_refusal(
                receipt_path,
                reason_code=reason_code,
                reason=f"allowed endpoint battery did not pass: {host}",
                telemetry={
                    "positive_convergence": convergence,
                    "allowed": allowed_results,
                    "denied": {},
                },
            )

    denied_results: dict[str, Any] = {}
    for host, port, label in DENIED:
        result = _target_attempt(
            host,
            port,
            resolver=resolver,
            connector=connector,
            now=monotonic,
            require_all_addresses=False,
        )
        refused = result["status"] != "CONNECTED"
        denied_results[label] = {**result, "status": "REFUSED" if refused else "CONNECTED"}
        if not refused:
            _write_refusal(
                receipt_path,
                reason_code="PROHIBITED_NETWORK_DESTINATION_ACCEPTED",
                reason=f"prohibited network destination accepted: {label}",
                telemetry={
                    "positive_convergence": convergence,
                    "allowed": allowed_results,
                    "denied": denied_results,
                },
            )

    receipt = {
        "schema_version": 2,
        "status": "PASS_NETWORK_ISOLATION_PRE_TORCH",
        "torch_imported": False,
        "timing_policy": {
            "positive_convergence_interval_seconds": POSITIVE_CONVERGENCE_INTERVAL_SECONDS,
            "positive_convergence_timeout_seconds": POSITIVE_CONVERGENCE_TIMEOUT_SECONDS,
            "per_address_connect_timeout_seconds": CONNECT_TIMEOUT_SECONDS,
        },
        "positive_and_negative_proofs": {
            "positive_convergence": convergence,
            "allowed": allowed_results,
            "denied": denied_results,
        },
        "inbound_listener_created": False,
        "contains_credentials_phi_audio_reference_or_prediction": False,
    }
    write_once(receipt_path, receipt)
    return receipt
