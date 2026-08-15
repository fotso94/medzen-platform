from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.asr_base_model_pilot_receipts import ReceiptStore
from scripts.asr_base_model_pilot_fake import build_rehearsal_operations
from scripts.asr_base_model_pilot_runner import AttemptContext, OperationRefusal
from scripts.asr_base_model_pod_lifecycle import (
    audit_waiter_and_finalizer_sites,
    exact_image_in_node_inventory,
    observe_pod,
)


BINDINGS = ROOT / "platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002V.json"
LIVE = ROOT / "platform/evidence/receipts/ASR-BASE-MODEL-2026-002V-A23-LIVE"


def _context(tmp_path: Path, injection: str | None = None):
    bindings = json.loads(BINDINGS.read_bytes())
    operations, state = build_rehearsal_operations(bindings, injection=injection)
    workdir = tmp_path / "live"
    workdir.mkdir()
    context = AttemptContext(
        attempt=23,
        bindings=bindings,
        receipts=ReceiptStore(
            workdir / "receipts",
            packet_sha256="0" * 64,
            authorization_sha256="a" * 64,
        ),
        workdir=workdir,
    )
    operations._save_state(  # noqa: SLF001 - exact lifecycle regression setup
        context,
        {
            "node_name": state.node_name,
            "instance_id": state.instance_id,
            "staging_path": "/var/lib/medzen-asr-eval/attempt-23",
        },
    )
    state.namespaces.add("medzen-asr-eval")
    return bindings, operations, state, context


def test_recorded_pending_shape_is_explicit_and_nonterminal() -> None:
    value = {
        "metadata": {"name": "asr-eval-dns-control"},
        "status": {
            "phase": "Pending",
            "containerStatuses": [
                {
                    "name": "dns-control",
                    "ready": False,
                    "restartCount": 0,
                    "state": {
                        "waiting": {
                            "reason": "ContainerCreating",
                            "message": "Pulling image",
                        }
                    },
                }
            ],
        },
    }
    observed = observe_pod(value)
    assert observed["phase"] == "Pending"
    assert observed["terminal"] is False
    assert observed["containers"][0]["state"]["reason"] == "ContainerCreating"


def test_exact_digest_prepull_executes_pending_inventory_and_absence_branches(
    tmp_path: Path,
) -> None:
    bindings, operations, state, context = _context(tmp_path)
    result = operations._image_prepull_qualification(context)  # noqa: SLF001
    expected = (
        "558069890522.dkr.ecr.eu-central-1.amazonaws.com/"
        "medzen-asr-eval-runtime@"
        + bindings["image"]["linux_amd64_digest"]
    )
    assert result["status"] == "PASS_EXACT_IMAGE_PREPULL_QUALIFICATION"
    assert result["image"] == expected
    assert result["terminal_observation"]["phase_sequence"] == [
        "Pending",
        "Succeeded",
    ]
    assert result["terminal_observation"]["sleep_branch_executed"] is True
    assert result["inventory_observation_sequence"] == [
        "ABSENT",
        "PRESENT",
        "PRESENT",
    ]
    assert state.pod_absence_observation_sequence["asr-eval-image-prepull"] == [
        "PRESENT",
        "ABSENT",
        "ABSENT",
    ]
    assert result["pod_cleanup"]["delete_waited_server_side"] is False


def test_node_inventory_requires_the_exact_digest_name() -> None:
    expected = "registry.example/repository@sha256:" + "1" * 64
    result = exact_image_in_node_inventory(
        {
            "metadata": {"name": "gpu-node"},
            "status": {
                "images": [
                    {
                        "names": [expected, "registry.example/repository:tag"],
                        "sizeBytes": 10,
                    }
                ]
            },
        },
        expected_node="gpu-node",
        expected_image=expected,
    )
    assert result["exact_image_present"] is True
    assert result["matching_name_sha256"] is not None


def test_primary_dns_refusal_survives_delete_timeout(tmp_path: Path) -> None:
    _, operations, state, context = _context(
        tmp_path, injection="dns_primary_delete_timeout"
    )
    (context.workdir / "workload.yaml").write_bytes((LIVE / "workload.yaml").read_bytes())
    (context.workdir / "network-binding.json").write_bytes(
        (LIVE / "network-binding.json").read_bytes()
    )
    with pytest.raises(OperationRefusal) as captured:
        operations._dns_resolution_consistency_gate(context)  # noqa: SLF001
    assert captured.value.reason_code == "DNS_RESOLVED_IP_OUTSIDE_ALLOWLIST"
    diagnostic = json.loads(
        (
            context.workdir
            / "asr-eval-dns-control-secondary-cleanup-diagnostic.json"
        ).read_bytes()
    )
    assert diagnostic["primary_reason_code"] == "DNS_RESOLVED_IP_OUTSIDE_ALLOWLIST"
    assert diagnostic["cleanup"]["reason_code"] == "STAGE_POD_DELETE_TIMEOUT"
    assert diagnostic["status"] == (
        "PRIMARY_EXCEPTION_RETAINED_WITH_SECONDARY_CLEANUP_DIAGNOSTIC"
    )
    assert state.pod_terminal_observation_sequence["asr-eval-dns-control"] == [
        "Pending",
        "Succeeded",
    ]


def test_systemic_waiter_finalizer_audit_is_enumerated() -> None:
    result = audit_waiter_and_finalizer_sites(ROOT)
    assert result["status"] == "PASS_SYSTEMIC_WAITER_FINALIZER_AUDIT"
    assert result["waiter_site_count"] >= 13
    assert result["finally_site_count"] >= 1
    assert result["stage_local_blocking_pod_deletes"] == 0
    assert result["undefined_sleep_calls"] == 0
    assert result["rehearsal_waiters_with_instant_terminal_only"] == 0
    assert result["remote_observation_site_count"] == 10
    assert result["remote_asynchronous_one_shot_success_gates"] == 0
    assert result["remote_unclassified_ssm_call_sites"] == 0
