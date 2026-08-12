from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKET = ROOT / "platform/decisions/ASR-BASE-MODEL-AWS-CHANGE-PACKET-2026-002C-attempt-4.md"
BINDINGS = ROOT / "platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002C.json"
QUALIFICATION = ROOT / "platform/evidence/B6-ASR-EVAL-RUNTIME-LOCAL-QUALIFICATION-2026-006.json"
DIAGNOSIS = ROOT / "platform/evidence/ASR-EVAL-RUNTIME-LAYER-DIAGNOSIS-2026-001.json"
ROUNDTRIP = ROOT / "platform/evidence/ASR-EVAL-RUNTIME-EXACT-IMAGE-ROUNDTRIP-PROOF-2026-001.json"
COLD = ROOT / "platform/evidence/receipts/ASR-BASE-MODEL-2026-002C-COLD/cold-rehearsal.json"
OCI_PUBLISHER = ROOT / "scripts/asr_eval_oci_publication.py"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_packet_is_non_executable_and_requests_only_attempt_4() -> None:
    text = PACKET.read_text()
    assert "NOT EXECUTABLE" in text
    assert "Approve ASR base-model AWS change packet 2026-002C only" in text
    assert "numbered attempt 4" in text
    assert "one non-transferable 10,800-second" in text
    assert "Attempts 1, 2, and 3 are consumed" in text
    assert "a fifth attempt" in text


def test_packet_binds_diagnosis_roundtrip_qualification_and_rehearsal() -> None:
    text = PACKET.read_text()
    bindings = json.loads(BINDINGS.read_bytes())
    for path in (BINDINGS, QUALIFICATION, DIAGNOSIS, ROUNDTRIP, COLD):
        assert sha(path) in text
    assert bindings["diagnosis"]["sha256"] == sha(DIAGNOSIS)
    assert bindings["exact_image_upload_proof"]["sha256"] == sha(ROUNDTRIP)
    assert bindings["qualification"]["sha256"] == sha(QUALIFICATION)
    assert bindings["executor"]["cold_rehearsal_receipt_sha256"] == sha(COLD)
    assert bindings["executor"]["oci_publication_sha256"] == sha(OCI_PUBLISHER)


def test_diagnosis_names_layer_and_does_not_overclaim_transport_trigger() -> None:
    value = json.loads(DIAGNOSIS.read_bytes())
    assert value["status"] == "CONFIRMED_UPLOAD_PATH_TRUNCATION"
    assert value["corrupt_layer_named"]["descriptor_digest"] == (
        "sha256:1ef81fd1e44444eb44c30a37e6485d8cb605c0288699e7016f8ca53c308dcbfd"
    )
    assert value["per_boundary_results"]["docker_content_store"]["status"] == "PASS"
    assert value["per_boundary_results"]["oci_export"]["status"] == "PASS"
    assert value["per_boundary_results"]["attempt_3_ecr_upload"]["received_bytes"] == 2272854016
    assert value["root_cause"]["lower_level_transport_trigger"] == "NOT_PROVABLE_FROM_RETAINED_LOGS"


def test_exact_image_proof_is_local_only_and_identity_preserving() -> None:
    value = json.loads(ROUNDTRIP.read_bytes())
    assert value["status"] == "PASS_EXACT_LOCAL_REGISTRY_ROUNDTRIP"
    assert value["roundtrip"]["objects_read_back"] == 21
    assert value["roundtrip"]["runs_byte_identical"] is True
    assert value["execution"]["real_aws_calls"] == 0
    assert value["execution"]["image_identity_changed"] is False


def test_packet_requires_bounded_parts_exact_readback_and_authoritative_scan() -> None:
    text = PACKET.read_text()
    bindings = json.loads(BINDINGS.read_bytes())
    assert "UploadLayerPart" in text
    assert "lastByteReceived" in text
    assert "20 MiB" in text
    assert "read back and rehash all three manifests" in text
    assert bindings["ecr_publication"]["maximum_part_bytes"] == 20 * 1024 * 1024
    assert bindings["ecr_publication"]["authoritative_child_scan_required_before_compute"] is True


def test_packet_carries_forward_rehearsal_and_committed_auth_validation() -> None:
    text = PACKET.read_text()
    cold = json.loads(COLD.read_bytes())
    assert "actual committed authorization blob" in text
    assert "No AWS operation" in text
    assert cold["status"] == "PASS_COLD_REHEARSAL"
    assert cold["injected_failure_runs"] == 5
    assert cold["scenarios"]["image_upload_part_truncation"]["outcome"] == "BLOCKED_IMAGE_SCAN"
    assert cold["scenarios"]["image_manifest_readback_drift"]["outcome"] == "BLOCKED_IMAGE_SCAN"
    assert all(value["ecr_scan_configuration_restored"] for value in cold["scenarios"].values())


def test_risk_continuation_is_exact_and_non_precedential() -> None:
    text = PACKET.read_text()
    bindings = json.loads(BINDINGS.read_bytes())
    assert bindings["risk_acceptance_sha256"] == (
        "06189414e82c7e497fe7b45d5395af0f03de523bc54c17e1b1e3ae91229d744c"
    )
    assert bindings["image"]["image_context_changed"] is False
    assert "No file under `services/asr-eval-runtime/` changed" in text
    assert "production SSM" in text
    assert "`approved/asr/`" in text
    assert "MLflow registration" in text
