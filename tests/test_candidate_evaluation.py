"""Tests for the candidate evaluator and for the defect that made it necessary.

The B4 candidate trained to a falling loss and then produced 722% WER. Nothing
in the training path was wrong about provenance, licensing or exclusions --
what was wrong was the objective itself, and no guard looked at that. These
tests pin both the evaluator's guarantees and the defect's exact shape.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / "scripts/evaluate_candidate.py"
TRAIN = ROOT / "pipeline/train_asr.py"
DECISION = ROOT / "platform/decisions/EVAL-2026-001-b4-candidate-failed.json"


@pytest.fixture(scope="module")
def decision():
    return json.loads(DECISION.read_bytes())


# --------------------------------------------------------------------------- #
# THE DEFECT — characterisation, not aspiration
#
# These assert what the code does TODAY. They will fail when the bug is fixed,
# which is the point: the fix must be deliberate and must update them.
# --------------------------------------------------------------------------- #
WHISPER_SOT = 50258          # <|startoftranscript|>
WHISPER_EOT = 50257          # <|endoftext|>, and ALSO whisper's bos_token_id


def test_whisper_bos_token_is_not_startoftranscript():
    """The trap. Whisper's bos_token is <|endoftext|>, so a guard written as
    `labels[0] == bos_token_id` compares 50258 against 50257 and is dead."""
    assert WHISPER_SOT != WHISPER_EOT


def test_collator_prefix_strip_is_currently_dead_code():
    src = TRAIN.read_text()
    assert "labels[:, 0] == processor.tokenizer.bos_token_id" in src, \
        "if this changed, the fix landed -- update this test deliberately"
    # the condition, simulated with the real ids
    labels_first_token = WHISPER_SOT
    bos_token_id = WHISPER_EOT
    assert (labels_first_token == bos_token_id) is False, \
        "the strip never fires, so the 4-token prefix stays in the labels"


def test_labels_therefore_carry_the_full_prefix():
    """Consequence: HF derives decoder_input_ids = shift_tokens_right(labels,
    decoder_start_token_id=50258), duplicating SOT and shifting every text
    token one position later than it appears at inference."""
    labels = [WHISPER_SOT, 50259, 50360, 50364, 675, 1913, WHISPER_EOT]
    decoder_input = [WHISPER_SOT] + labels[:-1]
    assert decoder_input[:2] == [WHISPER_SOT, WHISPER_SOT], "SOT duplicated"
    assert len(decoder_input) == len(labels)
    # the model is trained to predict SOT from SOT at position 0
    assert labels[0] == WHISPER_SOT and decoder_input[0] == WHISPER_SOT


def test_max_output_length_matches_the_decoder_cap_exactly():
    """444 observed + 4 prefix tokens = 448 = max_target_positions. The model
    ran to the hard cap without emitting EOS: termination failure, not merely
    bad quality."""
    assert 444 + 4 == 448


def test_no_training_guard_examines_label_content():
    """Length was checked; content and alignment were not. Recorded so the gap
    is not rediscovered by another failed run."""
    src = TRAIN.read_text()
    assert "max_target_positions" in src, "length guard exists"
    assert "label check" in src
    # no alignment assertion exists yet
    assert "decoder_input_ids" not in src


# --------------------------------------------------------------------------- #
# the failure decision record
# --------------------------------------------------------------------------- #
def test_failure_is_recorded_and_blocks_promotion(decision):
    assert decision["record"] == "FAILED_EVALUATION"
    assert decision["status"] == "rejected"
    c = decision["consequences"]
    assert c["promotion"] == "blocked" and c["registration"] == "blocked"
    assert c["b5"] == "paused"
    assert c["iam_guardrails"] == "unchanged"
    assert "candidates/" in c["artifacts"]


def test_failure_record_links_the_run_without_altering_it(decision):
    cand = decision["candidate"]
    assert cand["training_run_id"] == "23868bab2d8448759fc1b9ed26156952"
    assert cand["adapter_sha256"] == (
        "17e1b7381b7b3fdb362ecb692d72b92a2dc295d7ee79ff6367a8d6a9c7cd3195")
    assert "NOT amended" in decision["consequences"]["training_evidence"]
    assert "not modified" in decision["consequences"]["mlflow"]
    # and the original record is genuinely untouched
    tr = json.loads(
        (ROOT / "platform/evidence/training-run-23868bab2d84.json").read_bytes())
    assert tr["status"] == "COMPLETED"
    assert tr["artifacts"]["adapter_sha256"] == cand["adapter_sha256"]


def test_external_metrics_are_labelled_as_not_yet_reproduced(decision):
    """Recording someone else's numbers as verified fact would be the same
    class of error the evaluator exists to prevent."""
    prov = decision["evaluation"]["provenance"]
    assert prov.startswith("EXTERNAL")
    assert "NOT yet been independently reproduced" in prov


def test_secondary_factors_are_not_overstated(decision):
    """lr and forced_decoder_ids are suspects, not the cause. A record that
    blurred that would send the next run chasing the wrong fix."""
    factors = {f["factor"]: f["assessment"]
               for f in decision["root_cause"]["secondary_factors"]}
    assert any("NOT CONFIRMED" in v for v in factors.values())
    assert any("SUSPECTED" in v for v in factors.values())
    assert any("UNQUANTIFIED" in v for v in factors.values())


def test_deferred_rows_stay_excluded_and_unclassified(decision):
    assert "unclassified" in decision["consequences"]["deferred_rows"]
    draft = json.loads(
        (ROOT / "platform/decisions/DQ-2026-001-label-review.json").read_bytes())
    assert draft["status"] == "draft"
    assert all(e["classification"] is None for e in draft["entries"])


def test_record_carries_no_content(decision):
    blob = json.dumps(decision)
    for banned in ("text_normalized", "transcript", "speaker", "audio_filepath"):
        assert banned not in blob or banned in decision["content_policy"], banned


# --------------------------------------------------------------------------- #
# the evaluator
# --------------------------------------------------------------------------- #
def test_both_arms_scored_in_one_process():
    """A candidate compared against a baseline from a different runtime cannot
    attribute the difference to the model."""
    s = EVAL.read_text()
    assert "PeftModel.from_pretrained(model" in s, "wraps the SAME base object"
    assert "model = merged.unload()" in s, "unwraps so arms stay comparable"
    assert 'arms = {"base"' in s
    assert "identical_decoding" in s


def test_generation_settings_are_pinned_in_one_place():
    s = EVAL.read_text()
    assert "GEN = {" in s
    assert "kw = dict(GEN)" in s
    assert s.count("model.generate(") == 1, "one decode call, shared by both arms"


def test_evaluator_never_emits_transcripts():
    s = EVAL.read_text()
    assert '"reference"' not in s and '"hypothesis"' not in s
    assert "no transcript is printed, logged or stored" in s
    # the per-utterance row is checksums and numbers
    assert '"audio_checksum_sha256": rec["audio_checksum_sha256"]' in s
    for numeric_only in ('"output_tokens"', '"latency_s"', '"ref_words"'):
        assert numeric_only in s


def test_evaluator_records_length_and_cap_hits():
    """WER alone says 'bad'. Length and cap-hits say 'runaway', which is the
    actual diagnosis."""
    s = EVAL.read_text()
    assert "rows_hitting_length_cap" in s
    assert "hit_length_cap" in s
    assert '"median": statistics.median(lens)' in s


def test_evaluator_verifies_audio_checksums():
    s = EVAL.read_text()
    assert "audio checksum mismatch" in s
    assert "the eval set is not what the " in s and "manifest describes" in s


def test_evaluator_reports_confidence_intervals():
    s = EVAL.read_text()
    assert "bootstrap_ci" in s and "wer_ci95" in s


def test_evaluator_records_hashes():
    s = EVAL.read_text()
    assert "eval_manifest_sha256" in s
    assert '"adapter_sha256": files.get("adapter_model.safetensors")' in s
    assert "BASE_REVISION" in s


def test_evaluator_supports_a_checkpoint_sweep():
    s = EVAL.read_text()
    assert '"--adapter", action="append"' in s
