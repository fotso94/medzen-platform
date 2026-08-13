from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.asr_base_model_pilot_plan import exact_plan, validate_plan
from scripts.asr_eval_digest_rescan import validate_security_binding
from pipeline.asr_base_model_pilot_receipts import ReceiptStore
from scripts.asr_base_model_pilot_runner import AttemptContext, OperationRefusal, execute_attempt


BINDINGS = ROOT / "platform/manifests/ASR-EVAL-RUNTIME-ECR-DIGEST-RESCAN-BINDINGS-2026-001.json"


def _plan_bindings() -> dict:
    value = json.loads(BINDINGS.read_bytes())
    value["pilot_bundle"] = {
        "sha256": "1cdca3e75195c7c7417550154e36a1f372715e17efd13c835c87ee503fa84eee"
    }
    return value


def test_attempt_5_plan_skips_image_upload_and_registry_scan_mutation() -> None:
    bindings = _plan_bindings()
    plan = exact_plan(bindings, 5)
    assert validate_plan(plan, bindings, 5)["status"] == "PASS_EXACT_EXECUTION_PLAN"
    mutation_scope = plan["permanent_create_only"] + plan["temporary_create_then_delete"]
    assert not any(value.startswith("ecr:") for value in mutation_scope)
    assert plan["read_only_existing"].count("ecr:repository/medzen-asr-eval-runtime") == 1


def test_attempt_5_security_binding_matches_executable_gate_exactly() -> None:
    bindings = json.loads(BINDINGS.read_bytes())
    assert validate_security_binding(bindings["security_gate"])["status"] == (
        "PASS_EXACT_SECURITY_GATE_BINDING"
    )


def test_attempt_5_missing_scout_auth_refuses_before_attempt_envelope_or_aws(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.delenv("DOCKER_SCOUT_HUB_USER", raising=False)
    monkeypatch.delenv("DOCKER_SCOUT_HUB_PASSWORD", raising=False)
    bindings = _plan_bindings()
    class MustNotRun:
        pass

    ops = MustNotRun()
    context = AttemptContext(
        attempt=5,
        bindings=bindings,
        receipts=ReceiptStore(tmp_path / "receipts", packet_sha256="0" * 64, authorization_sha256="a" * 64),
        workdir=tmp_path,
    )
    with pytest.raises(OperationRefusal) as captured:
        execute_attempt(ops, context)
    assert captured.value.reason_code == "SCOUT_AUTHENTICATION_ABSENT"
    assert not (tmp_path / "attempt-envelope.json").exists()
