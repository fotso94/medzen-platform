from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from scripts.b6_6_proof_audio_binding import (
    MANIFEST,
    OPERATIONS,
    PACKET,
    PROBE,
    PROOF_AUDIO_PATH,
    PROOF_AUDIO_SHA256,
    PROOF_AUDIO_SHA256_ENV,
    ProofAudioBindingRefusal,
    audit,
    evaluate_projection_hashes,
    rehearsal,
)
from scripts.b6_6_aws_read_fixtures import audit as audit_aws_read_fixtures
from scripts.b6_6_bindings import (
    AUTH_ID,
    COLD_PATH,
    DESCRIPTION_PROJECTION_PATH,
    PACKET_ID,
    REQUIRED_SOURCES,
    sha256_file,
    validate,
)


ROOT = Path(__file__).resolve().parents[1]


def test_proof_audio_single_source_audit_passes_without_platform_calls() -> None:
    result = audit()
    assert result["status"] == "PASS_PROOF_AUDIO_SINGLE_SOURCE"
    assert result["proof_audio_sha256"] == PROOF_AUDIO_SHA256
    assert result["projection_count"] == 3
    assert set(result["projections"].values()) == {PROOF_AUDIO_SHA256}
    assert result["probe_expected_hash_source"] == "ENV_ONLY_NO_PRIVATE_LITERAL"
    assert result["operations_expected_hash_source"] == "BINDING_MODULE_TO_ENV"
    assert result["real_aws_calls"] == 0
    assert result["real_kubectl_calls"] == 0
    assert result["mutations"] == 0


def test_selected_audio_bytes_and_all_reviewed_projections_are_equal() -> None:
    assert hashlib.sha256(PROOF_AUDIO_PATH.read_bytes()).hexdigest() == (
        PROOF_AUDIO_SHA256
    )
    probe = PROBE.read_text()
    operations = OPERATIONS.read_text()
    manifest = MANIFEST.read_text()
    packet = PACKET.read_text()
    evidence = (
        ROOT
        / "platform/evidence/"
        / "B6-PACKET-2026-030-ATTEMPT-1-REFUSED-PROBE-AUDIO-BINDING.json"
    ).read_text()
    superseded_hash = re.search(
        r'"probe_private_literal_sha256": "([0-9a-f]{64})"', evidence
    )
    assert superseded_hash is not None
    assert PROOF_AUDIO_SHA256 not in probe
    assert superseded_hash.group(1) not in probe
    assert PROOF_AUDIO_SHA256 not in operations
    assert superseded_hash.group(1) not in operations
    assert "os.environ.get(PROOF_AUDIO_SHA256_ENV)" in probe
    assert f'{PROOF_AUDIO_SHA256_ENV}="$proof_audio_sha256"' in operations
    assert manifest.count(PROOF_AUDIO_SHA256) == 1
    assert packet.count(PROOF_AUDIO_SHA256) == 1
    assert len(re.findall(r"Proof-audio SHA-256: `([0-9a-f]{64})`", packet)) == 1


@pytest.mark.parametrize("changed", ["binding_module", "manifest_configmap", "packet"])
def test_any_proof_audio_projection_drift_refuses(changed: str) -> None:
    projections = {
        "binding_module": PROOF_AUDIO_SHA256,
        "manifest_configmap": PROOF_AUDIO_SHA256,
        "packet": PROOF_AUDIO_SHA256,
    }
    projections[changed] = "0" * 64
    with pytest.raises(
        ProofAudioBindingRefusal, match="PROOF_AUDIO_PROJECTION_HASH_DRIFT"
    ):
        evaluate_projection_hashes(projections)


def test_proof_audio_rehearsal_covers_aligned_and_all_three_drift_paths() -> None:
    result = rehearsal()
    assert result["status"] == "PASS"
    assert result["aligned_pass"]["status"] == "PASS_PROOF_AUDIO_SINGLE_SOURCE"
    assert result["injected_failures"] == 3
    assert {item["changed_projection"] for item in result["drift_injections"]} == {
        "binding_module",
        "manifest_configmap",
        "packet",
    }
    assert {item["reason_code"] for item in result["drift_injections"]} == {
        "PROOF_AUDIO_PROJECTION_HASH_DRIFT"
    }


def test_narrow_successor_carries_only_packet_030_attempt_two() -> None:
    operations = (ROOT / "scripts/b6_6_operations.sh").read_text()
    cleanup = (ROOT / "scripts/b6_6_cleanup.sh").read_text()
    runner = (ROOT / "scripts/b6_6_runner.py").read_text()
    assert '[[ "$attempt" == "2" ]]' in operations
    assert '[[ "$attempt" == "2" ]]' in cleanup
    assert 'choices=(2,)' in runner
    assert "B6-2026-030-A1-LIVE" in runner


def test_prospective_authorization_shape_validates_exact_single_attempt(
    tmp_path: Path,
) -> None:
    packet_sha256 = sha256_file(PACKET)
    cold_payload = json.loads((ROOT / COLD_PATH).read_bytes())["payload"]
    aws_fixtures = audit_aws_read_fixtures(ROOT)
    record = {
        "id": AUTH_ID,
        "status": "owner-approved",
        "packet": {"id": PACKET_ID, "sha256": packet_sha256},
        "independent_review": {
            "status": "PASS",
            "reviewer": "prospective-shape-test-only",
            "reviewed_packet_sha256": packet_sha256,
            "reviewed_repository_commit": "a" * 40,
        },
        "prepared_repository_commit": "a" * 40,
        "allowance": {
            "aggregate_project_ceiling_usd": 300.0,
            "recognized_committed_guardrail_usd": 64.4286064216,
            "existing_reservation_usd": 10.0,
            "new_reservation_usd": 0.0,
            "requested_attempts": 1,
            "maximum_seconds_per_attempt": 4500,
            "maximum_requested_worker_seconds": 4500,
            "estimated_compute_usd": 1.6,
            "cold_rehearsal_required_before_each_attempt": True,
            "unused_seconds_not_transferable_between_attempts": True,
            "continuity_source_packet": "B6-AWS-CHANGE-PACKET-2026-030",
            "continuity_attempt_number": 2,
            "source_attempt_1_cleanup_required": True,
            "pass_terminates_packet": True,
        },
        "stage_a_reuse": {
            "source_packet": "B6-AWS-CHANGE-PACKET-2026-026",
            "source_packet_sha256": (
                "c39130c456b36b128f3c52fab22a533243c9d8e235128c574c3c56f892634702"
            ),
            "aggregate_receipt_path": (
                "platform/evidence/receipts/B6-2026-026-STAGE-A-LIVE/stage_a.json"
            ),
            "aggregate_receipt_sha256": sha256_file(
                ROOT
                / "platform/evidence/receipts/B6-2026-026-STAGE-A-LIVE/stage_a.json"
            ),
            "cleanup_receipt_path": (
                "platform/evidence/receipts/B6-2026-026-STAGE-A-LIVE/"
                "stage_a_cleanup.json"
            ),
            "cleanup_receipt_sha256": sha256_file(
                ROOT
                / "platform/evidence/receipts/B6-2026-026-STAGE-A-LIVE/"
                "stage_a_cleanup.json"
            ),
            "stable_probe_passes": 3,
            "cleanup_complete": True,
            "rerun_permitted": False,
        },
        "persistent_secret": {
            "bridge_receipt_required_before_attempt_2": True,
            "create_or_delete_during_window": False,
            "rotate_in_place_at_stage0": True,
            "operator_plaintext_read": "EXPLICIT_DENY_REQUIRED",
        },
        "cold_rehearsal": {
            "path": COLD_PATH,
            "sha256": sha256_file(ROOT / COLD_PATH),
            "status": "PASS_COLD_REHEARSAL",
            "full_pass_runs": 1,
            "injected_failure_runs": 46,
            "stage_injected_failure_runs": 23,
            "new_gate_injected_failure_runs": 4,
            "proof_diagnostic_injected_failure_runs": 13,
            "pre_deadline_cleanup_injected_failure_runs": 2,
            "credential_visibility_transient_injection_runs": 1,
            "registry_rag_alignment_injected_failure_runs": 1,
            "proof_audio_binding_injected_failure_runs": 3,
            "stage_a_full_pass_runs": 1,
            "stage_a_injected_failure_runs": 7,
            "new_gate_rehearsal": cold_payload["new_gate_rehearsal"],
            "proof_diagnostic_rehearsal": cold_payload[
                "proof_diagnostic_rehearsal"
            ],
            "pre_deadline_cleanup_rehearsal": cold_payload[
                "pre_deadline_cleanup_rehearsal"
            ],
            "credential_visibility_rehearsal": cold_payload[
                "credential_visibility_rehearsal"
            ],
            "post_mutation_stability_audit": cold_payload[
                "post_mutation_stability_audit"
            ],
            "registry_rag_alignment_rehearsal": cold_payload[
                "registry_rag_alignment_rehearsal"
            ],
            "proof_audio_binding_rehearsal": cold_payload[
                "proof_audio_binding_rehearsal"
            ],
            "empirical_connectivity_gate": aws_fixtures["network_reduction"],
            "terraform_description_charset_lint": {
                "status": "PASS",
                "description_fields": 50,
                "string_descriptions": 48,
                "null_descriptions": 2,
                "invalid_descriptions": 0,
                "allowed_character_class": "A-Za-z0-9. _-:/()#,@[]+=&;{}!$*",
                "projection_path": DESCRIPTION_PROJECTION_PATH,
                "projection_sha256": sha256_file(
                    ROOT / DESCRIPTION_PROJECTION_PATH
                ),
                "projection_inventory_sha256": (
                    "07ad67c8409d7b5f547bca51c6926cdd2e1fd0ea83a2918347a2d2ca7026b880"
                ),
                "invalid_description_refusal_cases": 1,
                "real_aws_calls": 0,
            },
            "aws_read_fixture_fidelity": aws_fixtures,
        },
        "source_bindings": {
            relative: sha256_file(ROOT / relative)
            for relative in sorted(REQUIRED_SOURCES)
        },
    }
    authorization = tmp_path / "prospective-authorization.json"
    authorization.write_text(json.dumps(record))
    assert validate(authorization, packet_sha256, ROOT) == record
