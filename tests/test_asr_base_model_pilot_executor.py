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
from scripts.asr_base_model_pilot_fake import (
    assert_no_parallel_stage_implementation,
    build_rehearsal_operations,
)
from scripts.asr_base_model_ecr_scanning import (
    canonical_configuration,
    merge_scan_on_push_filter,
    validate_configuration,
)
from scripts.asr_base_model_pilot_k8s import render, verify
from scripts.asr_base_model_pilot_live import LiveOperations
from scripts.asr_base_model_pilot_plan import exact_plan, validate_plan
from scripts.asr_base_model_pilot_runner import (
    AttemptContext,
    OperationRefusal,
    STAGE_FUNCTIONS,
    _safe_reason,
    execute_attempt,
    validate_authorization_payload,
)


ZERO = "0" * 64
IMAGE = "sha256:" + "1" * 64


def bindings() -> dict:
    return {
        "image": {"linux_amd64_digest": IMAGE, "tag": "pilot-exact"},
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
    assert plan["permanent_create_only"] == [
        "ecr:repository/medzen-asr-eval-runtime:oci-index/sha256:" + "0" * 64,
        "ecr:repository/medzen-asr-eval-runtime:tag/pilot-exact",
        "ecr:repository/medzen-asr-eval-runtime:content-addressed-blobs/from-verified-oci-layout",
        "s3:medzen-speech/research/asr-base-model/pilot/" + "2" * 64 + "/**",
    ]
    assert "ecr:repository/medzen-asr-eval-runtime" in plan["read_only_existing"]
    assert plan["permanent_bounded_update"] == []
    assert (
        "ecr:registry-scanning-configuration/"
        "merge-exact-filter-then-restore-prior-filter-list"
        in plan["temporary_create_then_delete"]
    )
    drifted = json.loads(json.dumps(plan))
    drifted["temporary_create_then_delete"].append("iam:role/unreviewed")
    with pytest.raises(ValueError, match="exact allowlist"):
        validate_plan(drifted, bindings(), 1)


def test_real_ecr_scanning_response_merges_into_one_rule_and_is_idempotent() -> None:
    fixture_path = ROOT / (
        "tests/fixtures/aws/"
        "ecr-get-registry-scanning-configuration-basic-before-asr-eval.json"
    )
    before = json.loads(fixture_path.read_bytes())["scanningConfiguration"]
    updated, changed = merge_scan_on_push_filter(
        before, "medzen-asr-eval-runtime"
    )
    assert changed is True
    assert canonical_configuration(before) != canonical_configuration(updated)
    scan_on_push = [
        rule for rule in updated["rules"]
        if rule["scanFrequency"] == "SCAN_ON_PUSH"
    ]
    assert len(scan_on_push) == 1
    assert scan_on_push[0]["repositoryFilters"][:-1] == before["rules"][0][
        "repositoryFilters"
    ]
    assert scan_on_push[0]["repositoryFilters"][-1] == {
        "filter": "medzen-asr-eval-runtime",
        "filterType": "WILDCARD",
    }
    repeated, repeated_changed = merge_scan_on_push_filter(
        updated, "medzen-asr-eval-runtime"
    )
    assert repeated_changed is False
    assert canonical_configuration(repeated) == canonical_configuration(updated)


def test_ecr_model_rejects_duplicate_scan_frequency_like_aws() -> None:
    fixture_path = ROOT / (
        "tests/fixtures/aws/"
        "ecr-get-registry-scanning-configuration-basic-before-asr-eval.json"
    )
    value = json.loads(fixture_path.read_bytes())["scanningConfiguration"]
    value["rules"].append({
        "scanFrequency": "SCAN_ON_PUSH",
        "repositoryFilters": [{
            "filter": "medzen-asr-eval-runtime",
            "filterType": "WILDCARD",
        }],
    })
    with pytest.raises(ValueError, match="duplicate ECR scan frequency"):
        validate_configuration(value)


def test_ecr_merge_refuses_ambiguous_existing_repository_filter() -> None:
    fixture_path = ROOT / (
        "tests/fixtures/aws/"
        "ecr-get-registry-scanning-configuration-basic-before-asr-eval.json"
    )
    value = json.loads(fixture_path.read_bytes())["scanningConfiguration"]
    value["rules"].append({
        "scanFrequency": "MANUAL",
        "repositoryFilters": [{
            "filter": "medzen-asr-eval-runtime",
            "filterType": "WILDCARD",
        }],
    })
    with pytest.raises(ValueError, match="ambiguous frequency"):
        merge_scan_on_push_filter(value, "medzen-asr-eval-runtime")


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
    assert assert_no_parallel_stage_implementation()["parallel_stage_implementations"] == 0


def test_boundary_harness_instantiates_real_live_operations() -> None:
    current = json.loads((ROOT / "platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002I.json").read_bytes())
    operations, boundary = build_rehearsal_operations(current)
    assert type(operations) is LiveOperations
    assert boundary.zero_state()
    assert assert_no_parallel_stage_implementation()["parallel_stage_implementations"] == 0


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


def test_refusal_reason_survives_duplicate_module_class_identity() -> None:
    class ForeignOperationRefusal(RuntimeError):
        reason_code = "AUTHORIZATION_ATTEMPTS_ABSENT"
        detail = "top-level attempt authorization is absent"
        outcome = "FAILED_CLOSED_EXECUTION"

    assert _safe_reason(ForeignOperationRefusal()) == {
        "reason_code": "AUTHORIZATION_ATTEMPTS_ABSENT",
        "safe_error_text": "top-level attempt authorization is absent",
    }


def test_successor_authorization_allows_only_explicit_remaining_attempt() -> None:
    authorization = {
        "id": "ASR-BASE-MODEL-AWS-AUTH-2026-002A",
        "status": "owner-approved",
        "packet": {"sha256": "b" * 64},
        "risk_acceptance": {"sha256": "c" * 64},
        "attempts": {
            "authorized_numbers": [2],
            "maximum": 1,
            "seconds_each": 10800,
            "non_transferable": True,
        },
    }
    result = validate_authorization_payload(
        authorization,
        expected_id=authorization["id"],
        packet_sha256="b" * 64,
        risk_sha256="c" * 64,
        attempt=2,
    )
    assert result["status"] == "PASS_AUTHORIZATION_SCHEMA"
    with pytest.raises(OperationRefusal, match="successor owner authorization differs"):
        validate_authorization_payload(
            authorization,
            expected_id=authorization["id"],
            packet_sha256="b" * 64,
            risk_sha256="c" * 64,
            attempt=1,
        )


def test_authorization_without_top_level_attempts_refuses_precisely() -> None:
    with pytest.raises(OperationRefusal) as captured:
        validate_authorization_payload(
            {"id": "ASR-BASE-MODEL-AWS-AUTH-2026-002A", "status": "owner-approved"},
            expected_id="ASR-BASE-MODEL-AWS-AUTH-2026-002A",
            packet_sha256="b" * 64,
            risk_sha256="c" * 64,
            attempt=2,
        )
    assert captured.value.reason_code == "AUTHORIZATION_ATTEMPTS_ABSENT"


def test_live_executor_never_requires_dependencies_inside_reviewed_worktree() -> None:
    source = (ROOT / "scripts/asr_base_model_pilot_live.py").read_text(encoding="utf-8")
    assert '.venv/bin/python' not in source
    assert 'sys.executable, "scripts/audit_asr_base_model_eval_inputs.py"' in source


def test_pre_gpu_cleanup_does_not_issue_nodegroup_mutation() -> None:
    source = (ROOT / "scripts/asr_base_model_pilot_live.py").read_text(encoding="utf-8")
    cleanup = source[source.index("    def cleanup_and_expiry(") :]
    assert 'if state.get("gpu_scaled"):' in cleanup
    mutation = cleanup.index("self.eks.update_nodegroup_config")
    guard = cleanup.index('if state.get("gpu_scaled"):')
    read_only = cleanup.index("group = self._nodegroup(GPU_NODEGROUP)", mutation)
    assert guard < mutation < read_only


def test_cleanup_restores_exact_prior_ecr_scanning_configuration() -> None:
    source = (ROOT / "scripts/asr_base_model_pilot_live.py").read_text(
        encoding="utf-8"
    )
    cleanup = source[source.index("    def cleanup_and_expiry(") :]
    assert 'state.get("scan_configuration_before") is not None' in cleanup
    assert "canonical_configuration(current)" in cleanup
    assert "canonical_configuration(before)" in cleanup
    assert "self._wait_registry_scanning_configuration(before)" in cleanup


def test_successor_requires_the_existing_empty_evaluation_repository() -> None:
    source = (ROOT / "scripts/asr_base_model_pilot_live.py").read_text(
        encoding="utf-8"
    )
    image_stage = source[
        source.index("    def image_publication_and_scan(") :
        source.index("    def artifact_stage(")
    ]
    assert "self.ecr.create_repository" not in image_stage
    assert "ECR_EVALUATION_REPOSITORY_ABSENT" in image_stage


def test_image_stage_uses_verified_multipart_publication_not_docker_push() -> None:
    source = (ROOT / "scripts/asr_base_model_pilot_live.py").read_text(
        encoding="utf-8"
    )
    image_stage = source[
        source.index("    def image_publication_and_scan(") :
        source.index("    def artifact_stage(")
    ]
    assert "publish_exact_image(" in image_stage
    assert '"docker", "push"' not in image_stage
    assert '"docker", "login"' not in image_stage
    assert "OciPublicationRefusal" in image_stage


def test_artifact_stage_wrapper_preserves_outer_and_nested_statuses(tmp_path: Path) -> None:
    from scripts.asr_base_model_pilot_runner import stage_artifact_stage

    current = json.loads((ROOT / "platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002I.json").read_bytes())
    operations, _ = build_rehearsal_operations(current)
    current_context = AttemptContext(attempt=10, bindings=current, receipts=ReceiptStore(tmp_path / "receipts", packet_sha256=ZERO, authorization_sha256="a" * 64), workdir=tmp_path)
    payload = stage_artifact_stage(operations, current_context)
    assert payload["status"] == "PASS_ARTIFACT_STAGE"
    assert payload["verification"]["status"] == "PASS_PRESTAGED_BUNDLE_VERIFY_ONLY"
    assert payload["artifact_upload_bytes"] == 0
    assert payload["local_model_bindings"] == {
        "key": (
            "research/asr-base-model/pilot/"
            "1cdca3e75195c7c7417550154e36a1f372715e17efd13c835c87ee503fa84eee/"
            "model-bindings.json"
        ),
        "version_id": "KbUgIfO3pnQuwyBSGSTcIGbYLRU8heRa",
        "sha256": "b66c1c7f34375df1352a2be74fd9f975f2911c4a5366ff83f311802709477f2c",
        "bytes": 1065,
    }
    assert (tmp_path / "asset-staging/model-bindings.json").read_bytes() == (
        ROOT / "tests/fixtures/asr_base_model_pilot/model-bindings-2026-001.json"
    ).read_bytes()


def test_scan_configuration_post_mutation_checks_use_stable_polling() -> None:
    source = (ROOT / "scripts/asr_base_model_pilot_live.py").read_text(
        encoding="utf-8"
    )
    assert "def _wait_registry_scanning_configuration(" in source
    assert "if stable == 2:" in source
    assert "self._wait_registry_scanning_configuration(updated)" in source
    assert "self._wait_registry_scanning_configuration(before)" in source


def test_untyped_pre_model_aws_exception_retains_safe_service_text() -> None:
    assert _safe_reason(ValueError("duplicate scan frequency from service")) == {
        "reason_code": "UNEXPECTED_STAGE_EXCEPTION",
        "safe_error_text": "duplicate scan frequency from service",
    }
