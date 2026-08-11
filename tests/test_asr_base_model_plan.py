import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "platform" / "decisions" / "PLAN-2026-015-asr-base-model-evaluation.md"
EVIDENCE = (
    ROOT
    / "platform"
    / "evidence"
    / "B6-ASR-BASE-MODEL-DISCOVERY-2026-001.json"
)
SOURCE_EVIDENCE = (
    ROOT
    / "platform"
    / "evidence"
    / "B6-ASR-BASE-MODEL-SOURCES-2026-001.json"
)


def text() -> str:
    return PLAN.read_text(encoding="utf-8")


def evidence() -> dict:
    return json.loads(EVIDENCE.read_text(encoding="utf-8"))


def test_plan_is_non_executable_and_preserves_governance() -> None:
    value = text()
    assert "NO MODEL SCORING AUTHORIZED" in value
    assert "B5 remains `BLOCKED`" in value
    assert "absolute WER maximum remains `0.20`" in value
    assert "No language is reactivated" in value
    assert "Nothing in this plan is AWS authorization" in value


def test_plan_binds_data_and_discovery_hashes() -> None:
    value = text()
    raw = EVIDENCE.read_bytes()
    assert "e04a4140491d7a5d0a389403bcc3c20eed3ca713" in value
    assert "2906ddd24e16f2e2c30d9ecd324e47733ba6ac78" in value
    assert "f89b9e432a88db7eebe618c617f9c36f49fa2678b291ce16bebafa085b68c953" in value
    assert hashlib.sha256(raw).hexdigest() == (
        "dd6ca577b0394fae5ea274ac9da631b3d6e4ab7a5a8af748e876e4763be29d06"
    )
    assert hashlib.sha256(raw).hexdigest() in value
    source_raw = SOURCE_EVIDENCE.read_bytes()
    assert hashlib.sha256(source_raw).hexdigest() == (
        "adb88498d996ccafd7cb42beb2c72780d2593ca5b0e8bcd5d793b09be46c2794"
    )
    assert hashlib.sha256(source_raw).hexdigest() in value


def test_discovery_refuses_current_input_freeze() -> None:
    record = evidence()
    assert record["status"] == "REFUSED_INPUT_FREEZE"
    assert record["inventory"]["manifests"] == 64
    assert record["inventory"]["rows"] == 24232
    assert record["inventory"]["languages"] == 49
    assert record["prospective_independent_suite"]["manifests"] == 54
    assert record["prospective_independent_suite"]["rows"] == 23770
    assert record["prospective_independent_suite"]["languages"] == 47
    assert record["inventory"]["selected_manifest_generations"]["r2"] == 12
    assert record["inventory"]["selected_manifest_generations"]["original"] == 52
    assert record["execution"]["aws_mutations"] == 0
    assert record["execution"]["zero_shot_scoring_started"] is False


def test_every_discovered_hard_finding_is_named_in_plan() -> None:
    value = text()
    record = evidence()
    assert record["hard_findings"] == ["DUPLICATE_AUDIO_SHA256"]
    assert len(record["duplicates"]) == 2
    assert "6c472e2ab556b66022b048165929c7a9a6a0ff67d2b0370a5b17d1e1255a4d94" in value
    assert "4dc52a35b08269e38f8c54627c09f0292bdf098ed6fbb19f0d7eee3d6e23bb3d" in value
    assert record["policy_findings"]["asr_train_rows_in_eval"] == 0
    assert record["policy_findings"]["missing_license_tier_rows"] == 0
    assert record["policy_findings"]["non_test_split_rows"] == 0
    assert "evaluation boundary is the manifest namespace `eval/<language>/**`" in value
    assert "SOREVA source tally is already corrected to 39 languages / 5,483 clips" in value


def test_candidates_and_sources_are_exactly_pinned() -> None:
    value = text()
    for required in (
        "openai/whisper-large-v3",
        "06f233fe06e710322aca913c1bc4249a0d71fce1",
        "omniASR_CTC_1B_v2",
        "omniASR_LLM_1B_v2",
        "145a12a668aace6c1d0d290128c1225571fc1955",
        "6b8e811143603463c371c23464ff1946a52f876e6b6a62c5fb3deee6e39ab6d4",
        "af4d63febb0569831210e470b256ec70dc3a55065756c21c1f514d0001f283ed",
        "675b8a263aed48269020d4e9f06b3063d5b4e0d5399b2c3e0e06160e08d24f8e",
        "354f981756aa8f41591ea363e45b9c4eba1ec5144c2273af82e747efbb08919c",
        "cceb4d9ebac3d168a6af6b26c62ce11bafc562b38976c6bfa87e7d60422c6da5",
        "8aa11a1092142ef472537476ef6e76541123e2f0d789b79f3ebd119008240b1e",
    ):
        assert required in value
    assert "multipart ETag, is not a SHA-256" in value


def test_evaluation_modes_forbid_proxy_conditioning() -> None:
    value = text()
    assert "mode A, comparable primary: unconditioned/audio-only" in value
    assert "mode B, secondary: exact language-conditioned" in value
    assert "Meta CTC is reported `NOT_APPLICABLE` for conditioned mode" in value
    assert "no prompts, context examples, proxy languages" in value
    assert "No proxy token may be invented" in value


def test_metrics_include_quality_reliability_and_serving_fit() -> None:
    value = text()
    for required in (
        "word error rate (WER)",
        "character error rate (CER)",
        "language-macro averages",
        "EOS/cap failures",
        "peak L4 GPU memory",
        "median, p95, and real-time factor",
        "file and streaming contracts",
        "license and attribution obligations",
    ):
        assert required in value


def test_selection_does_not_conflate_research_with_promotion() -> None:
    value = text()
    assert "better future training base while still being explicitly ineligible for promotion" in value
    assert "OWNER_DECISION_REQUIRED" in value
    assert "cannot replace the common unconditioned comparison" in value
    assert "None of those states changes B5" in value


def test_model_downloads_and_compute_require_later_authorization() -> None:
    value = text()
    assert "Checkpoint SHA-256" in value
    assert "`NOT_MEASURED`" not in value
    assert "mirrored create-only into a controlled, non-serving staging area" in value
    assert "After a separate execution packet" in value
    assert "The packet must stop before the full suite" in value
    assert "full run needs a second authorization" in value


def test_branch_separation_is_explicit() -> None:
    value = text()
    assert "separate decision track from B7" in value
    assert "both now share unified mainline commit" in value


def test_owner_eval_boundary_ruling_is_bound() -> None:
    value = text()
    record = evidence()
    assert "prior 13,077-row path finding is withdrawn prospectively" in value
    assert record["evaluation_boundary"]["rule"] == "eval/<language>/**"
    assert record["evaluation_boundary"]["applies_to"] == "manifest namespace"
    assert record["evaluation_boundary"]["audio_object_location_is_not_scope_signal"] is True
    assert "EVAL_MANIFEST_REFERENCES_NON_EVAL_AUDIO" not in record["hard_findings"]
