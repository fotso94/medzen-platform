from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
PACKET = ROOT / (
    "platform/decisions/"
    "B6-AWS-CHANGE-PACKET-2026-032A-websocket-local-qualified.md"
)
SCAN = ROOT / "platform/evidence/B6-PACKET-2026-031-SCAN-RESULT.json"
FILE_RECEIPT = ROOT / "platform/evidence/receipts/B6-2026-030A-A2-LIVE/file_proof.json"
ATTEMPT_1 = ROOT / (
    "platform/evidence/"
    "B6-PACKET-2026-032-ATTEMPT-1-TERMINAL-WEBSOCKET-FRAME-REFUSAL.json"
)
LOCAL_CONVERSATION = ROOT / (
    "platform/evidence/b6-websocket-runtime/"
    "medzen-orchestrator.full-conversation.json"
)
COLD_RECEIPT = ROOT / "platform/evidence/receipts/B6-2026-032A-COLD/cold_rehearsal.json"
NEW = "sha256:a3bd7170dbef4541ff6286324974a79d0b0da2287dcdcaf8f77a20654c7befed"
OLD = "sha256:fa2cccdf9891c080fcc1eb408a325e8afbd623e4f89469ea228ddf166dad62aa"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_packet_binds_scan_pass_file_pass_and_exact_allowance():
    packet = PACKET.read_text()
    assert _sha(SCAN) in packet
    assert _sha(FILE_RECEIPT) in packet
    assert _sha(ATTEMPT_1) in packet
    assert _sha(LOCAL_CONVERSATION) in packet
    assert NEW in packet
    assert "must not be rerun" in packet
    assert "single unused non-transferable 4,500-second attempt" in packet
    assert "continuity attempt 2" in packet
    assert "New reservation | `$0.00`" in packet
    assert "Any further attempt" in packet


def test_successor_stages_exclude_file_and_keep_only_remaining_live_proofs():
    from scripts.b6_remaining_runner import (
        REMAINING_EXECUTION_STAGES,
        REMAINING_WINDOW_STAGES,
    )

    assert "file_proof" not in REMAINING_EXECUTION_STAGES
    assert REMAINING_EXECUTION_STAGES[-4:] == (
        "websocket_proof",
        "cancellation_proof",
        "failure_drills",
        "isolation_proof",
    )
    assert REMAINING_WINDOW_STAGES[-1] == "cleanup"
    assert len(REMAINING_WINDOW_STAGES) == 22
    operations = (ROOT / "scripts/b6_remaining_operations.sh").read_text()
    assert "stage_file_proof" not in operations
    assert "file_proof)" not in operations


def test_all_successor_digest_projections_use_scan_passed_child():
    for relative in (
        "platform/k8s/b6-6/remaining-proofs-window.yaml",
        "scripts/b6_remaining_operations.sh",
        "scripts/b6_remaining_pre_endpoint_images.py",
    ):
        text = (ROOT / relative).read_text()
        assert NEW in text
        assert OLD not in text
    scan = json.loads(SCAN.read_bytes())
    assert scan["subject"]["child_manifest_digest"] == NEW
    assert scan["outcome"] == "PASS_SCAN_ONLY"


def test_manifest_is_digest_pinned_and_contains_exact_seven_workload_pods():
    path = ROOT / "platform/k8s/b6-6/remaining-proofs-window.yaml"
    documents = [item for item in yaml.safe_load_all(path.read_text()) if item]
    images = []
    for item in documents:
        template = item.get("spec", {}).get("template", {}).get("spec", {})
        images.extend(
            container.get("image", "")
            for container in (*template.get("initContainers", []), *template.get("containers", []))
        )
    assert images
    assert all("@sha256:" in image and ":PLACEHOLDER" not in image for image in images)
    assert any(image.endswith(NEW) for image in images)


def test_manifest_renderer_is_separate_from_historical_projection():
    from scripts.b6_remaining_manifest_slice import MANIFEST, render

    assert MANIFEST.name == "remaining-proofs-window.yaml"
    pre = list(yaml.safe_load_all(render("pre-endpoint")))
    ingress = list(yaml.safe_load_all(render("ingress")))
    assert all(item.get("kind") != "Ingress" for item in pre if item)
    assert [item.get("kind") for item in ingress if item] == ["Ingress"]


def test_historical_cold_rehearsal_is_immutable_and_refuses_source_drift(tmp_path):
    assert _sha(COLD_RECEIPT) == (
        "84d5c16a8540502554365e3cba9639e60b24866fd4bb3328fc57a9194d8b2401"
    )
    historical = json.loads(COLD_RECEIPT.read_bytes())
    payload = historical["payload"]
    assert payload["status"] == "PASS_COLD_REHEARSAL"
    assert payload["full_pass_runs"] == 1
    assert payload["injected_failure_runs"] == 22
    assert payload["file_proof_receipts_created"] == 0
    assert payload["preserved_proofs_not_executed"] == ["file_proof"]
    assert payload["continuity_attempt_number"] == 2
    assert payload["new_attempt_allowance"] == 0
    assert payload["real_aws_calls"] == 0
    assert payload["real_kubectl_calls"] == 0
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/b6_remaining_cold_rehearsal.py",
            "--output-dir",
            str(tmp_path / "current-source-refusal"),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 2
    assert "immutable predecessor binding differs" in completed.stdout


def test_attempt_parser_allows_only_continuity_attempt_two():
    script = ROOT / "scripts/b6_remaining_runner.py"
    common = [
        sys.executable,
        str(script),
        "--kubeconfig",
        "/tmp/missing-kubeconfig",
        "--authorization",
        "/tmp/missing-authorization",
        "--packet-sha256",
        "0" * 64,
        "--receipts-dir",
        "/tmp/missing-receipts",
        "--token-file",
        "/tmp/missing-token",
    ]
    for attempt in (1, 3):
        result = subprocess.run(
            [*common, "--attempt", str(attempt)], capture_output=True, text=True
        )
        assert result.returncode != 0
        assert f"invalid choice: '{attempt}'" in result.stderr


def test_stage0_proves_worker_deployment_and_alb_zero_state():
    operations = (ROOT / "scripts/b6_remaining_operations.sh").read_text()
    for binding in (
        "nodegroup.scalingConfig.desiredSize",
        "SYNTHETIC_DEPLOYMENT_COUNT_IS_ZERO",
        "LoadBalancerNotFound",
        "WINDOW_ALB_IS_ABSENT",
    ):
        assert binding in operations


def test_successor_stage0_safe_refusals_retain_exact_pre_model_detail(tmp_path):
    from scripts.b6_remaining_runner import safe_remaining_stage0_refusal

    for reason, code in (
        ("STAGE0_WORKER_CAPACITY_ZERO_REFUSED", 44),
        ("STAGE0_ALB_ABSENCE_REFUSED", 45),
        ("STAGE0_SYNTHETIC_DEPLOYMENT_ZERO_REFUSED", 46),
    ):
        path = tmp_path / f"{code}.json"
        path.write_text(
            json.dumps(
                {
                    "status": "REFUSED",
                    "reason_code": reason,
                    "failed_assertion": "EXACT_ZERO_STATE_ASSERTION",
                    "stage_exit_code": code,
                    "safe_error_text": "synthetic pre-model diagnostic",
                    "pre_model_and_audio": True,
                }
            )
        )
        assert safe_remaining_stage0_refusal(path, code) == {
            "reason_code": reason,
            "failed_assertion": "EXACT_ZERO_STATE_ASSERTION",
            "stage_exit_code": code,
            "safe_error_text": "synthetic pre-model diagnostic",
            "pre_model_and_audio": True,
        }


def test_authorization_binding_accepts_only_the_single_continuity_attempt(
    tmp_path: Path,
):
    from scripts.b6_remaining_bindings import (
        ATTEMPT_1_RESULT_PATH,
        AUTH_ID,
        COLD_PATH,
        FILE_RECEIPT_PATH,
        LOCAL_CONVERSATION_PATH,
        NEW_ORCHESTRATOR_DIGEST,
        PACKET_ID,
        REQUIRED_SOURCES,
        SCAN_RESULT_PATH,
        BindingRefusal,
        validate,
    )

    packet_sha256 = _sha(PACKET)
    reviewed_commit = "1" * 40
    authorization = {
        "id": AUTH_ID,
        "status": "owner-approved",
        "packet": {"id": PACKET_ID, "sha256": packet_sha256},
        "prepared_repository_commit": reviewed_commit,
        "independent_review": {
            "status": "PASS",
            "reviewed_repository_commit": reviewed_commit,
            "reviewed_packet_sha256": packet_sha256,
            "reviewed_cold_rehearsal_sha256": _sha(ROOT / COLD_PATH),
        },
        "allowance": {
            "aggregate_project_ceiling_usd": 300.0,
            "recognized_committed_guardrail_usd": 64.4286064216,
            "existing_reservation_usd": 10.0,
            "new_reservation_usd": 0.0,
            "original_packet_attempts_authorized": 2,
            "original_packet_attempts_consumed": 1,
            "continuity_attempt_number": 2,
            "requested_attempts": 1,
            "maximum_seconds_per_attempt": 4500,
            "maximum_requested_worker_seconds": 4500,
            "estimated_compute_usd": 1.6,
            "attempts_non_transferable": True,
            "pass_terminates_packet": True,
            "cold_rehearsal_required_before_each_attempt": True,
        },
        "proof_scope": {
            "preserved_not_rerun": ["file_proof"],
            "remaining_live_proofs": [
                "websocket_proof",
                "cancellation_proof",
                "failure_drills",
                "isolation_proof",
            ],
            "production_traffic": False,
            "synthetic_only": True,
        },
        "stage_a_reuse": {
            "source_packet": "B6-AWS-CHANGE-PACKET-2026-026",
            "aggregate_receipt_path": (
                "platform/evidence/receipts/"
                "B6-2026-026-STAGE-A-LIVE/stage_a.json"
            ),
            "aggregate_receipt_sha256": _sha(
                ROOT
                / "platform/evidence/receipts/"
                / "B6-2026-026-STAGE-A-LIVE/stage_a.json"
            ),
            "cleanup_receipt_path": (
                "platform/evidence/receipts/"
                "B6-2026-026-STAGE-A-LIVE/stage_a_cleanup.json"
            ),
            "cleanup_receipt_sha256": _sha(
                ROOT
                / "platform/evidence/receipts/"
                / "B6-2026-026-STAGE-A-LIVE/stage_a_cleanup.json"
            ),
            "stable_probe_passes": 3,
            "cleanup_complete": True,
            "rerun_permitted": False,
        },
        "immutable_evidence": {
            "packet_2026_031_scan_result": {
                "path": SCAN_RESULT_PATH,
                "sha256": _sha(ROOT / SCAN_RESULT_PATH),
                "outcome": "PASS_SCAN_ONLY",
                "orchestrator_child_manifest_digest": NEW_ORCHESTRATOR_DIGEST,
            },
            "preserved_file_proof": {
                "path": FILE_RECEIPT_PATH,
                "sha256": _sha(ROOT / FILE_RECEIPT_PATH),
                "status": "PASS",
                "rerun_permitted": False,
            },
            "packet_2026_032_attempt_1_refusal": {
                "path": ATTEMPT_1_RESULT_PATH,
                "sha256": _sha(ROOT / ATTEMPT_1_RESULT_PATH),
                "status": (
                    "REFUSED_ATTEMPT_2_LOCKED_PENDING_"
                    "LOCAL_CONVERSATION_QUALIFICATION"
                ),
                "failure_stage": "websocket_proof",
                "cleanup": "PASS",
            },
            "local_full_websocket_conversation": {
                "path": LOCAL_CONVERSATION_PATH,
                "sha256": _sha(ROOT / LOCAL_CONVERSATION_PATH),
                "status": "PASS",
                "probe_app_pair_sha256": (
                    "e68098b4d3b1722bb37c0851be770bcf"
                    "51bf656a24476c264f141a5361866a9b"
                ),
            },
        },
        "source_bindings": {
            relative: _sha(ROOT / relative)
            for relative in sorted(REQUIRED_SOURCES)
        },
    }
    path = tmp_path / "authorization.json"
    path.write_text(json.dumps(authorization))
    assert validate(path, packet_sha256, ROOT)["allowance"][
        "requested_attempts"
    ] == 1
    authorization["allowance"]["requested_attempts"] = 2
    path.write_text(json.dumps(authorization))
    with pytest.raises(BindingRefusal, match="allowance binding differs"):
        validate(path, packet_sha256, ROOT)
