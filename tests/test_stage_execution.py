"""Behavioural tests for the real B4 execution boundary."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import orchestrate, stage_descriptor
from pipeline import decode_budget, diagnostic_budget
from pipeline import stage_runner
from pipeline.campaign_tracking import CampaignTracker
from pipeline.ec2_stage_adapter import (
    EC2StageAdapter, EC2StageConfig, StageLaunchError, render_user_data)
from pipeline.stage_runner import (
    _training_command, download_artifact_tree, require_runtime_provenance,
    upload_tree)
from pipeline.termination_diagnostic import (
    aggregate_rows, generated_row, repeated_ngram_rate, teacher_forced_row)
from pipeline.decode_compatibility import (
    STRATEGIES, score_strategy, select_strategy, strategy_fingerprint,
    strategy_kwargs)
from pipeline.validation_runner import ValidationRuntime


def descriptor(stage="sweep", **over):
    is_base = stage == "base_and_preflight"
    values = {
        "campaign_run": "b4-test", "attempt": "1", "stage": stage,
        "git_sha": "a" * 40, "bundle_tar_sha256": "b" * 64,
        "image_digest": "sha256:" + "c" * 64,
        "policy_sha256": "d" * 64,
        "adoption_key":
            "curated/_versions/v2/ADOPTION-B4-CORRECTED.json",
        "dataset_fingerprint": "e" * 64,
        "base_manifest_sha256": "f" * 64,
        "validation_manifest_sha256": "0" * 64,
        "base_arm_key": None if is_base else "1" * 64,
        "base_artifact_key": (
            None if is_base
            else "candidates/evaluations/b4-test/base.json"),
        "base_artifact_sha256": None if is_base else "2" * 64,
        "generation_config_fingerprint": "3" * 64,
        "evaluator_sha256": "4" * 64,
        "lr": None if is_base else 1e-4,
        "seed": 0,
        "max_steps": 0 if is_base else 100,
        "checkpoint_steps": [],
        "reservation_id": "r1",
        "watchdog_s": 60,
        "input_prefix": "curated/_versions/v2/",
        "input_artifact_sha256": None,
        "output_prefix": "candidates/evaluations/b4-test/attempt-1/"
                         + stage + "/",
        "mlflow_parent_run_id": "parent",
        "mlflow_child_run_id": "child",
        "purpose": "training_system_validation",
        "promotable": False,
    }
    values.update(over)
    return stage_descriptor.build(**values)


def test_user_data_runs_one_digest_pinned_direct_ec2_container():
    d = descriptor()
    text, digest = render_user_data(d, EC2StageConfig())
    assert len(digest) == 64
    assert "__" not in text
    assert "@sha256:" in text
    assert "--gpus all" in text
    assert "--read-only" in text
    assert "pipeline.stage_runner" in text
    assert "shutdown -h now" in text
    assert "eks" not in text.lower()
    assert "spot" not in text.lower()
    assert "--if-none-match '*'" in text
    assert "fileb://" not in text


def test_trainer_image_contains_runtime_governance_records():
    dockerfile = (ROOT / "pipeline/Dockerfile.trainer").read_text()
    assert "DQ-2026-003-policy-deferral-corrected.json" in dockerfile
    assert "VAL-2026-001-frozen-validation-sets.json" in dockerfile


def test_base_stage_runs_training_preflight_before_full_evaluation(
        monkeypatch, tmp_path):
    calls = []

    class Runtime:
        def __init__(self, cli, descriptor, work):
            calls.append("runtime")

        def evaluate_base(self, path):
            calls.append("evaluate_base")
            path.write_bytes(b"base")
            return {"artifact_sha256": hashlib.sha256(b"base").hexdigest()}

    monkeypatch.setattr(stage_runner, "ValidationRuntime", Runtime)
    monkeypatch.setattr(
        stage_runner, "run_training",
        lambda *args, **kwargs: calls.append("run_training") or {"steps": 200})
    monkeypatch.setattr(
        stage_runner, "verify_saved_adapter",
        lambda *args, **kwargs: calls.append("verify_adapter")
        or {"passed": True})
    monkeypatch.setattr(
        stage_runner, "put_immutable",
        lambda cli, key, body: hashlib.sha256(body).hexdigest())
    monkeypatch.setattr(
        stage_runner, "upload_tree",
        lambda *args, **kwargs: {"tree_sha256": "a" * 64})

    result = stage_runner.run_base_and_preflight(
        object(), descriptor(stage="base_and_preflight"), tmp_path)
    assert calls.index("run_training") < calls.index("evaluate_base")
    assert result["preflight"]["passed"] is True
    assert result["base"]["artifact_key"].endswith("/evaluations/base.json")


def test_saved_adapter_smoke_prepares_its_own_validation_cache():
    runtime = object.__new__(ValidationRuntime)
    runtime._loaded = {}
    runtime.processor = None
    calls = []

    def prepare():
        calls.append("prepare")
        runtime.processor = object()
        runtime._loaded = {"acholi": ([{"checksum": "x"}], [("audio", 16000)])}

    runtime.prepare = prepare
    runtime.ensure_prepared()
    runtime.ensure_prepared()
    assert calls == ["prepare"]
    assert "acholi" in runtime._loaded


@pytest.mark.parametrize("field,value", [
    ("git_sha", "G" * 40),
    ("image_digest", "sha256:" + "z" * 64),
    ("campaign_run", "../escape"),
    ("attempt", "1/other"),
    ("output_prefix", "candidates/evaluations/other/attempt-1/sweep/"),
])
def test_descriptor_refuses_malformed_identity_or_path_escape(field, value):
    values = descriptor()
    values[field] = value
    with pytest.raises(SystemExit, match="REFUSING"):
        stage_descriptor.build(**values)


def test_diagnostic_descriptor_pins_retained_tree_and_trains_zero_steps():
    d = descriptor(
        stage="diagnostic", max_steps=0, checkpoint_steps=[],
        input_prefix=(
            "candidates/evaluations/old/attempt-5/sweep-lr-1e-04/"
            "asr/checkpoint-100/"),
        input_artifact_sha256="5" * 64)
    assert d["input_artifact_sha256"] == "5" * 64
    for bad in ({"max_steps": 1}, {"input_artifact_sha256": None},
                {"input_prefix": "curated/_versions/v2/"}):
        values = dict(d)
        values.update(bad)
        with pytest.raises(SystemExit, match="REFUSING"):
            stage_descriptor.build(**values)


def test_diagnostic_budget_is_fresh_and_covers_builder_plus_one_gpu():
    assert diagnostic_budget.LEDGER_KEY == (
        "candidates/budget/b4-amharic-termination-diagnostic/ledger.json")
    total = (diagnostic_budget.worst_case_usd("builder")
             + diagnostic_budget.worst_case_usd("diagnostic"))
    assert total <= diagnostic_budget.CEILING_USD
    assert diagnostic_budget.WATCHDOG_S["diagnostic"] == 3300


def test_decode_descriptor_and_budget_are_separate_and_no_training():
    d = descriptor(
        stage="decode_compatibility", max_steps=0, checkpoint_steps=[],
        input_prefix=(
            "candidates/evaluations/old/attempt-5/sweep-lr-1e-04/"
            "asr/checkpoint-100/"),
        input_artifact_sha256="5" * 64)
    assert d["stage"] == "decode_compatibility"
    assert d["max_steps"] == 0
    assert decode_budget.LEDGER_KEY == (
        "candidates/budget/b4-amharic-decode-compatibility/ledger.json")
    total = (decode_budget.worst_case_usd("builder")
             + decode_budget.worst_case_usd("decode_compatibility"))
    assert total <= decode_budget.CEILING_USD
    assert decode_budget.WATCHDOG_S["decode_compatibility"] == 5400


def test_decode_strategies_are_frozen_and_exclude_repetition_constraints():
    assert tuple(STRATEGIES) == (
        "greedy_v1", "whisper_fallback_v1", "beam5_v1")
    fallback = strategy_kwargs("whisper_fallback_v1", "am")
    assert fallback["temperature"] == (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)
    assert fallback["compression_ratio_threshold"] == 1.35
    assert fallback["logprob_threshold"] == -1.0
    beam = strategy_kwargs("beam5_v1", "am")
    assert beam["num_beams"] == 5
    assert beam["early_stopping"] is True
    assert "no_repeat_ngram_size" not in json.dumps(
        {name: dict(value) for name, value in STRATEGIES.items()})
    assert "repetition_penalty" not in json.dumps(
        {name: dict(value) for name, value in STRATEGIES.items()})
    assert len(strategy_fingerprint()) == 64


def test_decode_selection_requires_termination_and_non_regression():
    def measured(strategy, wer, eos=1.0, cap=0.0, latency=2.0,
                 controls=0):
        return {
            "rows": 25, "strategy": strategy,
            "wer": wer, "cer": wer, "eos_rate": eos,
            "cap_hit_rate": cap,
            "latency_s": {"median": latency},
            "unexpected_control_tokens": {"total": controls},
        }

    results = {
        "greedy_v1": {
            "base": measured("greedy_v1", 1.05, eos=0.0, cap=1.0),
            "retained_1e_4": measured(
                "greedy_v1", 1.23, eos=0.12, cap=0.88),
        },
        "whisper_fallback_v1": {
            "base": measured("whisper_fallback_v1", 1.10, latency=3.0),
            "retained_1e_4": measured(
                "whisper_fallback_v1", 1.12, latency=3.2),
        },
        "beam5_v1": {
            "base": measured("beam5_v1", 1.00, latency=4.0),
            "retained_1e_4": measured("beam5_v1", 1.08, latency=4.2),
        },
    }
    out = select_strategy(results)
    assert out["selected_strategy"] == "whisper_fallback_v1"
    assert out["viability"]["greedy_v1"]["passed"] is False
    assert out["viability"]["beam5_v1"]["checks"][
        "candidate_vs_base_wer"] is False
    assert out["training_authorised"] is False
    assert out["promotable"] is False


def test_decode_score_reduces_private_row_to_aggregate_only():
    torch = pytest.importorskip("torch")
    from types import SimpleNamespace

    class Tokenizer:
        all_special_ids = [2, 10, 11, 12, 13]
        def convert_tokens_to_ids(self, token): return 2
        def decode(self, ids, skip_special_tokens=True): return "hello"

    class Processor:
        tokenizer = Tokenizer()
        def feature_extractor(self, audio, sampling_rate, return_tensors=None):
            return SimpleNamespace(input_features=torch.zeros(1, 4, 8))

    class Model:
        dtype = torch.float32
        def generate(self, features, **kwargs):
            return SimpleNamespace(
                sequences=torch.tensor([[10, 11, 12, 13, 5, 2]]))

    result = score_strategy(
        Model(), Processor(), [{
            "text_normalized": "hello",
            "audio_checksum_sha256": "a" * 64,
        }], [([0.0], 16000)], "amharic", "cpu", "am",
        [10, 11, 12, 13], "greedy_v1")
    assert result["wer"] == 0.0
    assert result["eos_rate"] == 1.0
    assert result["cap_hit_rate"] == 0.0
    serialised = json.dumps(result)
    for forbidden in ("audio_checksum", "text_normalized", "per_utterance",
                      "speaker", "session", "token_ids"):
        assert forbidden not in serialised


def test_stage_runner_dispatches_decode_compatibility_without_training(
        monkeypatch, tmp_path):
    d = descriptor(
        stage="decode_compatibility", max_steps=0, checkpoint_steps=[],
        input_prefix=(
            "candidates/evaluations/old/attempt-5/sweep-lr-1e-04/"
            "asr/checkpoint-100/"),
        input_artifact_sha256="5" * 64)
    calls = []
    monkeypatch.setattr(stage_runner, "require_runtime_provenance",
                        lambda value: calls.append("provenance"))
    monkeypatch.setattr(stage_runner, "require_environment",
                        lambda: calls.append("environment"))
    monkeypatch.setattr(stage_runner, "_s3", lambda: object())
    monkeypatch.setenv("MEDZEN_STAGE_WORK", str(tmp_path / "stage"))
    monkeypatch.setattr(
        stage_runner, "run_decode_compatibility",
        lambda cli, value, work: calls.append("decode") or {
            "decode_artifact_key": "candidates/test/decode.json",
            "decode_artifact_sha256": "a" * 64,
            "training_steps": 0,
            "strategies": {},
            "selection": {"selected_strategy": None},
        })
    out = tmp_path / "result.json"
    result = stage_runner.execute(d, out)
    assert calls == ["provenance", "environment", "decode"]
    assert result["stage"] == "decode_compatibility"
    assert result["training_steps"] == 0
    assert json.loads(out.read_bytes())["training_steps"] == 0


def test_decode_launcher_dry_run_constructs_no_tracker_or_reservation(
        monkeypatch):
    from scripts import run_decode_compatibility as launch

    monkeypatch.setattr(
        launch, "validate_inputs",
        lambda args: (object(), object(), {"writes_performed": 0}))
    monkeypatch.setattr(
        launch, "CampaignTracker",
        lambda *args, **kwargs: pytest.fail("dry run constructed MLflow"))
    monkeypatch.setattr(sys, "argv", [
        "run_decode_compatibility.py",
        "--campaign-run", "b4-test",
        "--git-sha", "a" * 40,
        "--bundle-tar-sha256", "b" * 64,
        "--image-digest", "sha256:" + "c" * 64,
    ])
    assert launch.main() == 0


def test_no_training_diagnostic_verifies_immutable_governance_bindings():
    from scripts import run_termination_diagnostic as launch

    policy_raw = launch.POLICY.read_bytes()
    policy = json.loads(policy_raw)
    complete = b'{"record":"complete"}\n'
    adoption = {
        "status": "approved",
        "deferral_policy_sha256": hashlib.sha256(policy_raw).hexdigest(),
        "complete_raw_sha256": hashlib.sha256(complete).hexdigest(),
        "deferred_checksums_sha256":
            policy["bindings"]["deferred_checksums_sha256"],
        "dataset_fingerprint": launch.DATASET_FINGERPRINT,
        "eligible_rows": 4601,
    }

    class Body:
        def __init__(self, value): self.value = value
        def read(self): return self.value

    class S3:
        def __init__(self): self.complete = complete
        def get_object(self, Bucket, Key):
            value = (json.dumps(adoption).encode()
                     if Key == launch.ADOPTION_KEY else self.complete)
            return {"Body": Body(value)}

    fake = S3()
    result = launch.verify_diagnostic_governance(fake)
    assert result["dataset_fingerprint"] == launch.DATASET_FINGERPRINT
    fake.complete = b"changed"
    with pytest.raises(SystemExit, match="governance binding failed"):
        launch.verify_diagnostic_governance(fake)


def test_retained_adapter_download_verifies_every_bound_byte(tmp_path):
    files = {
        "adapter_config.json": {"sha256": hashlib.sha256(b"{}").hexdigest(),
                                "bytes": 2},
        "adapter_model.safetensors": {
            "sha256": hashlib.sha256(b"weights").hexdigest(), "bytes": 7},
    }
    tree = hashlib.sha256(json.dumps(
        files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    prefix = (
        "candidates/evaluations/old/attempt-5/sweep-lr-1e-04/"
        "asr/checkpoint-100/")
    manifest = json.dumps({
        "prefix": "s3://medzen-speech/" + prefix,
        "files": files, "tree_sha256": tree,
    }).encode()

    class Body:
        def __init__(self, value): self.value = value
        def read(self): return self.value

    class S3:
        values = {
            prefix + "ARTIFACT.json": manifest,
            prefix + "adapter_config.json": b"{}",
            prefix + "adapter_model.safetensors": b"weights",
        }
        def get_object(self, Bucket, Key):
            return {"Body": Body(self.values[Key])}

    d = descriptor(
        stage="diagnostic", max_steps=0,
        input_prefix=prefix, input_artifact_sha256=tree)
    got = download_artifact_tree(S3(), d, tmp_path / "adapter")
    assert got["tree_sha256"] == tree
    assert got["adapter_sha256"] == files["adapter_model.safetensors"]["sha256"]


def test_aggregate_diagnostic_metrics_are_numeric_and_have_no_row_payload():
    rows = [{
        "target_tokens": 5, "content_tokens": 1,
        "total_nll_sum": 2.5, "content_nll_sum": 0.4,
        "eos_nll": 0.2, "eos_probability": 0.8, "eos_rank": 1,
        "generated_tokens": 3, "eos_emitted": True,
        "hit_length_cap": False, "unique_token_ratio": 1.0,
        "repeated_bigram_rate": 0.0, "repeated_trigram_rate": 0.0,
        "unexpected_control_tokens": 0,
    }, {
        "target_tokens": 6, "content_tokens": 2,
        "total_nll_sum": 6.0, "content_nll_sum": 2.0,
        "eos_nll": 2.0, "eos_probability": 0.1, "eos_rank": 8,
        "generated_tokens": 440, "eos_emitted": False,
        "hit_length_cap": True, "unique_token_ratio": 0.2,
        "repeated_bigram_rate": 0.7, "repeated_trigram_rate": 0.6,
        "unexpected_control_tokens": 2,
    }]
    out = aggregate_rows(rows)
    assert out["generation"]["eos_rate"] == 0.5
    assert out["generation"]["cap_hit_rate"] == 0.5
    assert out["teacher_forced"]["eos_rank"]["max"] == 8
    assert "per_utterance" not in json.dumps(out)
    assert repeated_ngram_rate([1, 2, 1, 2], 2) == pytest.approx(1 / 3)


def test_teacher_forced_and_generation_contract_on_tiny_runtime():
    torch = pytest.importorskip("torch")
    from types import SimpleNamespace

    class Padded(dict):
        def __getattr__(self, key): return self[key]

    class Tokenizer:
        prefix_tokens = [10, 11, 12, 13]
        all_special_ids = [2, 10, 11, 12, 13]
        def set_prefix_tokens(self, **kw): pass
        def convert_tokens_to_ids(self, token):
            return 10 if token == "<|startoftranscript|>" else 2
        def __call__(self, text):
            return SimpleNamespace(input_ids=[10, 11, 12, 13, 5, 2])
        def pad(self, values, return_tensors=None):
            ids = torch.tensor([v["input_ids"] for v in values])
            return Padded(input_ids=ids, attention_mask=torch.ones_like(ids))

    class Processor:
        tokenizer = Tokenizer()
        def feature_extractor(self, audio, sampling_rate, return_tensors=None):
            return SimpleNamespace(input_features=torch.zeros(1, 4, 8))

    class Model:
        dtype = torch.float32
        config = SimpleNamespace(decoder_start_token_id=10)
        def __call__(self, **batch):
            target = batch["labels"]
            logits = torch.zeros(1, target.shape[1], 20)
            for pos, token in enumerate(target[0].tolist()):
                logits[0, pos, token] = 8
            return SimpleNamespace(logits=logits)
        def generate(self, features, **kw):
            return SimpleNamespace(sequences=torch.tensor([[10, 11, 12, 13, 5, 2]]))

    teacher = teacher_forced_row(
        Model(), Processor(), {"text_normalized": "private"},
        [0.0], 16000, "acholi", "cpu")
    generated = generated_row(
        Model(), Processor(), [0.0], 16000, "acholi", "cpu")
    assert teacher["eos_rank"] == 1
    assert teacher["content_tokens"] == 1
    assert generated["eos_emitted"] is True
    assert generated["unexpected_control_tokens"] == 0


def test_runtime_provenance_must_match_every_descriptor_pin(monkeypatch):
    d = descriptor()
    monkeypatch.setenv("MEDZEN_CODE_GIT_SHA", d["git_sha"])
    monkeypatch.setenv("MEDZEN_GIT_SHA", d["git_sha"])
    monkeypatch.setenv("MEDZEN_CODE_TAR_SHA256", d["bundle_tar_sha256"])
    monkeypatch.setenv("MEDZEN_IMAGE_DIGEST", d["image_digest"])
    require_runtime_provenance(d)
    monkeypatch.setenv("MEDZEN_IMAGE_DIGEST", "sha256:" + "9" * 64)
    with pytest.raises(SystemExit, match="provenance differs"):
        require_runtime_provenance(d)


def test_final_segment_keeps_600_step_schedule_while_pausing_at_300():
    d = descriptor(
        stage="final", max_steps=600,
        checkpoint_steps=[100, 200, 300, 400, 500, 600])
    cmd = _training_command(
        d, Path("/cache/final"), lr=3e-4, max_steps=600,
        stop_at_step=300, resume=Path("/cache/final/checkpoint-200"))
    assert cmd[cmd.index("--max-steps") + 1] == "600"
    assert cmd[cmd.index("--stop-at-step") + 1] == "300"
    assert cmd[cmd.index("--resume") + 1].endswith("checkpoint-200")


def test_saved_adapter_smoke_is_a_hard_gate():
    clean = orchestrate.evaluate_gates(
        {l: 0.5 for l in orchestrate.VALIDATION_LANGUAGES},
        {l: 0.6 for l in orchestrate.VALIDATION_LANGUAGES},
        {l: 1.0 for l in orchestrate.VALIDATION_LANGUAGES},
        {l: 0.0 for l in orchestrate.VALIDATION_LANGUAGES},
    )
    out = orchestrate.apply_checkpoint_controls(
        clean, {"passed": False, "reasons": ["adapter inert"]})
    assert not out["passed"]
    assert out["gates"]["saved_adapter_smoke"] is False
    assert "adapter inert" in " ".join(out["failures"])


def test_real_tiny_whisper_lora_collate_save_reload_and_generate(tmp_path):
    """The exact boundary the previous source-only task-type test missed."""
    torch = pytest.importorskip("torch")
    from peft import LoraConfig, PeftModel, get_peft_model
    from transformers import (BatchEncoding, WhisperConfig,
                              WhisperForConditionalGeneration)

    from pipeline.generation import extract_sequence
    from pipeline.smoke import (adapter_effect_verdict,
                                lora_structure_verdict)
    from pipeline.train_asr import collate

    class TinyTokenizer:
        pad_token_id = 0

        def pad(self, features, return_tensors=None):
            sequences = [f["input_ids"] for f in features]
            width = max(map(len, sequences))
            ids = torch.tensor(
                [s + [0] * (width - len(s)) for s in sequences])
            mask = torch.tensor(
                [[1] * len(s) + [0] * (width - len(s))
                 for s in sequences])
            return BatchEncoding(
                {"input_ids": ids, "attention_mask": mask})

    class TinyProcessor:
        tokenizer = TinyTokenizer()

    cfg = WhisperConfig(
        vocab_size=32, num_mel_bins=8, d_model=16,
        encoder_layers=1, decoder_layers=1,
        encoder_attention_heads=2, decoder_attention_heads=2,
        encoder_ffn_dim=32, decoder_ffn_dim=32,
        max_source_positions=16, max_target_positions=16,
        pad_token_id=0, bos_token_id=2, eos_token_id=2,
        decoder_start_token_id=1,
    )
    torch.manual_seed(0)
    base = WhisperForConditionalGeneration(cfg)
    pristine = {k: v.detach().clone() for k, v in base.state_dict().items()}
    model = get_peft_model(
        base,
        LoraConfig(
            r=2, lora_alpha=4, target_modules=["q_proj", "v_proj"],
            task_type=None))
    batch = collate(TinyProcessor(), decoder_start_token_id=1)([{
        "input_features": torch.randn(8, 32),
        "labels": [1, 3, 4, 2],
    }])
    optimiser = torch.optim.AdamW(
        [p for p in model.parameters() if p.requires_grad], lr=0.1)
    first = model(**batch)
    assert torch.isfinite(first.loss)
    first.loss.backward()
    optimiser.step()

    with pytest.warns(
            UserWarning, match="vocabulary was not modified"):
        model.save_pretrained(tmp_path)
    saved = hashlib.sha256(
        (tmp_path / "adapter_model.safetensors").read_bytes()).hexdigest()
    fresh = WhisperForConditionalGeneration(cfg)
    fresh.load_state_dict(pristine)
    reloaded = PeftModel.from_pretrained(
        fresh, tmp_path, is_trainable=True)
    assert lora_structure_verdict(reloaded)["passed"]
    reloaded_hash = hashlib.sha256(
        (tmp_path / "adapter_model.safetensors").read_bytes()).hexdigest()
    with torch.no_grad():
        logits_on = reloaded(**batch).logits
        with reloaded.disable_adapter():
            logits_off = reloaded(**batch).logits
    norms = {
        n: float(p.detach().norm())
        for n, p in reloaded.named_parameters() if "lora_B" in n
    }
    effect = adapter_effect_verdict(
        logits_on, logits_off, norms,
        checkpoint_sha256=saved,
        tested_artifact_sha256=reloaded_hash)
    assert effect["passed"]

    generated = reloaded.generate(
        batch["input_features"], max_new_tokens=2,
        return_dict_in_generate=True, force_unique_generate_call=True)
    assert extract_sequence(generated)[0] == cfg.decoder_start_token_id
    fallback = reloaded.generate(
        batch["input_features"], max_new_tokens=2,
        return_dict_in_generate=True, force_unique_generate_call=True,
        temperature=(0.0, 0.2), compression_ratio_threshold=1.35,
        logprob_threshold=-1.0)
    assert extract_sequence(fallback)[0] == cfg.decoder_start_token_id
    beam = reloaded.generate(
        batch["input_features"], max_new_tokens=2,
        return_dict_in_generate=True, force_unique_generate_call=True,
        num_beams=5, do_sample=False, early_stopping=True)
    assert extract_sequence(beam)[0] == cfg.decoder_start_token_id


def test_real_mlflow_parent_child_structure(tmp_path):
    tracker = CampaignTracker(tmp_path / "mlflow.db", "camp", "7")
    child = tracker.start_stage("sweep-lr-1e-4", {
        "code_git_sha": "a" * 40,
        "image_digest": "sha256:" + "b" * 64,
        "dataset_fingerprint": "c" * 64,
        "promotable": False,
    })
    tracker.finish_stage("sweep-lr-1e-4", {
        "instance_id": "i-123",
        "root_volume_deleted": True,
        "stage_descriptor_sha256": "d" * 64,
        "actual_seconds": 12,
        "steps_completed": 100,
        "wer": {"acholi": 0.5},
        "strategies": {
            "beam5_v1": {
                "base": {
                    "wer": 1.0, "cer": 0.8, "eos_rate": 1.0,
                    "cap_hit_rate": 0.0,
                    "generated_tokens": {"median": 80},
                    "latency_s": {"median": 2.5},
                    "unique_token_ratio": {"mean": 0.7},
                    "repeated_bigram_rate": {"mean": 0.1},
                }
            }
        },
    })
    tracker.finish_parent(True, "ok")
    run = tracker.client.get_run(child)
    assert run.data.tags["mlflow.parentRunId"] == tracker.parent_run_id
    assert run.data.tags["purpose"] == "training_system_validation"
    assert run.data.tags["promotable"] == "false"
    assert run.data.params["code_git_sha"] == "a" * 40
    assert run.data.metrics["val_wer_acholi"] == 0.5
    assert run.data.metrics["decode_beam5_v1_base_wer"] == 1.0
    assert run.data.metrics["decode_beam5_v1_base_eos_rate"] == 1.0
    assert tracker.client.search_registered_models() == []


class Body:
    def __init__(self, value):
        self.value = value

    def read(self, amount=None):
        if amount is None:
            value, self.value = self.value, b""
            return value
        value, self.value = self.value[:amount], self.value[amount:]
        return value


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.puts = []

    def list_objects_v2(self, Bucket, Prefix, MaxKeys=None):
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        if MaxKeys is not None:
            keys = keys[:MaxKeys]
        return {"KeyCount": len(keys),
                "Contents": [{"Key": k} for k in keys]}

    def put_object(self, Bucket, Key, Body, **kwargs):
        if kwargs.get("IfNoneMatch") == "*" and Key in self.objects:
            raise AssertionError("overwrite")
        self.objects[Key] = Body.read() if hasattr(Body, "read") else Body
        self.puts.append((Key, kwargs))
        return {}

    def get_object(self, Bucket, Key):
        return {"Body": Body(self.objects[Key])}


def test_artifact_tree_uses_conditional_writes_and_sha256_readback(tmp_path):
    (tmp_path / "adapter.safetensors").write_bytes(b"adapter-bytes")
    cli = FakeS3()
    manifest = upload_tree(cli, tmp_path, "candidates/test/artifact")
    assert manifest["files"]["adapter.safetensors"]["sha256"] == \
        hashlib.sha256(b"adapter-bytes").hexdigest()
    upload = next(
        kwargs for key, kwargs in cli.puts
        if key.endswith("/adapter.safetensors"))
    assert upload["IfNoneMatch"] == "*"
    assert upload["ChecksumSHA256"]
    assert upload["ServerSideEncryption"] == "aws:kms"


class FakeECR:
    def batch_get_image(self, repositoryName, imageIds, **kwargs):
        return {"images": [{"imageId": {
            "imageDigest": imageIds[0]["imageDigest"]}}]}

    def describe_image_scan_findings(self, **kwargs):
        return {"imageScanStatus": {"status": "COMPLETE"}}

    def describe_images(self, repositoryName, imageIds):
        return {"imageDetails": [{
            "imageDigest": imageIds[0]["imageDigest"],
            "imageTags": ["a" * 40],
        }]}


class FakeEC2:
    def __init__(self, s3, tamper=False, active=False):
        self.s3 = s3
        self.tamper = tamper
        self.active = active
        self.launched = []

    def describe_instances(self, Filters=None, InstanceIds=None):
        if Filters is not None:
            active = [{
                "InstanceId": "i-orphan"
            }] if self.active else []
            return {"Reservations": [{"Instances": active}] if active else []}
        return {"Reservations": [{"Instances": [{
            "InstanceId": InstanceIds[0],
            "State": {"Name": "terminated"},
            "BlockDeviceMappings": [{
                "Ebs": {"VolumeId": "vol-1"}}],
        }]}]}

    def describe_images(self, ImageIds):
        return {"Images": [{
            "ImageId": ImageIds[0], "State": "available",
            "Architecture": "x86_64", "OwnerId": "898082745236",
        }]}

    def describe_subnets(self, SubnetIds):
        return {"Subnets": [{
            "SubnetId": SubnetIds[0], "VpcId": "vpc-1",
            "AvailabilityZone": "eu-central-1a",
        }]}

    def describe_security_groups(self, GroupIds):
        return {"SecurityGroups": [{
            "GroupId": GroupIds[0], "VpcId": "vpc-1",
        }]}

    def run_instances(self, **kwargs):
        self.launched.append(kwargs)
        descriptor_key = next(
            k for k in self.s3.objects if k.endswith("/descriptor.json"))
        d = json.loads(self.s3.objects[descriptor_key])
        prefix = descriptor_key.rsplit("/", 1)[0] + "/"
        result = {
            "stage_descriptor_sha256":
                ("9" * 64 if self.tamper
                 else stage_descriptor.descriptor_hash(d)),
            "campaign_run": d["campaign_run"],
            "attempt": d["attempt"],
            "stage": d["stage"],
            "wer": {l: 0.5 for l in orchestrate.VALIDATION_LANGUAGES},
            "eos_rate": {l: 1.0 for l in orchestrate.VALIDATION_LANGUAGES},
            "cap_hit_rate": {l: 0.0
                             for l in orchestrate.VALIDATION_LANGUAGES},
            "artifact_sha256": "5" * 64,
            "smoke": {"passed": True, "reasons": []},
        }
        self.s3.objects[prefix + "container-result.json"] = (
            json.dumps(result).encode())
        self.s3.objects[prefix + "container-exit-code"] = b"0\n"
        return {"Instances": [{
            "InstanceId": "i-stage",
            "LaunchTime": datetime.now(timezone.utc),
        }]}

    def describe_volumes(self, VolumeIds):
        from botocore.exceptions import ClientError
        raise ClientError(
            {"Error": {"Code": "InvalidVolume.NotFound"}},
            "DescribeVolumes")

    def terminate_instances(self, InstanceIds):
        raise AssertionError("watchdog fallback was not expected")


class FakeSession:
    def __init__(self, tamper=False, active=False):
        self.s3 = FakeS3()
        self.ecr = FakeECR()
        self.ec2 = FakeEC2(self.s3, tamper=tamper, active=active)
        self.sts = type("STS", (), {
            "get_caller_identity": lambda self: {
                "Account": "558069890522"}})()
        self.iam = type("IAM", (), {
            "get_instance_profile": lambda self, **kw: {
                "InstanceProfile": {"Roles": [{"RoleName": "trainer"}]}}})()

    def client(self, name, region_name=None):
        return {
            "s3": self.s3, "ecr": self.ecr, "ec2": self.ec2,
            "sts": self.sts, "iam": self.iam,
        }[name]


def test_ec2_adapter_observes_termination_and_volume_deletion():
    session = FakeSession()
    cfg = EC2StageConfig(poll_seconds=0, termination_grace_seconds=1)
    result = EC2StageAdapter(session, cfg).run(descriptor())
    assert result["instance_id"] == "i-stage"
    assert result["aws_final_state"] == "terminated"
    assert result["root_volume_id"] == "vol-1"
    assert result["root_volume_deleted"] is True
    assert result["lifecycle"] == "on-demand-direct-ec2"
    assert result["eks_involved"] is False and result["spot_involved"] is False
    stage_descriptor.verify_result(descriptor(), result)
    assert any(k.endswith("/stage-result.json")
               for k in session.s3.objects)
    launch = session.ec2.launched[0]
    assert launch["MinCount"] == launch["MaxCount"] == 1
    assert len(launch["ClientToken"]) == 64
    assert launch["InstanceInitiatedShutdownBehavior"] == "terminate"
    assert launch["MetadataOptions"]["HttpTokens"] == "required"
    assert launch["BlockDeviceMappings"][0]["Ebs"]["DeleteOnTermination"] is True
    assert launch["BlockDeviceMappings"][0]["DeviceName"] == "/dev/xvda"


def test_direct_ec2_preflight_verifies_infrastructure_without_mutation():
    session = FakeSession()
    d = descriptor()
    result = EC2StageAdapter(session).preflight_campaign(
        d["git_sha"], d["image_digest"])
    assert result["active_b4_instances"] == 0
    assert result["availability_zone"] == "eu-central-1a"
    assert result["eks_involved"] is False
    assert session.s3.objects == {}
    assert session.ec2.launched == []


def test_ec2_adapter_returns_terminated_lifecycle_for_semantic_reconciliation():
    session = FakeSession(tamper=True)
    d = descriptor()
    result = EC2StageAdapter(
        session,
        EC2StageConfig(poll_seconds=0, termination_grace_seconds=1),
    ).run(d)
    assert result["aws_final_state"] == "terminated"
    assert result["identity_problems"]
    with pytest.raises(SystemExit, match="instance ran something else"):
        stage_descriptor.verify_result(d, result)


def test_orphan_gpu_refuses_before_any_s3_mutation():
    session = FakeSession(active=True)
    with pytest.raises(StageLaunchError, match="active MedZen B4 instance"):
        EC2StageAdapter(session).run(descriptor())
    assert session.s3.objects == {}
    assert session.ec2.launched == []
