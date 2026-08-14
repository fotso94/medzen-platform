from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.asr_base_model_pilot_receipts import ReceiptStore, canonical_json
from scripts.asr_base_model_pilot_dns import (
    DnsAlignmentRefusal,
    VPC_DNS_RESOLVER,
    validate_dns_resolution_receipt,
    validate_pod_dns_fields,
    workload_egress_allowlist,
)
from scripts.asr_base_model_pilot_fake import build_rehearsal_operations
from scripts.asr_base_model_pilot_k8s import render, verify
from scripts.asr_base_model_pilot_runner import AttemptContext, OperationRefusal


BINDINGS = ROOT / "platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002U.json"


def bindings() -> dict:
    return json.loads(BINDINGS.read_bytes())


def rendered() -> str:
    return render(
        bindings(),
        ["10.0.1.7", "10.0.1.10"],
        ["16.12.24.0/21"],
        23,
    )


def test_pilot_job_uses_vpc_resolver_without_cluster_dns_dependency() -> None:
    body = rendered()
    documents = [value for value in yaml.safe_load_all(body) if value]
    pod = documents[-1]["spec"]["template"]["spec"]
    assert validate_pod_dns_fields(pod) == {
        "status": "PASS_POD_DNS_ALIGNED_TO_VPC_RESOLVER",
        "dns_policy": "None",
        "nameservers": [VPC_DNS_RESOLVER],
        "cluster_dns_dependency": False,
    }
    assert verify(body, bindings()["image"]["linux_amd64_digest"], 23)[
        "pod_dns"
    ]["cluster_dns_dependency"] is False


def test_rendered_allowlist_binds_vpc_dns_and_tcp_destinations() -> None:
    assert workload_egress_allowlist(rendered()) == {
        "tcp_443_cidrs": ["10.0.1.10/32", "10.0.1.7/32", "16.12.24.0/21"],
        "dns_cidrs": ["172.31.0.2/32"],
    }


def test_resolution_gate_records_only_addresses_inside_rendered_allowlist() -> None:
    receipt = {
        "schema_version": 1,
        "status": "PASS_VPC_RESOLVER_CONSISTENCY",
        "effective_nameservers": [VPC_DNS_RESOLVER],
        "resolved_ips": {
            "api.example": ["10.0.1.7"],
            "dkr.example": ["10.0.1.10"],
            "s3.example": ["16.12.24.1"],
        },
        "torch_imported": False,
    }
    value = validate_dns_resolution_receipt(
        receipt,
        expected_hosts=["api.example", "dkr.example", "s3.example"],
        allowed_tcp_443_cidrs=[
            "10.0.1.7/32",
            "10.0.1.10/32",
            "16.12.24.0/21",
        ],
    )
    assert value["status"] == "PASS_DNS_RESOLUTION_CONSISTENCY_GATE"
    assert value["resolved_ips"]["s3.example"] == ["16.12.24.1"]


def test_resolution_gate_refuses_if_torch_was_imported() -> None:
    with pytest.raises(DnsAlignmentRefusal) as captured:
        validate_dns_resolution_receipt(
            {
                "schema_version": 1,
                "status": "PASS_VPC_RESOLVER_CONSISTENCY",
                "effective_nameservers": [VPC_DNS_RESOLVER],
                "resolved_ips": {"api.example": ["10.0.1.7"]},
                "torch_imported": True,
            },
            expected_hosts=["api.example"],
            allowed_tcp_443_cidrs=["10.0.1.7/32"],
        )
    assert captured.value.reason_code == "DNS_CONTROL_TORCH_IMPORTED"


@pytest.mark.parametrize(
    ("receipt", "reason_code"),
    [
        (
            {
                "schema_version": 1,
                "status": "REFUSED",
                "reason_code": "DNS_RESOLVER_UNREACHABLE",
                "effective_nameservers": [VPC_DNS_RESOLVER],
            },
            "DNS_RESOLVER_UNREACHABLE",
        ),
        (
            {
                "schema_version": 1,
                "status": "PASS_VPC_RESOLVER_CONSISTENCY",
                "effective_nameservers": [VPC_DNS_RESOLVER],
                "resolved_ips": {"api.example": ["203.0.113.10"]},
                "torch_imported": False,
            },
            "DNS_RESOLVED_IP_OUTSIDE_ALLOWLIST",
        ),
    ],
)
def test_resolution_gate_refuses_unreachable_or_outside_allowlist(
    receipt: dict, reason_code: str
) -> None:
    with pytest.raises(DnsAlignmentRefusal) as captured:
        validate_dns_resolution_receipt(
            receipt,
            expected_hosts=["api.example"],
            allowed_tcp_443_cidrs=["10.0.1.7/32"],
        )
    assert captured.value.reason_code == reason_code


def _context(tmp_path: Path, injection: str | None) -> tuple[object, object, AttemptContext]:
    value = bindings()
    operations, state = build_rehearsal_operations(value, injection=injection)
    workdir = tmp_path / (injection or "pass")
    workdir.mkdir(parents=True)
    body = render(
        value,
        [f"10.0.1.{number}" for number in range(7, 13)],
        ["16.12.24.0/21"],
        23,
    )
    (workdir / "workload.yaml").write_text(body, encoding="utf-8")
    (workdir / "network-binding.json").write_bytes(canonical_json({
        "schema_version": 1,
        "classification": "OFFLINE_EVALUATION_ONLY",
        "allowed_tcp_443_hosts": [
            "api.ecr.eu-central-1.amazonaws.com",
            "558069890522.dkr.ecr.eu-central-1.amazonaws.com",
            "medzen-speech.s3.eu-central-1.amazonaws.com",
        ],
    }))
    context = AttemptContext(
        attempt=23,
        bindings=value,
        receipts=ReceiptStore(
            workdir / "receipts",
            packet_sha256="0" * 64,
            authorization_sha256="a" * 64,
        ),
        workdir=workdir,
    )
    return operations, state, context


def test_live_dns_gate_uses_actual_rendered_control_pod_and_records_pass(
    tmp_path: Path,
) -> None:
    operations, state, context = _context(tmp_path, None)
    result = operations._dns_resolution_consistency_gate(context)
    assert result["status"] == "PASS_DNS_RESOLUTION_CONSISTENCY_GATE"
    assert result["resolved_ips"][
        "api.ecr.eu-central-1.amazonaws.com"
    ] == ["10.0.1.7"]
    assert state.dns_control_spec is not None
    assert validate_pod_dns_fields(state.dns_control_spec)["status"].startswith(
        "PASS_"
    )
    assert (context.workdir / "dns-consistency-gate.json").is_file()


@pytest.mark.parametrize(
    ("injection", "reason_code"),
    [
        ("dns_resolver_unreachable", "DNS_RESOLVER_UNREACHABLE"),
        (
            "dns_resolved_ip_outside_allowlist",
            "DNS_RESOLVED_IP_OUTSIDE_ALLOWLIST",
        ),
    ],
)
def test_live_dns_gate_rehearses_both_required_refusals(
    tmp_path: Path, injection: str, reason_code: str
) -> None:
    operations, state, context = _context(tmp_path, injection)
    with pytest.raises(OperationRefusal) as captured:
        operations._dns_resolution_consistency_gate(context)
    assert captured.value.reason_code == reason_code
    assert captured.value.outcome == "BLOCKED_NETWORK_ISOLATION"
    assert state.dns_control_spec is not None
