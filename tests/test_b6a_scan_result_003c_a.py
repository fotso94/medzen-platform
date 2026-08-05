from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "platform/evidence/B6A-PACKET-2026-003C-A-SCAN-RESULT.json"


def _result():
    return json.loads(RESULT.read_text())


def test_scan_result_binds_authorization_packet_and_local_evidence():
    result = _result()
    assert result["status"] == "VERIFIED_COMPLETE"
    assert result["outcome"] == "PASS_SCAN_ONLY"
    for key in ("authorization", "packet", "local_engineering"):
        binding = result[key]
        assert hashlib.sha256((ROOT / binding["path"]).read_bytes()).hexdigest() == binding[
            "sha256"
        ]


def test_both_exact_images_passed_automatic_scan_without_manual_scan():
    subjects = _result()["automatic_scan_subjects"]
    assert set(subjects) == {"asr_runtime", "nvidia_dra"}
    for subject in subjects.values():
        assert subject["automatic_scan_on_push"] is True
        assert subject["manual_scan_invoked"] is False
        assert subject["scan_status"] == "COMPLETE"
        assert subject["critical"] == 0
        assert subject["high"] == 0
        assert subject["findings"] == []
        assert subject["gate"] == "PASS"


def test_scan_result_binds_deployable_asr_child_not_only_index():
    asr = _result()["automatic_scan_subjects"]["asr_runtime"]
    assert asr["oci_index_digest"] == (
        "sha256:47d86776bb02dc9f06f40496a9905d89eb1fc25ab181607702743b06deb53a56"
    )
    assert asr["linux_amd64_manifest_digest"] == (
        "sha256:434ac9e757b56949324e9e480490042fcbf35285f27bdee713bd771b502f4087"
    )


def test_scan_packet_did_not_cross_deployment_boundary():
    post = _result()["post_execution_verification"]
    assert post["gpu_desired"] == 0
    assert post["b6a_artifact_target_objects"] == 0
    assert post["approved_asr_objects"] == 0
    assert post["production_registry_parameters"] == 0
    for key in (
        "artifact_upload_attempted",
        "iam_or_terraform_change_attempted",
        "nvidia_dra_install_attempted",
        "kubernetes_change_attempted",
        "deployment_attempted",
        "gpu_window_opened",
        "security_waiver_used",
        "image_deletion_attempted",
    ):
        assert post[key] is False


def test_pass_only_allows_preparing_separate_deployment_packet():
    result = _result()
    assert result["next_boundary"]["deployment_authorized"] is False
    assert result["next_boundary"]["separate_owner_approval_required"] is True
    assert result["preserved_project_state"]["b6a_deployment_complete"] is False
    assert result["preserved_project_state"]["b6_status"] == "BLOCKED"


def test_scan_result_records_exact_validation_counts():
    validation = _result()["validation"]
    assert validation["pytest_passed"] == 919
    assert validation["pytest_failed"] == 0
    assert validation["pytest_skipped"] == 0
    assert validation["pytest_deselected"] == 7
    assert validation["terraform_validate"] == "PASS"
    assert validation["generated_output_check"] == "PASS_13_FILES"
