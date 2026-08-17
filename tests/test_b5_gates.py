"""Behavioral B5 refusal, termination and promotion-boundary tests.

Two worlds are exercised deliberately:

- The *promotion world* (`promotion_root` fixture) is a byte-identical rebuild
  of the registry exactly as the frozen B5 scope describes it, so every
  behavioral property of the refusal machinery stays covered.
- The *live tree* has since grown hand-authored data-scope languages that the
  frozen scope never covered, so the engine correctly fails closed there; that
  refusal is asserted explicitly rather than papered over.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
HISTORICAL_REPORT = (
    ROOT / "platform/evidence/b5/gate-reports/"
    "25217157215ea979440187aa050772ffdf248d75e1ae823d5dcb72cb9d8def30.json"
)
sys.path.insert(0, str(ROOT))

from pipeline.b5_gates import (
    FailClosedError,
    GateState,
    _absolute_cer_gate,
    _compare,
    _git_head,
    _verify_hash,
    checkpoint_termination_gate,
    evaluate_current_artifact,
    holdout_termination_gate,
    parse_state,
    post_conversion_termination_gate,
    resolve_thresholds,
    unique_termination_failures,
    write_content_addressed_report,
)
from pipeline.b5_promotion import (
    CANONICALIZATION,
    DRY_RUN_SIGNATURE_PURPOSE,
    SCHEMA_VERSION,
    SIGNING_ALGORITHM,
    attach_blocked_report_to_mlflow,
    build_current_artifact_dry_run_manifest,
    canonical_manifest_bytes,
    promotion_preflight,
    sign_manifest,
    sign_dry_run_manifest,
    ssm_dry_run_plan,
    verify_manifest,
    verify_dry_run_manifest,
    write_dry_run_manifest,
)
from scripts.attach_b5_report import SSE_KMS_KEY_ARN, _put_create_or_same


def _gates(report: dict, language: str) -> dict[str, dict]:
    return {row["gate"]: row for row in report["languages"][language]["gates"]}


PROMOTION_WORLD_LANGUAGES = {
    "acholi", "akan", "amharic", "english", "ewe", "french", "fula",
    "hausa", "igbo", "lingala", "luganda", "oromo", "pidgin", "shona",
    "swahili", "wolof", "yoruba",
}


def _control_referenced_paths(root: Path) -> set[str]:
    """Every platform file the frozen B5 controls bind by path (and hash)."""
    refs = {
        "platform/decisions/B5-AUTH-2026-001-refusal-engineering.json",
        # fixed list read by build_current_artifact_dry_run_manifest
        "platform/evidence/CAMPAIGNRUN-2026-012-failed.json",
        "platform/evidence/CAMPAIGNRUN-2026-013-passed.json",
        "platform/evidence/CAMPAIGNRUN-2026-014-passed.json",
        "platform/evidence/B5-MLFLOW-RUN-RESOLUTION-2026-001.json",
        "platform/evidence/VAL-2026-001-frozen-validation-sets.json",
        "platform/evidence/VAL-2026-003-lingala-post-selection-holdout.json",
        "platform/decisions/B4-SCOPE-2026-002-simplified-exit.json",
    }
    auth = json.loads((root / "platform/decisions/"
                       "B5-AUTH-2026-001-refusal-engineering.json").read_bytes())
    for binding in auth.get("b4_evidence_bindings", []):
        refs.add(binding["path"])
    scope = yaml.safe_load((root / "registry/gates/b5-scope-v1.yaml").read_text())
    for group in (scope.get("candidate_evidence") or {}).values():
        refs.add(group["path"])
    for group in (scope.get("decision_references") or {}).values():
        refs.add(group["path"])
    termination = yaml.safe_load(
        (root / "registry/gates/_termination-v1.yaml").read_text())
    refs.add(termination["decision"]["path"])
    return refs


@pytest.fixture(scope="module")
def promotion_root(tmp_path_factory) -> Path:
    """Rebuild the frozen promotion world the B5 controls still describe.

    Every file is a byte-identical copy, so all hash bindings hold; only the
    language registry is pruned back to the aliases the scope froze. The live
    tree meanwhile grew data-scope languages the scope never covered, which
    the engine refuses — see
    test_live_registry_growth_is_refused_fail_closed.
    """
    root = tmp_path_factory.mktemp("b5-promotion-world")
    shutil.copytree(ROOT / "registry", root / "registry")
    for path in (root / "registry/languages").glob("*.yaml"):
        if path.stem not in PROMOTION_WORLD_LANGUAGES:
            path.unlink()
    for rel in ("pipeline/b5_gates.py", "scripts/evaluate_gates.py"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, root / rel)
    for rel in sorted(_control_referenced_paths(ROOT)):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, root / rel)
    # Minimal read-only .git metadata so the engine binds the real HEAD.
    (root / ".git").mkdir()
    (root / ".git/HEAD").write_text(_git_head(ROOT) + "\n")
    return root


@pytest.fixture(scope="module")
def report(promotion_root) -> dict:
    report = evaluate_current_artifact(promotion_root)
    assert report["evaluation_purpose"] == "REFUSAL_ONLY_NEGATIVE_TEST_CASE", (
        report["overall_reason"])
    return report


@pytest.fixture(scope="module")
def historical_report() -> dict:
    raw = HISTORICAL_REPORT.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == (
        "25217157215ea979440187aa050772ffdf248d75e1ae823d5dcb72cb9d8def30")
    return json.loads(raw)


def test_exactly_five_gate_states_are_supported(report):
    assert {state.value for state in GateState} == {
        "PASS", "FAIL", "NOT_EVALUATED", "DEFERRED", "NOT_APPLICABLE"}
    observed = set(report["gate_state_counts"])
    assert observed == {state.value for state in GateState}
    assert report["gate_state_counts"]["FAIL"] == 0
    assert report["gate_state_counts"]["DEFERRED"] > 0
    assert report["gate_state_counts"]["NOT_EVALUATED"] > 0


def test_unknown_state_fails_closed():
    with pytest.raises(FailClosedError, match="unknown or malformed"):
        parse_state("SKIPPED")
    with pytest.raises(FailClosedError, match="unknown or malformed"):
        parse_state(None)


def test_missing_measurement_never_becomes_pass():
    row = _compare("asr.test", None, 0.2, "<=", [])
    assert row["state"] == "NOT_EVALUATED"
    assert row["required"] is True


def test_missing_provenance_returns_blocked_report(tmp_path):
    blocked = evaluate_current_artifact(tmp_path)
    assert blocked["overall"] == "BLOCKED"
    assert blocked["global_gates"][0]["state"] == "FAIL"
    assert blocked["engine_errors"]


def test_live_registry_growth_is_refused_fail_closed():
    """The live registry grew languages the frozen scope never covered; the
    engine must refuse the whole evaluation rather than improvise coverage."""
    live = evaluate_current_artifact()
    assert live["evaluation_purpose"] == "FAIL_CLOSED_ENGINE_ERROR"
    assert live["overall"] == "BLOCKED"
    assert live["languages"] == {}
    assert live["gate_state_counts"]["FAIL"] == 1
    assert any("promotion scope does not cover registry aliases" in error
               for error in live["engine_errors"])
    assert live["side_effects"]["models_registered"] == 0
    assert live["side_effects"]["b6_status"] == "BLOCKED"


def test_measured_promotion_group_stays_locked_until_reactivation(
        promotion_root, tmp_path):
    """A measured language cannot be evaluated by this engine build: the
    refusal-only evaluation hard-refuses a non-empty measured group. This is
    why the asr.absolute_cer wiring is asserted structurally rather than
    end-to-end below."""
    variant = tmp_path / "measured-variant"
    shutil.copytree(promotion_root, variant)
    scope_path = variant / "registry/gates/b5-scope-v1.yaml"
    scope = yaml.safe_load(scope_path.read_text())
    scope["promotion_languages"]["measured"] = ["yoruba"]
    scope["promotion_languages"]["not_evaluated"].remove("yoruba")
    scope_path.write_text(yaml.safe_dump(scope, sort_keys=False))
    blocked = evaluate_current_artifact(variant)
    assert blocked["evaluation_purpose"] == "FAIL_CLOSED_ENGINE_ERROR"
    assert any("measured promotion group is not empty" in error
               for error in blocked["engine_errors"])


def test_hash_mismatch_refuses(tmp_path):
    path = tmp_path / "evidence.json"
    path.write_text("{}")
    with pytest.raises(FailClosedError, match="hash mismatch"):
        _verify_hash(path, "0" * 64)


def test_threshold_inheritance_and_override_are_deterministic():
    lingala = resolve_thresholds("lingala")
    pidgin = resolve_thresholds("pidgin")
    assert lingala["values"]["asr"]["absolute_wer_max"] == 0.20
    assert lingala["value_sources"]["asr.absolute_wer_max"]["source"] == (
        "registry/gates/_defaults.yaml")
    assert pidgin["values"]["asr"]["code_switch_wer_ratio_max"] == 1.20
    assert pidgin["value_sources"]["asr.code_switch_wer_ratio_max"][
        "source"] == "registry/gates/pidgin-v1.yaml"


@pytest.mark.parametrize("language", ["yoruba", "igbo", "akan", "ewe"])
def test_tonal_languages_resolve_the_primary_cer_ceiling(language):
    resolved = resolve_thresholds(language)
    assert resolved["values"]["asr"]["absolute_cer_max"] == 0.10
    assert resolved["value_sources"]["asr.absolute_cer_max"]["source"] == (
        f"registry/gates/{language}-v1.yaml")


def test_all_five_tonal_gate_files_freeze_the_cer_ceiling():
    for language in ("yoruba", "igbo", "akan", "ewe", "yemba"):
        gate = yaml.safe_load(
            (ROOT / f"registry/gates/{language}-v1.yaml").read_text())
        assert gate["overrides"]["asr"]["absolute_cer_max"] == 0.10, language
        assert gate["overrides"]["asr"]["absolute_wer_max"] == 0.30, language


def test_yemba_thresholds_resolve_with_the_owner_approved_tonal_rule():
    """yemba was onboarded by owner approval 2026-08-17 ("approve the
    thresholds"): thresholds_ref is wired and the tonal rule binds —
    WER ceiling 0.30 AND the 0.10 CER ceiling that forces tone fidelity."""
    values = resolve_thresholds("yemba")["values"]["asr"]
    assert values["absolute_wer_max"] == 0.30
    assert values["absolute_cer_max"] == 0.10


@pytest.mark.parametrize("language", ["lingala", "hausa", "swahili", "wolof"])
def test_non_tonal_languages_leave_the_cer_ceiling_null(language):
    resolved = resolve_thresholds(language)
    assert resolved["values"]["asr"]["absolute_cer_max"] is None
    assert resolved["value_sources"]["asr.absolute_cer_max"]["source"] == (
        "registry/gates/_defaults.yaml")


def test_absolute_cer_gate_skips_only_a_null_registry_threshold():
    assert _absolute_cer_gate("asr.absolute_cer", 0.08, None, []) is None
    absent = _absolute_cer_gate("asr.absolute_cer", None, 0.10, [])
    assert absent["state"] == "NOT_EVALUATED"
    assert absent["required"] is True
    assert absent["threshold"] == {"operator": "<=", "value": 0.10}


def test_absolute_cer_gate_enforces_the_tonal_ceiling():
    passing = _absolute_cer_gate("asr.absolute_cer", 0.0999, 0.10, [])
    exact = _absolute_cer_gate("asr.absolute_cer", 0.10, 0.10, [])
    failing = _absolute_cer_gate("asr.absolute_cer", 0.1001, 0.10, [])
    assert passing["state"] == "PASS"
    assert exact["state"] == "PASS"
    assert failing["state"] == "FAIL"
    assert failing["measurement"] == 0.1001
    assert failing["threshold"] == {"operator": "<=", "value": 0.10}


def test_absolute_cer_gate_malformed_values_fail_closed():
    assert _absolute_cer_gate(
        "asr.absolute_cer", float("nan"), 0.10, [])["state"] == "FAIL"
    assert _absolute_cer_gate(
        "asr.absolute_cer", "0.05", 0.10, [])["state"] == "FAIL"
    assert _absolute_cer_gate(
        "asr.absolute_cer", 0.05, "0.10", [])["state"] == "FAIL"


def test_absolute_cer_gate_is_wired_into_both_wer_measurement_sites():
    """CLAUDE-REVIEW-2026-08-16-035: absolute_cer_max was resolved and carried
    but never compared. The measured branch is unreachable until B5
    reactivation (the engine refuses a non-empty measured group), so the
    wiring is asserted structurally so the gate cannot silently vanish
    again."""
    source = (ROOT / "pipeline/b5_gates.py").read_text()
    assert '"asr.absolute_cer"' in source
    assert '"asr.untouched_holdout.absolute_cer"' in source
    assert source.count("_absolute_cer_gate(") == 3  # definition + two sites


def test_current_artifact_is_blocked_with_no_engine_error(report):
    assert report["overall"] == "BLOCKED"
    assert report["engine_errors"] == []
    assert report["candidate"]["promotable"] is False
    assert report["side_effects"]["models_registered"] == 0
    assert report["side_effects"]["objects_copied_to_approved_asr"] == 0
    assert report["side_effects"]["b6_status"] == "BLOCKED"


def test_git_commit_resolves_without_git_binary(monkeypatch):
    def unavailable(*args, **kwargs):
        raise FileNotFoundError("git intentionally absent")

    monkeypatch.setattr("pipeline.b5_gates.subprocess.run", unavailable)
    assert len(_git_head(ROOT)) == 40


@pytest.mark.parametrize("language", ["lingala", "luganda", "oromo"])
def test_preserved_absolute_wer_failure_cannot_be_overridden_by_relative_gain(
        historical_report, language):
    gates = _gates(historical_report, language)
    assert gates["asr.relative_wer_gain"]["state"] == "PASS"
    assert gates["asr.absolute_wer"]["state"] == "FAIL"
    assert gates["asr.absolute_wer"]["threshold"]["value"] == 0.20
    assert historical_report["languages"][language]["state"] == "FAIL"


def test_preserved_untouched_lingala_holdout_is_absolute_wer_fail(
        historical_report):
    gates = _gates(historical_report, "lingala")
    assert gates["asr.untouched_holdout.relative_wer_gain"]["state"] == "PASS"
    assert gates["asr.untouched_holdout.absolute_wer"]["state"] == "FAIL"
    assert gates["termination.untouched_lingala_holdout"]["state"] == "PASS"


@pytest.mark.parametrize("language", ["hausa", "igbo", "pidgin", "swahili", "yoruba"])
def test_unevaluated_languages_are_neither_fail_nor_pass(report, language):
    assert report["languages"][language]["state"] == "NOT_EVALUATED"
    assert _gates(report, language)["promotion_quality_evidence"]["state"] == (
        "NOT_EVALUATED")


@pytest.mark.parametrize("language", [
    "acholi", "akan", "amharic", "ewe", "fula", "shona",
    "lingala", "luganda", "oromo",
])
def test_deferred_languages_keep_reason_and_decision_reference(report, language):
    row = _gates(report, language)["promotion_scope"]
    assert report["languages"][language]["state"] == "DEFERRED"
    assert row["state"] == "DEFERRED"
    assert "deferred" in row["reason"].lower()
    assert "reactivation condition" in row["reason"].lower()
    assert row["evidence"][0]["path"].endswith(
        "B4-B5-SCOPE-2026-003-language-deferral.json")


def test_global_replay_and_code_switch_are_not_evaluated(report):
    gates = {row["gate"]: row for row in report["global_gates"]}
    assert gates["english_french_replay_regression"]["state"] == "NOT_EVALUATED"
    assert gates["code_switch_evaluation"]["state"] == "NOT_EVALUATED"
    memory = gates["peak_l4_gpu_memory"]
    assert memory["state"] == "NOT_EVALUATED" and memory["required"] is False
    assert "on-disk" in memory["reason"]


@pytest.mark.parametrize("language", ["lingala", "luganda", "oromo", "hausa"])
def test_no_current_production_is_not_applicable_only_when_proven(report, language):
    row = _gates(report, language)["current_production_comparison"]
    assert row["state"] == "NOT_APPLICABLE"
    lang = yaml.safe_load((ROOT / f"registry/languages/{language}.yaml").read_text())
    assert lang["asr"]["artifact"] is None
    assert lang["asr"]["approved_version"] is None
    assert any(e["path"].endswith(f"languages/{language}.yaml") for e in row["evidence"])


def test_decode_strategy_fails_closed_without_unique_b4_evidence(report):
    for language in ("lingala", "luganda", "oromo"):
        assert report["languages"][language]["state"] == "DEFERRED"
        assert _gates(report, language)["promotion_scope"]["state"] == (
            "DEFERRED")
    approvals = yaml.safe_load((ROOT / "registry/decode-approvals/v1.yaml").read_text())
    assert approvals["languages"]["pidgin"]["promotion_state"] == "NOT_EVALUATED"
    assert approvals["languages"]["pidgin"][
        "historical_provisional_configuration"]["provisional"] is True


def test_both_termination_conditions_count_once_by_audio_checksum():
    checksum = "a" * 64
    failures = unique_termination_failures([
        {"audio_checksum_sha256": checksum, "missing_eos": True, "cap_hit": True},
        {"audio_checksum_sha256": checksum, "missing_eos": True, "cap_hit": False},
    ])
    assert failures == {checksum}


def test_checkpoint_selection_tolerance_is_exact():
    one = checkpoint_termination_gate([
        {"checkpoint": 100, "failures": ["a" * 64]}
    ])
    two = checkpoint_termination_gate([
        {"checkpoint": 100, "failures": ["a" * 64, "b" * 64]}
    ])
    assert one["state"] == "PASS"
    assert two["state"] == "FAIL"


def test_repeated_checkpoint_checksum_fails_immediately_and_blocks_later_work():
    row = checkpoint_termination_gate([
        {"checkpoint": 100, "failures": ["a" * 64]},
        {"checkpoint": 200, "failures": ["a" * 64]},
        {"checkpoint": 300, "failures": []},
    ])
    assert row["state"] == "FAIL"
    assert "recurred" in row["reason"]
    assert "Subsequent optimization" in row["reason"]


def test_untouched_holdout_is_strict_zero():
    assert holdout_termination_gate([])["state"] == "PASS"
    assert holdout_termination_gate(["a" * 64])["state"] == "FAIL"


def test_post_conversion_checksum_continuity_and_count():
    assert post_conversion_termination_gate(["a" * 64], ["a" * 64])[
        "state"] == "PASS"
    assert post_conversion_termination_gate([], ["a" * 64])[
        "state"] == "FAIL"
    assert post_conversion_termination_gate(["a" * 64], ["b" * 64])[
        "state"] == "FAIL"


def test_generated_language_yaml_is_regenerated_from_sources():
    """The generator owns exactly the sources.yaml scope; every other
    registry language is a hand-authored addition and must stay hand-owned
    (no GENERATED banner it did not earn)."""
    sources = yaml.safe_load((ROOT / "registry/sources.yaml").read_text())
    generated = set(sources["scope"])
    for path in (ROOT / "registry/languages").glob("*.yaml"):
        text = path.read_text()
        if path.stem in generated:
            assert text.startswith(
                "# GENERATED by scripts/generate_languages.py"), path
        else:
            assert not text.startswith("# GENERATED"), path
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/generate_languages.py"), "--check"],
        cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_generated_scope_keeps_nine_deferred_and_five_not_evaluated():
    deferred = {
        "acholi", "akan", "amharic", "ewe", "fula", "shona",
        "lingala", "luganda", "oromo",
    }
    not_evaluated = {"hausa", "igbo", "pidgin", "swahili", "yoruba"}
    for language in deferred | not_evaluated:
        doc = yaml.safe_load(
            (ROOT / f"registry/languages/{language}.yaml").read_text())
        want = "DEFERRED" if language in deferred else "NOT_EVALUATED"
        assert doc["b4_b5_scope"]["promotion_state"] == want
    sources = yaml.safe_load((ROOT / "registry/sources.yaml").read_text())
    assert set(sources["scope"]) == {
        "swahili", "hausa", "yoruba", "amharic", "oromo", "igbo",
        "lingala", "shona", "wolof", "fula", "pidgin", "akan", "ewe",
        "luganda", "acholi", "english", "french",
    }
    assert set(sources["scope"]) <= set(sources["languages"])


def test_artifact_and_approved_version_remain_unchanged_and_empty():
    for path in (ROOT / "registry/languages").glob("*.yaml"):
        lang = yaml.safe_load(path.read_text())
        assert lang["asr"]["artifact"] is None
        assert lang["asr"]["approved_version"] is None


def test_deterministic_gate_report_hash_is_identical_twice(tmp_path):
    synthetic = {
        "schema_version": "writer-test-only",
        "overall": "BLOCKED",
        "reason": "Tests content addressing without regenerating B5.1 evidence.",
    }
    first = write_content_addressed_report(synthetic, tmp_path)
    second = write_content_addressed_report(synthetic, tmp_path)
    assert first["sha256"] == second["sha256"]
    assert first["created"] is True and second["created"] is False
    assert Path(first["path"]).read_bytes() == Path(second["path"]).read_bytes()


def _complete_manifest() -> dict:
    h = "a" * 64
    return {
        "schema_version": SCHEMA_VERSION,
        "canonicalization": CANONICALIZATION,
        "decision": {"outcome": "PASS"},
        "artifact": {
            "tree_sha256": h,
            "base_model_revision": "openai/whisper-large-v3@0123456789abcdef",
            "tokenizer_revision": "0123456789abcdef",
            "processor_revision": "0123456789abcdef",
            "precision": "float16",
            "selected_checkpoint": 400,
            "adapter_sha256": h,
            "adapter_tree_sha256": h,
            "destination_prefix": "s3://medzen-speech/approved/asr/v8/",
        },
        "dataset": {
            "fingerprint": h,
            "adoption_record": {"path": "s3://medzen-speech/curated/v3/adoption.json",
                                "sha256": h},
        },
        "evaluations": {
            "frozen_manifests": [{"path": "s3://medzen-speech/eval/lingala/v2/manifest.jsonl",
                                   "sha256": h}],
            "gate_report": {"path": "s3://medzen-speech/candidates/gates/report.json",
                            "sha256": h},
            "lingala_holdout": {"path": "s3://medzen-speech/eval/lingala/v2/holdout.json",
                                "sha256": h},
        },
        "provenance": {
            "git_commit": "b" * 40,
            "bundle_sha256": h,
            "container_image_digest": "sha256:" + h,
            "spot_resume_evidence": {"path": "s3://medzen-speech/candidates/spot.json",
                                     "sha256": h},
        },
        "decode": {"configuration": {"mode": "auto", "token_cap": 440}},
        "scope": {"deviations": ["owner-decision-v2"]},
        "mlflow": {"run_id": "1" * 32},
        "approval": {"identity": "platform-owner-approval-2026-999",
                     "timestamp_utc": "2026-08-03T00:00:00Z"},
    }


def test_manifest_canonicalization_is_deterministic_and_complete():
    manifest = _complete_manifest()
    first = canonical_manifest_bytes(manifest)
    second = canonical_manifest_bytes(json.loads(json.dumps(manifest)))
    assert first == second
    assert b" " not in first
    broken = _complete_manifest()
    del broken["mlflow"]["run_id"]
    with pytest.raises(FailClosedError, match="mlflow.run_id"):
        canonical_manifest_bytes(broken)
    floats = _complete_manifest()
    floats["artifact"]["latency"] = 0.9252
    with pytest.raises(FailClosedError, match="floating-point"):
        canonical_manifest_bytes(floats)


def _blocked_dry_run_manifest() -> dict:
    manifest = _complete_manifest()
    manifest["decision"] = {
        "outcome": "BLOCKED", "purpose": DRY_RUN_SIGNATURE_PURPOSE}
    manifest["artifact"]["destination_prefix"] = (
        "s3://medzen-speech/candidates/b5-dry-run/test/")
    return manifest


def test_dry_run_manifest_is_content_addressed_and_never_targets_approved(tmp_path):
    manifest = _blocked_dry_run_manifest()
    first = write_dry_run_manifest(manifest, tmp_path)
    second = write_dry_run_manifest(manifest, tmp_path)
    assert first["sha256"] == second["sha256"]
    assert json.loads(Path(first["path"]).read_bytes())["decision"][
        "outcome"] == "BLOCKED"
    bad = _blocked_dry_run_manifest()
    bad["artifact"]["destination_prefix"] = "s3://medzen-speech/approved/asr/v1/"
    with pytest.raises(FailClosedError, match="dry-run destination"):
        write_dry_run_manifest(bad, tmp_path)


class _FakeKms:
    def __init__(self, keys: set[str]):
        self.keys = keys

    @staticmethod
    def _signature(key: str, message: bytes) -> bytes:
        return hashlib.sha256(key.encode() + message).digest()

    def sign(self, **kwargs):
        assert kwargs["SigningAlgorithm"] == SIGNING_ALGORITHM
        assert kwargs["MessageType"] == "DIGEST"
        if kwargs["KeyId"] not in self.keys:
            raise RuntimeError("unknown key")
        return {"KeyId": kwargs["KeyId"],
                "Signature": self._signature(kwargs["KeyId"], kwargs["Message"])}

    def verify(self, **kwargs):
        valid = (kwargs["KeyId"] in self.keys
                 and kwargs["Signature"] == self._signature(
                     kwargs["KeyId"], kwargs["Message"]))
        return {"KeyId": kwargs["KeyId"], "SignatureValid": valid}


def test_kms_sign_verify_contract_tamper_and_wrong_key_refuse():
    key = "arn:aws:kms:eu-central-1:558069890522:key/11111111-1111-1111-1111-111111111111"
    other = "arn:aws:kms:eu-central-1:558069890522:key/22222222-2222-2222-2222-222222222222"
    kms = _FakeKms({key, other})
    manifest = _complete_manifest()
    envelope = sign_manifest(kms, manifest, key)
    assert verify_manifest(kms, manifest, envelope) is True
    tampered = _complete_manifest()
    tampered["artifact"]["selected_checkpoint"] = 500
    assert verify_manifest(kms, tampered, envelope) is False
    wrong = dict(envelope, key_arn=other)
    assert verify_manifest(kms, manifest, wrong) is False
    assert verify_manifest(kms, manifest, dict(
        envelope, message_type="RAW")) is False
    assert verify_manifest(kms, manifest, dict(
        envelope, canonicalization="unknown")) is False
    assert verify_manifest(kms, manifest, dict(
        envelope, signature_encoding="hex")) is False


def test_dry_run_signature_cannot_verify_as_production_signature():
    key = "arn:aws:kms:eu-central-1:558069890522:key/11111111-1111-1111-1111-111111111111"
    kms = _FakeKms({key})
    manifest = _blocked_dry_run_manifest()
    envelope = sign_dry_run_manifest(kms, manifest, key)
    assert verify_dry_run_manifest(kms, manifest, envelope) is True
    assert verify_manifest(kms, manifest, envelope) is False
    with pytest.raises(FailClosedError, match="signed PASS"):
        sign_manifest(kms, manifest, key)


def test_current_artifact_dry_run_binds_known_gaps_and_stays_blocked(
        report, promotion_root, tmp_path):
    receipt = write_content_addressed_report(report, tmp_path)
    manifest = build_current_artifact_dry_run_manifest(
        report, Path(receipt["path"]), promotion_root)
    assert manifest["decision"]["outcome"] == "BLOCKED"
    assert manifest["artifact"]["processor_revision"]["state"] == "NOT_EVALUATED"
    assert manifest["decode"]["configuration"]["state"] == "NOT_EVALUATED"
    assert manifest["dataset"]["adoption_record"]["sha256"]["state"] == (
        "NOT_EVALUATED")
    with pytest.raises(FailClosedError, match="signed PASS"):
        canonical_manifest_bytes(manifest)


def test_current_blocked_report_never_authorizes_publication(report):
    manifest = _complete_manifest()
    decision = promotion_preflight(
        manifest, report, signature_valid=True,
        destination_exists=False, if_none_match="*")
    assert decision["authorized"] is False
    assert "gate report outcome is not PASS" in decision["reasons"]


def _statement(policy: dict, sid: str) -> dict:
    return next(row for row in policy["Statement"] if row.get("Sid") == sid)


def test_trainer_and_builder_cannot_write_approved_paths():
    for filename in ("medzen-trainer-role.json", "medzen-builder-role.json"):
        policy = json.loads((ROOT / "platform/iam" / filename).read_text())
        approved_allows = [row for row in policy["Statement"]
                           if row["Effect"] == "Allow"
                           and "s3:PutObject" in row.get("Action", [])
                           and any("/approved/" in resource
                                   for resource in row.get("Resource", []))]
        assert approved_allows == []
    bucket_policy = json.loads((
        ROOT / "platform/promotion/approved-bucket-policy.template.json").read_text())
    deny = _statement(bucket_policy, "DenyTrainerAndBuilderApprovedWrites")
    assert "s3:PutObject" in deny["Action"]
    assert deny["Resource"] == "arn:aws:s3:::medzen-speech/approved/*"


def test_promotion_role_is_confined_and_requires_complete_pass_tags():
    policy = json.loads((ROOT / "platform/iam/medzen-b5-promotion-role.template.json").read_text())
    allow = _statement(policy, "WriteOnlyCompleteSignedApprovedAsrObjects")
    assert allow["Resource"] == ["arn:aws:s3:::medzen-speech/approved/asr/*"]
    tags = allow["Condition"]["StringEquals"]
    assert tags["s3:RequestObjectTag/medzen-gate"] == "PASS"
    assert tags["s3:RequestObjectTag/medzen-manifest-state"] == "complete"
    deny = _statement(policy, "DenyWritesOutsideApprovedAsr")
    assert deny["NotResource"] == ["arn:aws:s3:::medzen-speech/approved/asr/*"]

    trust = json.loads((
        ROOT / "platform/iam/medzen-b5-promotion-trust.template.json").read_text())
    denied = _statement(trust, "DenyTrainerBuilderAndRuntimeAssumption")
    principals = denied["Principal"]["AWS"]
    assert any("medzen-trainer-role" in principal for principal in principals)
    assert any("medzen-builder-role" in principal for principal in principals)


def test_dry_run_role_cannot_write_approved_and_requires_create_only_header():
    policy = json.loads((
        ROOT / "platform/iam/medzen-b5-promotion-dry-run-role.template.json").read_text())
    allow = _statement(policy, "WriteOnlyCreateOnlyBlockedDryRunObjects")
    assert allow["Resource"] == (
        "arn:aws:s3:::medzen-speech/candidates/b5-dry-run/*")
    conditions = allow["Condition"]["StringEquals"]
    assert conditions["s3:if-none-match"] == "*"
    assert conditions["s3:RequestObjectTag/medzen-gate"] == "BLOCKED"
    serialized = json.dumps(policy)
    assert "approved/asr" not in serialized
    deny = _statement(policy, "DenyWritesOutsideDryRunPrefix")
    assert deny["NotResource"].endswith("/candidates/b5-dry-run/*")


def test_bucket_boundary_enforces_create_only_complete_signed_writes():
    policy = json.loads((
        ROOT / "platform/promotion/approved-bucket-policy.template.json").read_text())
    create_only = _statement(
        policy, "DenyPromotionWritesWithoutCreateOnlyPrecondition")
    assert create_only["Condition"]["StringNotEquals"][
        "s3:if-none-match"] == "*"
    expected = {
        "DenyPromotionWritesWithoutPassTag":
            ("s3:RequestObjectTag/medzen-gate", "PASS"),
        "DenyPromotionWritesWithoutCompleteManifestTag":
            ("s3:RequestObjectTag/medzen-manifest-state", "complete"),
        "DenyPromotionWritesWithoutContentAddressTag":
            ("s3:RequestObjectTag/medzen-content-addressed", "true"),
    }
    for sid, (key, value) in expected.items():
        assert _statement(policy, sid)["Condition"]["StringNotEquals"][key] == value


def test_kms_policy_pins_digest_message_type_and_algorithm():
    policy = json.loads((
        ROOT / "platform/promotion/kms-key-policy.template.json").read_text())
    use = _statement(policy, "PromotionRoleMaySignAndVerifyOnly")
    conditions = use["Condition"]["StringEquals"]
    assert conditions["kms:SigningAlgorithm"] == SIGNING_ALGORITHM
    assert conditions["kms:MessageType"] == "DIGEST"


def test_runtime_roles_remain_read_only_on_approved_paths():
    for filename in ("medzen-asr-role.json", "medzen-loader-role.json"):
        policy = json.loads((ROOT / "platform/iam" / filename).read_text())
        actions = {action for row in policy["Statement"]
                   for action in row.get("Action", [])}
        assert "s3:GetObject" in actions
        assert "s3:PutObject" not in actions
        assert "s3:DeleteObject" not in actions


class _RunInfo:
    def __init__(self, run_id: str):
        self.run_id = run_id


class _Run:
    def __init__(self, run_id: str):
        self.info = _RunInfo(run_id)


class _FakeMlflowClient:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.attachments: list[tuple] = []

    def get_run(self, run_id):
        return _Run(run_id)

    def search_registered_models(self):
        return []

    def log_artifact(self, *args, **kwargs):
        self.attachments.append((args, kwargs))


def test_mlflow_report_attachment_does_not_register_a_model(report, tmp_path):
    receipt = write_content_addressed_report(report, tmp_path)
    run_id = "1" * 32
    client = _FakeMlflowClient(run_id)
    attached = attach_blocked_report_to_mlflow(
        client, run_id, Path(receipt["path"]), receipt["sha256"])
    assert attached["registered_models_before"] == 0
    assert attached["registered_models_after"] == 0
    assert len(client.attachments) == 1


class _FakeS3:
    def __init__(self):
        self.request = None

    def put_object(self, **kwargs):
        self.request = kwargs
        return {"VersionId": "version-1"}


def test_mlflow_attachment_write_is_create_only_and_pins_sse_kms():
    client = _FakeS3()
    version = _put_create_or_same(
        client, "mlflow/artifacts/run/report.json", b"{}", "application/json",
        "medzen-gate=BLOCKED")
    assert version == "version-1"
    assert client.request["IfNoneMatch"] == "*"
    assert client.request["ServerSideEncryption"] == "aws:kms"
    assert client.request["SSEKMSKeyId"] == SSE_KMS_KEY_ARN
    assert client.request["ChecksumAlgorithm"] == "SHA256"
    assert client.request["Metadata"]["medzen-sha256"] == hashlib.sha256(
        b"{}").hexdigest()


def test_ssm_dry_run_cannot_alter_production_serving_state():
    plan = ssm_dry_run_plan({"gate-report": "blocked"}, {})
    assert plan["writes_performed"] == 0
    assert plan["production_namespace_changes"] == 0
    assert plan["namespace"] == "/medzen/b5-test"
    with pytest.raises(FailClosedError, match="production serving namespace"):
        ssm_dry_run_plan({}, {}, namespace="/medzen/registry")
