from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "services" / "asr-eval-runtime"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(PACKAGE) not in sys.path:
    sys.path.insert(0, str(PACKAGE))

from medzen_asr_eval.conditioning import language_id, load_conditioning
from medzen_asr_eval.harness import EvaluationRefusal
from medzen_asr_eval.metrics import aggregate, error_counts, normalize_text
from medzen_asr_eval.network_probe import probe_network
from pipeline.asr_base_model_pilot_receipts import ReceiptStore, STAGES
from scripts.asr_base_model_pilot_fake import FakeOperations
from scripts.asr_base_model_pilot_k8s import render, verify
from scripts.asr_base_model_pilot_live import LiveOperations
from scripts.asr_base_model_pilot_plan import exact_plan, validate_plan
from scripts.asr_base_model_pilot_runner import (
    AttemptContext,
    STAGE_FUNCTIONS,
    execute_attempt,
)


ZERO = "0" * 64
IMAGE = "sha256:" + "1" * 64


def bindings() -> dict:
    return {
        "image": {"linux_amd64_digest": IMAGE},
        "pilot_bundle": {"sha256": "2" * 64},
    }


def context(tmp_path: Path) -> AttemptContext:
    return AttemptContext(
        attempt=1,
        bindings=bindings(),
        receipts=ReceiptStore(tmp_path / "receipts", packet_sha256=ZERO, authorization_sha256="a" * 64),
        workdir=tmp_path,
    )


def test_language_conditioning_is_explicit_and_never_proxies() -> None:
    mapping = load_conditioning(PACKAGE / "assets/language-conditioning-v1.json")
    assert language_id("whisper-large-v3", "english", mapping) == "en"
    assert language_id("omniASR_LLM_1B_v2", "swahili", mapping) == "swh_Latn"
    assert language_id("omniASR_LLM_1B_v2", "gbaya", mapping) is None
    assert language_id("omniASR_CTC_1B_v2", "english", mapping) is None
    with pytest.raises(EvaluationRefusal, match="no reviewed conditioning"):
        language_id("whisper-large-v3", "invented", mapping)


def test_metrics_normalize_and_aggregate_micro_macro_and_resources() -> None:
    assert normalize_text("  HÉLLO,\nWORLD! ") == "héllo world"
    assert error_counts("one two", "one too") == {
        "word_errors": 1,
        "reference_words": 2,
        "character_errors": 1,
        "reference_characters": 6,
    }
    rows = []
    for language, word_errors, latency in (("english", 0, 1.0), ("french", 1, 2.0)):
        rows.append({
            "status": "PASS_ROW_INFERENCE",
            "candidate": "whisper-large-v3",
            "mode": "unconditioned",
            "language": language,
            "source_id": "fleurs",
            "errors": {"word_errors": word_errors, "reference_words": 2, "character_errors": word_errors, "reference_characters": 6},
            "latency_seconds": latency,
            "rtf": latency / 2,
            "eos_failure": False,
            "cap_hit": False,
        })
    value = aggregate(rows, [100.0, 125.0, 110.0])
    assert value["status"] == "PASS_AGGREGATE"
    assert value["groups"]["whisper-large-v3|unconditioned"]["wer"] == 0.25
    assert value["groups"]["whisper-large-v3|unconditioned"]["language_macro_wer"] == 0.25
    assert value["gpu_memory"] == {"unit": "MiB", "sample_count": 3, "baseline": 100.0, "peak": 125.0}


def test_network_probe_proves_allow_and_deny_before_torch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(sys.modules, "torch", raising=False)
    binding = tmp_path / "network.json"
    binding.write_text(json.dumps({
        "schema_version": 1,
        "classification": "OFFLINE_EVALUATION_ONLY",
        "allowed_tcp_443_hosts": [
            "bucket.s3.eu-central-1.amazonaws.com",
            "api.ecr.eu-central-1.amazonaws.com",
            "dkr.ecr.eu-central-1.amazonaws.com",
        ],
    }))

    def connector(host: str, port: int, timeout: float) -> None:
        if host in {"dl.fbaipublicfiles.com", "example.com", "169.254.169.254"}:
            raise OSError("refused")

    value = probe_network(binding, tmp_path / "receipt.json", connector=connector)
    assert value["status"] == "PASS_NETWORK_ISOLATION_PRE_TORCH"
    assert len(value["positive_and_negative_proofs"]["allowed"]) == 3
    assert len(value["positive_and_negative_proofs"]["denied"]) == 3


def test_network_probe_fails_if_public_control_is_reachable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(sys.modules, "torch", raising=False)
    binding = tmp_path / "network.json"
    binding.write_text(json.dumps({
        "schema_version": 1,
        "classification": "OFFLINE_EVALUATION_ONLY",
        "allowed_tcp_443_hosts": ["a.amazonaws.com", "b.amazonaws.com", "c.amazonaws.com"],
    }))

    def connector(host: str, port: int, timeout: float) -> None:
        if host in {"dl.fbaipublicfiles.com", "169.254.169.254"}:
            raise OSError("refused")

    with pytest.raises(EvaluationRefusal, match="public_https_control"):
        probe_network(binding, tmp_path / "receipt.json", connector=connector)


def test_plan_is_exact_and_rejects_prohibited_drift() -> None:
    plan = exact_plan(bindings(), 1)
    assert validate_plan(plan, bindings(), 1)["status"] == "PASS_EXACT_EXECUTION_PLAN"
    drifted = json.loads(json.dumps(plan))
    drifted["temporary_create_then_delete"].append("iam:role/unreviewed")
    with pytest.raises(ValueError, match="exact allowlist"):
        validate_plan(drifted, bindings(), 1)


def test_k8s_workload_is_digest_pinned_non_serving_and_network_first() -> None:
    rendered = render(bindings(), ["10.0.1.7", "10.0.2.8"], ["52.219.0.0/16"], 1)
    result = verify(rendered, IMAGE, 1)
    assert result["service_count"] == 0
    docs = list(yaml.safe_load_all(rendered))
    assert [value["kind"] for value in docs] == ["Namespace", "ResourceClaimTemplate", "NetworkPolicy", "NetworkPolicy", "Job"]
    command = docs[-1]["spec"]["template"]["spec"]["containers"][0]["args"][0]
    assert command.index("network-probe") < command.index("inbound-listener-ready") < command.index("network-release") < command.index(" pilot ")


def test_stage_map_implements_every_claimed_stage() -> None:
    assert tuple(STAGE_FUNCTIONS) == STAGES
    assert all(callable(STAGE_FUNCTIONS[name]) for name in STAGES)
    assert all(callable(getattr(LiveOperations, name)) for name in STAGES)
    assert all(callable(getattr(FakeOperations, name)) for name in STAGES)


@pytest.mark.parametrize(
    ("injected_stage", "expected_outcome"),
    [
        (None, "PASS_PILOT"),
        ("deadline_identity_and_acceptance", "FAILED_CLOSED_EXECUTION"),
        ("private_endpoint_and_policy_gate", "BLOCKED_NETWORK_ISOLATION"),
        ("cleanup_and_expiry", "FAILED_CLOSED_EXECUTION"),
    ],
)
def test_cold_attempt_pass_and_injected_failures_cleanup(
    tmp_path: Path, injected_stage: str | None, expected_outcome: str
) -> None:
    ops = FakeOperations(inject=injected_stage)
    result = execute_attempt(ops, context(tmp_path))
    assert result["outcome"] == expected_outcome
    assert ops.zero_state()
    cleanup = json.loads((tmp_path / "receipts/cleanup_and_expiry.json").read_bytes())
    assert cleanup["status"] == ("REFUSED" if injected_stage == "cleanup_and_expiry" else "PASS")
    if injected_stage is None:
        assert result["failure_stage"] is None
        assert ops.aggregate is not None
        assert ops.aggregate["completed_inferences"] == 5
        assert tuple(ops.stage_order) == STAGES
    else:
        assert result["failure_stage"] == injected_stage


def test_receipts_are_write_once(tmp_path: Path) -> None:
    store = ReceiptStore(tmp_path, packet_sha256=ZERO, authorization_sha256="a" * 64)
    store.persist(STAGES[0], "PASS", {"status": "PASS"})
    with pytest.raises(Exception, match="overwrite"):
        store.persist(STAGES[0], "PASS", {"status": "PASS"})


def test_scan_subject_and_successor_hash_chain_are_self_identifying() -> None:
    scan_path = ROOT / "platform/evidence/ASR-EVAL-RUNTIME-LOCAL-SCAN-2026-003.sarif.json"
    subject_path = ROOT / "platform/evidence/ASR-EVAL-RUNTIME-LOCAL-SCAN-SUBJECT-2026-004.json"
    qualification_path = ROOT / "platform/evidence/B6-ASR-EVAL-RUNTIME-LOCAL-QUALIFICATION-2026-003.json"
    risk_path = ROOT / "platform/decisions/ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-002.json"
    bindings_path = ROOT / "platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002.json"
    packet_path = ROOT / "platform/decisions/ASR-BASE-MODEL-AWS-CHANGE-PACKET-2026-002-pilot-successor.md"

    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    subject = json.loads(subject_path.read_bytes())
    qualification = json.loads(qualification_path.read_bytes())
    risk = json.loads(risk_path.read_bytes())
    bound = json.loads(bindings_path.read_bytes())
    packet = packet_path.read_text(encoding="utf-8")

    assert subject["status"] == "PASS_SCAN_SUBJECT_IDENTIFIED_SCOPED_RISK_ACCEPTANCE_REQUIRED"
    assert subject["normalized_sarif"]["sha256"] == digest(scan_path)
    assert subject["normalized_sarif"]["new_scan_output_byte_identical_to_committed_sarif"] is True
    assert subject["scan_execution"]["command"][-1] == "local://medzen-asr-eval-runtime:pilot-5d1b8a0"
    assert subject["scan_subject"]["oci_index_digest"] == bound["image"]["oci_index_digest"]
    assert subject["scan_subject"]["linux_amd64_child_manifest_digest"] == bound["image"]["linux_amd64_digest"]
    assert subject["scan_subject"]["source_commit"] == bound["image"]["source_commit"]
    assert qualification["local_scan"]["subject_record"]["sha256"] == digest(subject_path)
    assert risk["immutable_subject"]["qualification_record"]["sha256"] == digest(qualification_path)
    assert risk["immutable_subject"]["local_scan"]["subject_record_sha256"] == digest(subject_path)
    assert bound["risk_acceptance_sha256"] == digest(risk_path)
    assert digest(qualification_path) in packet
    assert digest(risk_path) in packet
    assert digest(subject_path) in packet
