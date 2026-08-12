from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKET = ROOT / (
    "platform/decisions/"
    "ASR-BASE-MODEL-AWS-CHANGE-PACKET-2026-002B-attempt-3.md"
)
BINDINGS = ROOT / (
    "platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002B.json"
)
QUALIFICATION = ROOT / (
    "platform/evidence/B6-ASR-EVAL-RUNTIME-LOCAL-QUALIFICATION-2026-005.json"
)
COLD = ROOT / (
    "platform/evidence/receipts/"
    "ASR-BASE-MODEL-2026-002B-COLD/cold-rehearsal.json"
)
FIXTURE = ROOT / (
    "tests/fixtures/aws/"
    "ecr-get-registry-scanning-configuration-basic-before-asr-eval.json"
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_packet_is_non_executable_and_requests_only_fresh_attempt_3() -> None:
    text = PACKET.read_text()
    assert "NOT EXECUTABLE" in text
    assert "Approve ASR base-model AWS change packet 2026-002B only" in text
    assert "numbered attempt 3" in text
    assert "one non-transferable 10,800-second" in text
    assert "Attempts 1 and 2 cannot be reused" in text
    assert "a fourth attempt" in text


def test_packet_requires_committed_authorization_dry_validation() -> None:
    text = PACKET.read_text()
    assert "actual committed authorization blob" in text
    assert "validate_authorization_payload" in text
    assert "validation receipt must be committed" in text
    assert "No AWS operation is permitted before it passes" in text


def test_packet_binds_qualification_rehearsal_and_real_fixture() -> None:
    text = PACKET.read_text()
    assert sha(BINDINGS) in text
    assert sha(QUALIFICATION) in text
    assert sha(COLD) in text
    assert sha(FIXTURE) in text
    bindings = json.loads(BINDINGS.read_bytes())
    assert bindings["qualification"]["sha256"] == sha(QUALIFICATION)
    assert bindings["executor"]["cold_rehearsal_receipt_sha256"] == sha(COLD)
    assert bindings["ecr_scanning"]["fixture_sha256"] == sha(FIXTURE)


def test_packet_requires_merge_and_exact_restoration() -> None:
    text = PACKET.read_text()
    bindings = json.loads(BINDINGS.read_bytes())
    assert "add `medzen-asr-eval-runtime` to the existing `SCAN_ON_PUSH` rule" in text
    assert "restore the exact prior scan type" in text
    assert "two stable, exact restoration observations" in text
    assert bindings["ecr_scanning"]["merge_rule"] == (
        "MERGE_FILTER_INTO_EXISTING_SCAN_ON_PUSH_RULE"
    )
    assert bindings["ecr_scanning"]["cleanup_rule"] == (
        "RESTORE_EXACT_PRIOR_CONFIGURATION"
    )


def test_packet_continues_only_the_exact_unchanged_image_risk() -> None:
    text = PACKET.read_text()
    bindings = json.loads(BINDINGS.read_bytes())
    assert bindings["risk_acceptance_sha256"] == (
        "06189414e82c7e497fe7b45d5395af0f03de523bc54c17e1b1e3ae91229d744c"
    )
    assert bindings["image"]["oci_index_digest"] in text
    assert bindings["image"]["linux_amd64_digest"] in text
    assert "No file under `services/asr-eval-runtime/` changed" in text
    assert "authoritative child scan remains mandatory" in text


def test_packet_treats_attempt_2_repository_as_existing_not_a_new_create() -> None:
    text = PACKET.read_text()
    assert "existing read-only infrastructure" in text
    assert "may not create, delete, replace, or" in text
    assert "reconfigure the repository" in text


def test_packet_preserves_offline_and_non_promotion_boundaries() -> None:
    text = PACKET.read_text()
    for required in (
        "S3/ECR endpoints only",
        "no public internet",
        "production SSM",
        "`approved/asr/`",
        "language registry",
        "MLflow registration",
        "full-suite scoring",
    ):
        assert required in text


def test_cold_rehearsal_enforces_aws_constraint_and_restores_every_path() -> None:
    receipt = json.loads(COLD.read_bytes())
    assert receipt["status"] == "PASS_COLD_REHEARSAL"
    assert receipt["ecr_scan_rule_constraint"]["status"] == (
        "PASS_ONE_RULE_PER_FREQUENCY_ENFORCED"
    )
    assert all(
        scenario["ecr_scan_configuration_restored"] is True
        for scenario in receipt["scenarios"].values()
    )
