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
    "B6-AWS-CHANGE-PACKET-2026-034-remaining-proofs.md"
)
SCAN = ROOT / "platform/evidence/B6-PACKET-2026-033-SCAN-RESULT.json"
FILE_RECEIPT = ROOT / "platform/evidence/receipts/B6-2026-030A-A2-LIVE/file_proof.json"
PRIOR_REFUSAL = ROOT / (
    "platform/evidence/"
    "B6-PACKET-2026-032A-ATTEMPT-2-TERMINAL-DEPENDENCY-REFUSAL.json"
)
LOCAL_QUALIFICATION = ROOT / (
    "platform/evidence/"
    "B6-WEBSOCKET-PARTIAL-SOURCE-LOCAL-QUALIFICATION-2026-001.json"
)
COLD_RECEIPT = ROOT / "platform/evidence/receipts/B6-2026-034-COLD/cold_rehearsal.json"
HISTORICAL_COLD = ROOT / "platform/evidence/receipts/B6-2026-032A-COLD/cold_rehearsal.json"
NEW = "sha256:475ff8520e7ff78a52208a1bebe1de78c2a257de112424a837d0f5e1a73d2dc3"
OLD = "sha256:a3bd7170dbef4541ff6286324974a79d0b0da2287dcdcaf8f77a20654c7befed"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_packet_binds_scan_pass_file_pass_and_fresh_allowance():
    packet = PACKET.read_text()
    assert _sha(SCAN) in packet
    assert _sha(FILE_RECEIPT) in packet
    assert _sha(PRIOR_REFUSAL) in packet
    assert _sha(LOCAL_QUALIFICATION) in packet
    assert NEW in packet
    assert "must not be rerun" in packet
    assert "two non-transferable" in packet
    assert "Fresh attempts requested | `2`" in packet
    assert "New reservation | `$0.00`" in packet
    assert "A third attempt" in packet


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


def test_historical_cold_rehearsal_remains_immutable():
    assert _sha(HISTORICAL_COLD) == (
        "84d5c16a8540502554365e3cba9639e60b24866fd4bb3328fc57a9194d8b2401"
    )
    historical = json.loads(HISTORICAL_COLD.read_bytes())
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


def test_fresh_cold_rehearsal_includes_dependency_unavailable_injection():
    receipt = json.loads(COLD_RECEIPT.read_bytes())
    payload = receipt["payload"]
    assert payload["packet"] == "B6-AWS-CHANGE-PACKET-2026-034"
    assert payload["requested_attempts"] == 2
    assert payload["maximum_seconds_per_attempt"] == 4500
    assert payload["attempts_non_transferable"] is True
    assert payload["attempt_model_audit"] == {
        "allowed_attempts": [1, 2],
        "seconds_per_attempt": {"1": 4500, "2": 4500},
        "maximum_requested_worker_seconds": 9000,
        "attempts_non_transferable": True,
        "attempt_1_requires_no_predecessor": True,
        "attempt_2_requires_attempt_1_clean_refusal": True,
        "pass_terminates_packet": True,
        "status": "PASS",
    }
    assert payload["full_pass_runs"] == 1
    assert payload["injected_failure_runs"] == 23
    assert payload["dependency_unavailable_injections"] == 1
    injected = [
        scenario
        for scenario in payload["scenarios"]
        if scenario["scenario"] == "dependency-unavailable-websocket-proof"
    ]
    assert len(injected) == 1
    assert injected[0]["outcome"] == "REFUSED"
    assert injected[0]["failure_stage"] == "websocket_proof"
    assert injected[0]["injected_reason_code"] == (
        "STREAMING_PARTIAL_SOURCE_UNAVAILABLE"
    )
    assert injected[0]["dependency_refusal_diagnostic"] == {
        "dependency": "streaming_partial_source",
        "http_status": 503,
        "close_code": 4503,
        "reason_code": "STREAMING_PARTIAL_SOURCE_UNAVAILABLE",
        "synthetic_only": True,
    }
    assert injected[0]["cleanup_complete"] is True
    assert payload["file_proof_receipts_created"] == 0
    assert payload["real_aws_calls"] == 0
    assert payload["real_kubectl_calls"] == 0


def test_fresh_cold_rehearsal_is_deterministic(tmp_path):
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/b6_remaining_cold_rehearsal.py",
            "--output-dir",
            str(tmp_path / "repeat"),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout
    repeated = json.loads((tmp_path / "repeat/cold_rehearsal.json").read_bytes())
    reviewed = json.loads(COLD_RECEIPT.read_bytes())
    # B6v2 round 4: determinism means the SCENARIO OUTCOMES replay
    # byte-identically. runner_source_hashes honestly records the bytes
    # that ran each time — sources reviewed and changed since the closed
    # window verify at the window's prepared_repository_commit instead
    # of being frozen forever (same rule as the bindings validator).
    repeated_sources = repeated["payload"].pop("runner_source_hashes")
    reviewed_sources = reviewed["payload"].pop("runner_source_hashes")
    assert repeated["payload"] == reviewed["payload"]
    assert set(repeated_sources) == set(reviewed_sources)
    import sys as _sys
    _sys.path.insert(0, str(ROOT))
    from scripts.b6_remaining_cold_rehearsal import (
        _matches_current_or_authorized,
    )
    for relative, reviewed_sha in reviewed_sources.items():
        assert _matches_current_or_authorized(reviewed_sha, relative), (
            f"{relative}: reviewed hash matches neither the working tree "
            "nor the window's prepared commit")


def test_attempt_parser_allows_exactly_fresh_attempts_one_and_two():
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
    for attempt in (0, 3):
        result = subprocess.run(
            [*common, "--attempt", str(attempt)], capture_output=True, text=True
        )
        assert result.returncode != 0
        assert f"invalid choice: '{attempt}'" in result.stderr
    for attempt in (1, 2):
        result = subprocess.run(
            [*common, "--attempt", str(attempt)], capture_output=True, text=True
        )
        assert "invalid choice" not in result.stderr


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


def test_authorization_binding_accepts_only_two_fresh_nontransferable_attempts(
    tmp_path: Path,
):
    from scripts.b6_remaining_bindings import (
        AUTH_ID,
        COLD_PATH,
        FILE_RECEIPT_PATH,
        LOCAL_QUALIFICATION_PATH,
        NEW_ORCHESTRATOR_DIGEST,
        PACKET_ID,
        PRIOR_REFUSAL_PATH,
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
            "requested_attempts": 2,
            "maximum_seconds_per_attempt": 4500,
            "maximum_requested_worker_seconds": 9000,
            "estimated_compute_usd": 3.2,
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
            "packet_2026_033_scan_result": {
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
            "packet_2026_032a_dependency_refusal": {
                "path": PRIOR_REFUSAL_PATH,
                "sha256": _sha(ROOT / PRIOR_REFUSAL_PATH),
                "status": "REFUSED_NO_ATTEMPTS_REMAINING_PENDING_DEPENDENCY_DIAGNOSIS",
                "failure_stage": "websocket_proof",
                "close_code": 4503,
                "cleanup": "PASS",
            },
            "local_partial_source_qualification": {
                "path": LOCAL_QUALIFICATION_PATH,
                "sha256": _sha(ROOT / LOCAL_QUALIFICATION_PATH),
                "status": "PASS_LOCAL_ECR_SCAN_NOT_AUTHORIZED",
                "dependency_unavailable_reason": (
                    "STREAMING_PARTIAL_SOURCE_UNAVAILABLE"
                ),
                "stable_full_conversation_passes": 3,
                "probe_app_pair_sha256": (
                    "f6c8eb872cbd80c5542350e0c4ac5c0"
                    "b1cff82d820d94ab452ef12cba816a9d6"
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
    ] == 2
    authorization["allowance"]["requested_attempts"] = 3
    path.write_text(json.dumps(authorization))
    with pytest.raises(BindingRefusal, match="allowance binding differs"):
        validate(path, packet_sha256, ROOT)
