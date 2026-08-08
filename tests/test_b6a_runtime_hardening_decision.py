from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DECISION = (
    ROOT / "platform/decisions/B6A-DESIGN-2026-003-runtime-image-hardening.json"
)


def _decision():
    return json.loads(DECISION.read_text())


def test_decision_preserves_prior_records_by_hash():
    decision = _decision()
    assert decision["status"] == "owner-approved-local-engineering-only"
    for binding in decision["supersedes_by_reference_only"]:
        path = ROOT / binding["path"]
        assert binding["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert binding["historical_record_edited"] is False


def test_decision_correctly_attributes_pip_to_final_stage_venv_install():
    root_cause = _decision()["root_cause"]
    assert root_cause["affected_os_package"] == "python3-pip-whl"
    assert "python3.12-venv" in root_cause["introduced_by"]
    assert root_cause["not_inherited_from_pinned_cuda_base"] is True
    assert root_cause["authoritative_ecr_high_findings"] == 4


def test_local_authority_does_not_authorize_live_scan_or_deployment():
    decision = _decision()
    assert "Any AWS mutation" in decision["prohibited_operations"]
    assert "Any ECR push or scan invocation" in decision["prohibited_operations"]
    boundary = decision["future_003c_a_boundary"]
    assert boundary["purpose"] == "Scan-only AWS packet, not deployment."
    assert boundary["separate_owner_approval_required"] is True
    assert "GPU scale-up" in boundary["prohibited_even_if_scans_pass"]


def test_standing_pattern_requires_minimal_runtime_and_remote_gate():
    rules = " ".join(_decision()["standing_runtime_image_pattern"]["rules"])
    for required in (
        "digest-pinned bases",
        "multi-stage builds",
        "package installers",
        "non-root identity",
        "automatic ECR scan",
        "deployable child digests",
    ):
        assert required in rules
