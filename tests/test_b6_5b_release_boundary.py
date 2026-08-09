from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load(relative: str):
    return json.loads((ROOT / relative).read_bytes())


def test_network_design_has_one_exact_security_group_source_and_no_cidr():
    discovery = load("platform/evidence/B6-5B-NETWORK-DISCOVERY-2026-001.json")
    design = load("platform/designs/B6-5B-ORCHESTRATOR-ALB-BOUNDARY-2026-001.json")
    assert discovery["mutation_performed"] is False
    assert discovery["selected_backend_source"]["security_group_id"] == (
        "sg-0a83abae6ab954543"
    )
    assert discovery["selected_backend_source"][
        "backend_owner_confirmation_required_before_apply"
    ] is True
    ingress = design["future_packet_created_resource"]["ingress"]
    assert ingress == [{
        "protocol": "tcp",
        "from_port": 80,
        "to_port": 80,
        "source_security_group_id": "sg-0a83abae6ab954543",
        "cidr_sources": [],
    }]
    assert design["ingress_design"]["scheme"] == "internal"
    assert design["ingress_design"]["backend_service"] == "speech-orchestrator"
    assert design["ingress_design"]["public_or_internet_facing_route"] is False
    assert set(design["service_exposure"].values()) == {
        "INTERNAL_ALB_TO_CLUSTER_IP",
        "CLUSTER_IP_ONLY",
    }
    assert sum(
        value == "INTERNAL_ALB_TO_CLUSTER_IP"
        for value in design["service_exposure"].values()
    ) == 1


def test_generated_dependency_services_remain_cluster_ip_only():
    for service in ("asr-runtime", "rag-index", "llm-gateway", "tts-gateway"):
        manifest = (ROOT / f"platform/k8s/base/{service}.yaml").read_text()
        assert "type: ClusterIP" in manifest
        for forbidden in ("type: LoadBalancer", "type: NodePort", "kind: Ingress"):
            assert forbidden not in manifest


def test_cost_revision_closes_the_reservation_without_calling_it_actual_spend():
    previous_path = ROOT / "platform/finance/COST-REGISTRY-2026-001.json"
    current = load("platform/finance/COST-REGISTRY-2026-002.json")
    assert current["supersedes"]["sha256"] == hashlib.sha256(
        previous_path.read_bytes()
    ).hexdigest()
    summary = current["guardrail_summary"]
    assert summary["recognized_committed_guardrail_usd"] == 62.5288
    assert summary["active_reservations_usd"] == 0.0
    assert summary["guardrail_headroom_after_reservations_usd"] == 237.4712
    assert summary["actual_project_spend"] == "NOT_FULLY_RECONCILED"
    serving = next(
        item for item in current["allocations"]
        if item["allocation_id"] == "B6A-SERVING-PROOF"
    )
    assert serving["recognized_committed_usd"] == 15.0
    assert serving["active_reservation_usd"] == 0.0
    assert serving["conservative_observed_gpu_cost_usd"] == 0.9401
    assert serving["unattributed_guardrail_margin_usd"] == 14.0599
    assert current["controls"]["current_active_billable_reservations"] == 0


def test_every_current_allocation_has_the_revision_two_tag_set():
    current = load("platform/finance/COST-REGISTRY-2026-002.json")
    required = set(current["allocation_tag_standard"]["required_keys"])
    for allocation in current["allocations"]:
        assert set(allocation["allocation_tags"]) == required
        assert allocation["allocation_tags"]["BudgetRegistry"] == (
            "COST-REGISTRY-2026-002"
        )
