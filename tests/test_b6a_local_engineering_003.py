from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "platform/evidence/B6A-LOCAL-ENGINEERING-2026-003.json"


def _evidence():
    return json.loads(EVIDENCE.read_text())


def test_local_engineering_evidence_preserves_historical_records():
    evidence = _evidence()
    assert evidence["status"] == "LOCAL_ENGINEERING_COMPLETE_AWS_SCAN_NOT_AUTHORIZED"
    for binding in evidence["historical_bindings"]:
        path = ROOT / binding["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == binding["sha256"]
        assert binding["edited"] is False


def test_local_engineering_evidence_binds_current_sources():
    for relative, expected in _evidence()["source_bindings"].items():
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == expected


def test_asr_image_is_bound_to_hardened_deployable_identity():
    image = _evidence()["accepted_asr_runtime_image"]
    assert image["source_revision_label"] == (
        "89f94d330de24478d0084cdf9010f7ac7a968303"
    )
    assert image["linux_amd64_manifest_digest"] == (
        "sha256:434ac9e757b56949324e9e480490042fcbf35285f27bdee713bd771b502f4087"
    )
    assert image["user"] == "10001:10001"
    assert image["gpu_memory_claim"] == "NOT_MEASURED_AND_NOT_INFERRED_FROM_DISK_SIZE"


def test_local_gates_remove_installers_and_do_not_override_ecr():
    evidence = _evidence()
    gates = evidence["local_gates"]
    assert set(gates["forbidden_os_packages_absent"]) == {
        "python3-pip-whl",
        "python3-setuptools-whl",
        "python3.12-venv",
    }
    assert gates["pip_executables_absent"] is True
    assert gates["pip_module_absent"] is True
    assert gates["docker_scout"]["critical"] == 0
    assert gates["docker_scout"]["high"] == 0
    assert gates["docker_scout"]["authoritative_for_deployment"] is False


def test_local_engineering_performed_no_live_mutation():
    boundary = _evidence()["execution_boundary"]
    for key in (
        "aws_mutations",
        "ecr_pushes",
        "artifact_uploads",
        "iam_or_kubernetes_changes",
        "gpu_hours",
        "gpu_cost_usd",
        "approved_asr_writes",
        "production_ssm_changes",
        "model_registrations",
    ):
        assert boundary[key] == 0
    assert boundary["b6a_complete"] is False
