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
# THE DEFECT — historical record. The fix has landed.
#
# These document the shape of the defect so it stays findable. The proof that
# the CORRECTED collator works is behavioural and lives in
# tests/test_collator_alignment.py, which runs the real collator and
# HuggingFace's own shift_tokens_right. Source-string assertions could never
# have shown a fix works, only that a bug existed.
# --------------------------------------------------------------------------- #
WHISPER_SOT = 50258          # <|startoftranscript|>
WHISPER_EOT = 50257          # <|endoftext|>, and ALSO whisper's bos_token_id


def test_whisper_bos_token_is_not_startoftranscript():
    """The trap. Whisper's bos_token is <|endoftext|>, so a guard written as
    `labels[0] == bos_token_id` compares 50258 against 50257 and is dead."""
    assert WHISPER_SOT != WHISPER_EOT


def _reads_bos_token_id(path: Path) -> bool:
    """AST, not text search: the docstrings deliberately DISCUSS bos_token_id,
    and a grep would confuse explaining the trap with falling into it."""
    import ast
    return any(isinstance(n, ast.Attribute) and n.attr == "bos_token_id"
               for n in ast.walk(ast.parse(path.read_text())))


def test_the_dead_comparison_is_gone():
    """What the defect was: `labels[:, 0] == tokenizer.bos_token_id` compared
    50258 against 50257 and was therefore always false."""
    src = TRAIN.read_text()
    assert "labels[:, 0] == processor.tokenizer.bos_token_id" not in src
    assert not _reads_bos_token_id(TRAIN), "no code may identify SOT via bos"
    assert "first == decoder_start_token_id" in src


def test_the_same_mistake_is_gone_from_the_length_checker():
    """pipeline/label_length.py carried an identical bos_token_id comparison,
    so `effective` silently equalled `raw` for every row ever measured."""
    ll = ROOT / "pipeline/label_length.py"
    assert not _reads_bos_token_id(ll)
    assert "def decoder_start_id(" in ll.read_text()


def test_the_defect_duplicated_sot_and_shifted_every_target():
    """What went wrong: HF derives decoder_input_ids = shift_tokens_right(
    labels, decoder_start_token_id=50258). An unstripped SOT therefore appeared
    twice and every target moved one position from where it sits at inference.

    Note what was NOT wrong: the language, task and no-timestamps tokens are
    legitimate Whisper training targets. An earlier draft called all four prefix
    tokens wrongly-trained content; only the retained SOT was erroneous."""
    labels = [WHISPER_SOT, 50259, 50360, 50364, 675, 1913, WHISPER_EOT]
    decoder_input = [WHISPER_SOT] + labels[:-1]
    assert decoder_input[:2] == [WHISPER_SOT, WHISPER_SOT], "SOT duplicated"
    assert len(decoder_input) == len(labels)
    # the model is trained to predict SOT from SOT at position 0
    assert labels[0] == WHISPER_SOT and decoder_input[0] == WHISPER_SOT


def test_cap_hit_is_defined_by_eos_absence_not_by_arithmetic():
    """An earlier draft read the reported 444-token maximum as 444 + 4 prefix =
    448 = max_target_positions. That was unfounded: generate() returns prompt
    tokens INSIDE the sequence and max_new_tokens excludes them, so 444 is just
    as consistent with 4 prompt + 440 new. The external evaluator's token
    accounting is unknown, so the coincidence proves nothing.

    A cap hit is therefore defined operationally: EOS absent AND generated
    tokens reaching max_new_tokens."""
    s = EVAL.read_text()
    assert "eos_emitted" in s
    assert "generated_tokens" in s and "prompt_tokens" in s
    assert "444" not in s, "no arithmetic coincidence may be encoded as fact"


def test_alignment_is_now_a_hard_gate_in_the_training_path():
    """The gap that let this through: length was checked, alignment was not."""
    src = TRAIN.read_text()
    assert "max_target_positions" in src, "length guard still exists"
    assert "decoder_start_id(processor.tokenizer, model.config)" in src, \
        "tokenizer and model must be cross-checked before training"
    assert "REFUSING:" in src and "do not begin with" in src


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
    assert 'arms = {"base"' in s
    assert "identical_decoding" in s
    assert s.count("score_arm(") >= 3, "one scoring function, used by every arm"


def test_every_arm_starts_from_identical_unmodified_base_weights():
    """Reusing one object and calling unload() assumes the unwrap is perfect.
    Reloading removes the assumption -- which matters most in a checkpoint
    sweep, where weights are the only difference that should exist."""
    s = EVAL.read_text()
    assert "def fresh_base():" in s
    assert "base = fresh_base()" in s
    # the docstring explains why unload() is NOT used; no call may remain
    import ast
    assert not any(isinstance(n, ast.Attribute) and n.attr == "unload"
                   for n in ast.walk(ast.parse(s)))


def test_generation_settings_are_pinned_in_one_place():
    s = EVAL.read_text()
    assert "GEN = {" in s
    assert "kw = dict(GEN, language=lang_token, task=TASK)" in s
    assert s.count("model.generate(") == 1, "one decode call, shared by both arms"


def test_language_and_task_are_forced_for_both_arms():
    """Auto-detection could decode the arms as different languages and the
    difference would be blamed on the adapter."""
    s = EVAL.read_text()
    assert '"--lang-token", required=True' in s
    assert 'TASK = "transcribe"' in s
    assert "task=TASK" in s


def test_evaluator_never_emits_transcripts():
    s = EVAL.read_text()
    assert '"reference"' not in s and '"hypothesis"' not in s
    assert "no transcript is printed, logged or stored" in s
    # the per-utterance row is checksums and numbers
    assert '"audio_checksum_sha256": rec["audio_checksum_sha256"]' in s
    for numeric_only in ('"generated_tokens"', '"latency_s"', '"ref_words"'):
        assert numeric_only in s


def test_evaluator_records_length_and_cap_hits():
    """WER alone says 'bad'. Length, EOS rate and cap-hits say 'runaway',
    which is the actual diagnosis."""
    s = EVAL.read_text()
    assert "rows_hitting_length_cap" in s and "cap_hit_rate" in s
    assert "eos_rate" in s and "eos_emitted_rows" in s
    assert '"stop_reasons"' in s
    assert '"median": statistics.median(gen)' in s


def test_prompt_and_generated_tokens_are_counted_separately():
    """generate() returns a sequence INCLUDING the decoder prompt, while
    max_new_tokens excludes it. Conflating them overstates generated length by
    the prompt size and mislabels rows as cap hits."""
    s = EVAL.read_text()
    assert "def _prompt_len(" in s
    assert "n_gen = n_total - n_prompt" in s
    assert 'cap_hit = (not eos_emitted) and n_gen >= GEN["max_new_tokens"]' in s
    assert '"prompt_tokens": n_prompt' in s
    assert '"eos_position": eos_pos' in s
    assert '"stop_reason"' in s


def test_prompt_length_is_measured_not_assumed():
    s = EVAL.read_text()
    assert "all_special_ids" in s
    assert "would break silently" in s


def test_base_cache_is_verified_every_run():
    """A cache trusted because the directory exists is an assumption."""
    s = EVAL.read_text()
    assert "base_files = fetch_prefix(cli, BASE_PREFIX, base_dir)" in s
    assert "if not base_dir.exists()" not in s
    assert "is not the pinned artifact" in s
    assert "base_manifest_sha256" in s


def test_audio_is_verified_every_run_not_only_on_download():
    s = EVAL.read_text()
    assert "EVERY run, not only on download" in s
    i_read = s.index("got = sha256_bytes(local.read_bytes())")
    i_check = s.index("audio checksum mismatch")
    assert i_read < i_check


def test_adapter_caches_are_keyed_by_full_uri():
    """Every run's final/ and every checkpoint-100 share a basename; a shared
    cache directory would score one adapter under another's name."""
    s = EVAL.read_text()
    assert "key = sha256_bytes(uri.encode())[:16]" in s
    assert 'd = work / "adapters" / key' in s


def test_full_provenance_is_recorded():
    s = EVAL.read_text()
    for field in ("base_manifest_sha256", "eval_manifest_sha256",
                  "adapter_sha256", "image_digest", "code_git_commit",
                  '"generation"'):
        assert field in s, field


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
