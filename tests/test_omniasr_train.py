"""C1 tests: config refusals, pre-sampling gates, resume bookkeeping, loop.

The torch-free majority runs on the engineering host; the loop tests carry
the same torch skip-marker policy as test_omniasr_lora and execute inside
the trainer image (work item C3).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from pipeline.omniasr_data import DataRefusal, batch_rows, fetch_audio
from pipeline.omniasr_train import (
    TrainerConfig,
    TrainerRefusal,
    build_gated_mix,
    parse_config,
    read_resume_state,
    run_fingerprint,
    write_checkpoint_marker,
)
from pipeline.licence_filter import LicencePolicyRefusal

_needs_torch = pytest.mark.skipif(
    importlib.util.find_spec("torch") is None,
    reason="torch is a training/runtime-host dependency, absent on the engineering host",
)


BASE_ENV = {
    "MEDZEN_VARIANT": "ctc",
    "MEDZEN_MANIFEST_VERSION": "v9",
    "MEDZEN_LANGUAGES": "yemba",
    "MEDZEN_SEED": "7",
}


def make_config(**overrides) -> TrainerConfig:
    env = dict(BASE_ENV)
    env.update(overrides)
    return parse_config(env)


# --------------------------------------------------------------------------
# configuration refusals
# --------------------------------------------------------------------------

def test_llm_variant_is_refused_by_name():
    with pytest.raises(TrainerRefusal, match="calibration"):
        make_config(MEDZEN_VARIANT="llm")


@pytest.mark.parametrize("absent", ["MEDZEN_VARIANT", "MEDZEN_MANIFEST_VERSION",
                                    "MEDZEN_LANGUAGES", "MEDZEN_SEED"])
def test_required_settings_have_no_default(absent):
    env = {k: v for k, v in BASE_ENV.items() if k != absent}
    with pytest.raises(TrainerRefusal, match=absent):
        parse_config(env)


def test_unknown_licence_policy_in_config_is_refused():
    with pytest.raises(TrainerRefusal, match="unknown"):
        make_config(MEDZEN_ALLOWED_LICENCE_POLICIES="cc0,made_up_policy")


def test_never_train_policy_cannot_be_configured_in():
    config = make_config(MEDZEN_ALLOWED_LICENCE_POLICIES="cc0,research_only")
    with pytest.raises(LicencePolicyRefusal, match="never"):
        config_gate_rows(config, [_row("yemba", "cc0")])


def test_nonsense_numbers_are_refused():
    with pytest.raises(TrainerRefusal):
        make_config(MEDZEN_MAX_STEPS="0")
    with pytest.raises(TrainerRefusal):
        make_config(MEDZEN_SEED="not-a-number")
    with pytest.raises(TrainerRefusal):
        make_config(MEDZEN_AUDIO_CAP_HOURS="0")


def test_train_mode_parses_and_defaults_to_lora():
    config = make_config()
    assert config.train_mode == "lora"
    # full mode requires an explicit LR since the Codex-finding corrections
    # (2026-08-20) — the bare form is covered by its own refusal test
    config = make_config(MEDZEN_TRAIN_MODE="full", MEDZEN_LR="1e-5",
                          MEDZEN_WARMUP_STEPS="100",
                          MEDZEN_LR_SCHEDULE="constant")
    assert config.train_mode == "full"
    # the mode is part of the run fingerprint — a full-FT run can never
    # collide with a LoRA run's identity
    assert config.fingerprint_payload()["train_mode"] == "full"


def test_ctc_defaults_bind_the_eval_proven_identity():
    config = make_config()
    assert config.model_card == "medzen_omniASR_CTC_1B_v2"
    assert config.audio_cap_hours == 100.0
    assert sorted(config.allowed_policies) == [
        "cc0", "cc_by_3_0", "cc_by_4_0", "commercial_ok", "sharealike_review"]


# --------------------------------------------------------------------------
# the gates act BEFORE temperature sampling (via a fake S3 corpus)
# --------------------------------------------------------------------------

def _row(lang: str, policy: str, *, seconds: float = 30.0, index: int = 0) -> dict:
    sha = hashlib.sha256(f"{lang}/{policy}/{index}".encode()).hexdigest()
    return {
        "split": "train",
        "allowed_use": ["asr_train"],
        "audio_checksum_sha256": sha,
        "audio_filepath": f"s3://medzen-speech/curated/{lang}/audio/{sha}.wav",
        "text_normalized": f"row {index} of {lang}",
        "duration_s": seconds,
        "license_policy": policy,
        "_lang": lang,
    }


def config_gate_rows(config, rows):
    from pipeline.licence_filter import filter_training_rows
    return filter_training_rows(rows, allowed=config.allowed_policies)


class FakeS3:
    """Just enough of the S3 surface for load_mix: one version, N languages."""

    def __init__(self, rows_by_lang: dict[str, list[dict]], version: str = "v9"):
        self.version = version
        self.objects: dict[str, bytes] = {}
        manifests = {}
        for lang, rows in rows_by_lang.items():
            key = f"curated/{lang}/asr/default/{version}/manifest.jsonl"
            body = "\n".join(json.dumps({k: v for k, v in r.items()
                                         if k != "_lang"}) for r in rows).encode()
            self.objects[key] = body
            manifests[f"{lang}/asr/default"] = {
                "sha256": hashlib.sha256(body).hexdigest()}
        complete = json.dumps({"manifests": manifests}).encode()
        self.objects[f"curated/_versions/{version}/COMPLETE.json"] = complete
        self.objects[f"curated/_versions/{version}/ADOPTION.json"] = json.dumps({
            "status": "approved",
            "complete_raw_sha256": hashlib.sha256(complete).hexdigest(),
        }).encode()

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise KeyError(Key)
        return {"Body": type("B", (), {"read": lambda _s, b=self.objects[Key]: b})()}

    def list_objects_v2(self, **kwargs):
        keys = [k for k in sorted(self.objects)
                if k.startswith(kwargs.get("Prefix", "")) and k.endswith("manifest.jsonl")]
        return {"Contents": [{"Key": k} for k in keys], "IsTruncated": False}


def test_licence_gate_removes_rows_before_weights_are_taken():
    """Two languages, equal CLEAR rows; one also carries blocked rows. If the
    gate ran after sampling, the blocked language would out-weigh the clean
    one; running before, both contribute identically."""
    rows = {
        "yemba": [_row("yemba", "cc0", index=i) for i in range(20)],
        "wolof": ([_row("wolof", "cc0", index=i) for i in range(20)]
                  + [_row("wolof", "research_only", index=100 + i)
                     for i in range(60)]),
    }
    config = make_config(MEDZEN_LANGUAGES="yemba,wolof")
    mix, provenance = build_gated_mix(config, client=FakeS3(rows))
    per_lang = {}
    for r in mix:
        per_lang[r["_lang"]] = per_lang.get(r["_lang"], 0) + 1
    assert provenance["pool_gate"]["excluded_rows_by_policy"] == {
        "research_only": 60}
    assert provenance["pool_gate"]["applied"] == "before temperature sampling"
    assert per_lang["yemba"] == per_lang["wolof"], (
        "equal post-gate pools must draw equal shares — the gate ran late")


def test_missing_license_policy_refuses_the_whole_run():
    rows = {"yemba": [_row("yemba", "cc0")]}
    del rows["yemba"][0]["license_policy"]
    config = make_config()
    with pytest.raises(LicencePolicyRefusal, match="no license_policy"):
        build_gated_mix(config, client=FakeS3(rows))


def test_unknown_license_policy_refuses_the_whole_run():
    rows = {"yemba": [_row("yemba", "wtfpl")]}
    config = make_config()
    with pytest.raises(LicencePolicyRefusal, match="unknown"):
        build_gated_mix(config, client=FakeS3(rows))


def test_audio_cap_subsamples_before_sampling_and_is_deterministic():
    rows = {"yemba": [_row("yemba", "cc0", seconds=3600.0, index=i)
                      for i in range(10)]}  # 10 hours in the pool
    config = make_config(MEDZEN_AUDIO_CAP_HOURS="2")
    mix_a, prov_a = build_gated_mix(config, client=FakeS3(rows))
    mix_b, prov_b = build_gated_mix(config, client=FakeS3(rows))
    capped = prov_a["per_language_audio_cap"]["capped_languages"]["yemba"]
    assert capped["hours_before"] == 10.0
    assert capped["hours_after"] == 2.0
    assert capped["rows_after"] == 2
    assert [r["audio_checksum_sha256"] for r in mix_a] == \
           [r["audio_checksum_sha256"] for r in mix_b], "cap must be deterministic"
    assert prov_a == prov_b


def test_uncapped_language_is_untouched_and_unreported():
    rows = {"yemba": [_row("yemba", "cc0", seconds=60.0, index=i)
                      for i in range(5)]}
    config = make_config()  # 100h default cap, pool holds 5 minutes
    _, provenance = build_gated_mix(config, client=FakeS3(rows))
    assert provenance["per_language_audio_cap"]["capped_languages"] == {}


def test_fingerprint_binds_config_and_corpus_state():
    rows = {"yemba": [_row("yemba", "cc0", index=i) for i in range(4)]}
    config = make_config()
    _, prov = build_gated_mix(config, client=FakeS3(rows))
    base = run_fingerprint(config, prov)
    assert run_fingerprint(config, prov) == base, "fingerprint must be stable"
    other_config = make_config(MEDZEN_SEED="8")
    assert run_fingerprint(other_config, prov) != base
    rows["yemba"].append(_row("yemba", "cc0", index=99))
    _, moved_prov = build_gated_mix(config, client=FakeS3(rows))
    assert run_fingerprint(config, moved_prov) != base


# --------------------------------------------------------------------------
# checkpoint bookkeeping (no torch: markers and refusals are pure files)
# --------------------------------------------------------------------------

def _fake_checkpoint(directory: Path, step: int, fingerprint: str) -> None:
    name = f"step-{step:07d}.pt"
    (directory / name).write_bytes(f"state-{step}".encode())
    write_checkpoint_marker(directory, step=step, checkpoint_name=name,
                            fingerprint=fingerprint)


def test_fresh_directory_resumes_from_nothing(tmp_path):
    assert read_resume_state(tmp_path, "f" * 64) is None


def test_marker_roundtrip_returns_latest_step(tmp_path):
    _fake_checkpoint(tmp_path, 50, "f" * 64)
    _fake_checkpoint(tmp_path, 100, "f" * 64)
    marker = read_resume_state(tmp_path, "f" * 64)
    assert marker["step"] == 100
    assert marker["checkpoint"] == "step-0000100.pt"


def test_foreign_fingerprint_refuses_resume(tmp_path):
    _fake_checkpoint(tmp_path, 50, "a" * 64)
    with pytest.raises(TrainerRefusal, match="different run"):
        read_resume_state(tmp_path, "b" * 64)


def test_missing_checkpoint_file_is_corruption_not_restart(tmp_path):
    _fake_checkpoint(tmp_path, 50, "f" * 64)
    (tmp_path / "step-0000050.pt").unlink()
    with pytest.raises(TrainerRefusal, match="corrupt"):
        read_resume_state(tmp_path, "f" * 64)


def test_torn_checkpoint_bytes_are_refused(tmp_path):
    _fake_checkpoint(tmp_path, 50, "f" * 64)
    (tmp_path / "step-0000050.pt").write_bytes(b"torn")
    with pytest.raises(TrainerRefusal, match="torn|differs"):
        read_resume_state(tmp_path, "f" * 64)


# --------------------------------------------------------------------------
# data plumbing (no torch)
# --------------------------------------------------------------------------

def test_batch_rows_wrap_deterministically():
    mix = [{"i": n} for n in range(5)]
    assert [r["i"] for r in batch_rows(mix, 2, 0)] == [0, 1]
    assert [r["i"] for r in batch_rows(mix, 2, 2)] == [4, 0]
    assert batch_rows(mix, 2, 7) == batch_rows(mix, 2, 7)


def test_fetch_audio_verifies_the_manifest_checksum(tmp_path):
    body = b"PCM-BYTES"
    row = _row("yemba", "cc0")
    row["audio_checksum_sha256"] = hashlib.sha256(body).hexdigest()
    row["audio_filepath"] = "s3://medzen-speech/curated/yemba/a.wav"

    class Cli:
        def get_object(self, Bucket, Key):
            return {"Body": type("B", (), {"read": lambda _s: body})()}

    local = fetch_audio(Cli(), row, tmp_path)
    assert local.read_bytes() == body

    row_bad = dict(row, audio_checksum_sha256="0" * 64)
    with pytest.raises(DataRefusal, match="changed after ingest"):
        fetch_audio(Cli(), row_bad, tmp_path)


# --------------------------------------------------------------------------
# the loop itself (torch; runs inside the trainer image at C3)
# --------------------------------------------------------------------------

@_needs_torch
def test_kill_and_resume_matches_uninterrupted_run(tmp_path):
    """3 steps + checkpoint + fresh process to 6 == straight run to 6."""
    import torch

    from pipeline.omniasr_train import run_training_loop

    def build(seed):
        torch.manual_seed(seed)
        model = torch.nn.Linear(4, 3)
        optimizer = torch.optim.SGD(model.parameters(), lr=0.1)
        return model, optimizer

    def batches(index):
        generator = torch.Generator().manual_seed(1000 + index)
        return (torch.randn(2, 4, generator=generator),
                torch.randn(2, 3, generator=generator))

    def batch_loss(model, batch):
        inputs, wanted = batch
        return torch.nn.functional.mse_loss(model(inputs), wanted)

    def run(directory, max_steps, model, optimizer):
        env = dict(BASE_ENV, MEDZEN_MAX_STEPS=str(max_steps),
                   MEDZEN_GRAD_ACCUM="2", MEDZEN_CHECKPOINT_EVERY="3",
                   MEDZEN_CHECKPOINT_DIR=str(directory))
        config = parse_config(env)
        stop_flag = {"stop": False}

        def save_state(path, step):
            with path.open("wb") as stream:
                torch.save({"step": step,
                            "model": model.state_dict(),
                            "optimizer": optimizer.state_dict()}, stream)

        def load_state(path):
            state = torch.load(path, weights_only=False)
            model.load_state_dict(state["model"])
            optimizer.load_state_dict(state["optimizer"])
            return int(state["step"])

        return run_training_loop(
            model=model, optimizer=optimizer, batches=batches,
            batch_loss=batch_loss, config=config, fingerprint="f" * 64,
            save_state=save_state, load_state=load_state, stop_flag=stop_flag)

    straight_dir = tmp_path / "straight"
    straight_dir.mkdir()
    model, optimizer = build(11)
    straight = run(straight_dir, 6, model, optimizer)
    straight_weights = {k: v.clone() for k, v in model.state_dict().items()}

    resumed_dir = tmp_path / "resumed"
    resumed_dir.mkdir()
    model_a, optimizer_a = build(11)
    first = run(resumed_dir, 3, model_a, optimizer_a)
    assert first["status"] == "COMPLETED" and first["step"] == 3
    model_b, optimizer_b = build(999)  # deliberately different init:
    # resume must overwrite it entirely from the checkpoint
    second = run(resumed_dir, 6, model_b, optimizer_b)
    assert second["resumed_from"] == 3 and second["step"] == 6

    for key, wanted in straight_weights.items():
        assert torch.equal(model_b.state_dict()[key], wanted), (
            f"{key} diverged across the kill/resume boundary")
    assert straight["losses"][3:] == second["losses"], (
        "post-resume losses must replay the uninterrupted trajectory")


@_needs_torch
def test_stop_flag_checkpoints_and_reports_interruption(tmp_path):
    import torch

    from pipeline.omniasr_train import run_training_loop

    model = torch.nn.Linear(2, 2)
    optimizer = torch.optim.SGD(model.parameters(), lr=0.1)
    env = dict(BASE_ENV, MEDZEN_MAX_STEPS="10",
               MEDZEN_CHECKPOINT_DIR=str(tmp_path))
    config = parse_config(env)

    outcome = run_training_loop(
        model=model, optimizer=optimizer,
        batches=lambda i: torch.ones(1, 2),
        batch_loss=lambda m, b: m(b).sum(),
        config=config, fingerprint="f" * 64,
        save_state=lambda p, s: p.write_bytes(b"x"),
        load_state=lambda p: 0,
        stop_flag={"stop": True})
    assert outcome["status"] == "INTERRUPTED_CHECKPOINTED"
    assert (tmp_path / "LATEST.json").exists()


def test_single_oversized_row_survives_the_cap_and_is_reported():
    """A row bigger than the whole cap is kept — a cap must never empty a
    language — and the report shows hours_after above the cap so the
    excess is visible, not hidden."""
    rows = {"yemba": [_row("yemba", "cc0", seconds=7200.0, index=0),
                      _row("yemba", "cc0", seconds=7200.0, index=1)]}
    config = make_config(MEDZEN_AUDIO_CAP_HOURS="1")
    _, provenance = build_gated_mix(config, client=FakeS3(rows))
    capped = provenance["per_language_audio_cap"]["capped_languages"]["yemba"]
    assert capped["rows_after"] == 1
    assert capped["hours_after"] == 2.0, "the excess is reported, not hidden"


def test_model_staging_verifies_and_refuses_drift(tmp_path):
    from pipeline.omniasr_train import stage_model_artifacts

    body = b"frozen-weights"
    good = {"model.pt": {"sha256": hashlib.sha256(body).hexdigest(), "parts": 2}}
    halves = [body[:6], body[6:]]

    class Cli:
        def download_fileobj(self, bucket, key, stream):
            assert key.endswith(("part-0000", "part-0001")), key
            stream.write(halves[int(key[-1])])

    staged = stage_model_artifacts(Cli(), destination=tmp_path, artifacts=good)
    assert staged == {"model.pt": good["model.pt"]["sha256"]}
    # cached file is REVERIFIED: corrupt it and staging must refuse
    (tmp_path / "model.pt").write_bytes(b"tampered")
    with pytest.raises(TrainerRefusal, match="drifted"):
        stage_model_artifacts(Cli(), destination=tmp_path, artifacts=good)
    assert not (tmp_path / "model.pt").exists(), "drifted file must be removed"


def test_model_staging_pins_the_evaluated_identities():
    from pipeline.omniasr_train import CTC_MODEL_ARTIFACTS, MODEL_ROOT_PREFIX
    assert CTC_MODEL_ARTIFACTS["omniASR-CTC-1B-v2.pt"]["sha256"].startswith("354f9817")
    assert CTC_MODEL_ARTIFACTS["omniASR_tokenizer_written_v2.model"]["sha256"].startswith("8aa11a10")
    assert MODEL_ROOT_PREFIX.startswith("research/asr-base-model/pilot/1cdca3e7")
    assert MODEL_ROOT_PREFIX.endswith("bundles/")


# --------------------------------------------------------------------------
# Full fine-tune mode — Codex finding 2026-08-20 (owner-ordered corrections).
# The paid failure mode was: training completes, then export KeyErrors on
# LoRA-only audit fields and merge_lora refuses the adapter-free model.
# --------------------------------------------------------------------------

def test_full_mode_requires_an_explicit_learning_rate():
    with pytest.raises(TrainerRefusal, match="EXPLICIT MEDZEN_LR"):
        make_config(MEDZEN_TRAIN_MODE="full", MEDZEN_WARMUP_STEPS="100")
    config = make_config(MEDZEN_TRAIN_MODE="full", MEDZEN_LR="1e-5",
                          MEDZEN_WARMUP_STEPS="100",
                          MEDZEN_LR_SCHEDULE="constant")
    assert config.learning_rate == 1e-5


def test_full_mode_learning_rate_is_finite_and_bounded():
    """Codex reviews #2 + #4: 0, 1000, NaN, Infinity were accepted; then
    the #2 bound was boundary-INCLUSIVE and still blessed 1e-3 — the
    documented runaway rate. Cap = 1e-4, the highest probe-proven rate."""
    for bad in ("0", "1000", "nan", "inf", "2e-3", "1e-3", "2e-4"):
        with pytest.raises(TrainerRefusal, match="MEDZEN_LR|finite"):
            make_config(MEDZEN_TRAIN_MODE="full", MEDZEN_LR=bad,
                        MEDZEN_WARMUP_STEPS="100")
    assert make_config(MEDZEN_TRAIN_MODE="full", MEDZEN_LR="1e-4",
                       MEDZEN_WARMUP_STEPS="100",
                       MEDZEN_LR_SCHEDULE="constant").learning_rate == 1e-4


def test_unknown_train_mode_is_refused_at_parse_time():
    """Codex review #2: an unknown mode used to pass parse and refuse only
    after mix building and staging had spent time and bytes."""
    with pytest.raises(TrainerRefusal, match="MEDZEN_TRAIN_MODE"):
        make_config(MEDZEN_TRAIN_MODE="fullfinetune", MEDZEN_LR="1e-5",
                    MEDZEN_WARMUP_STEPS="100")


def test_full_mode_warmup_is_explicit_and_bounded():
    """Codex review #4: 0, > max_steps and absurd warmups all passed (and
    the old test BLESSED zero). Now: explicit AND 1 <= warmup < max_steps."""
    with pytest.raises(TrainerRefusal, match="MEDZEN_WARMUP_STEPS"):
        make_config(MEDZEN_TRAIN_MODE="full", MEDZEN_LR="1e-5")
    for bad in ("0", "600", "999999999"):   # max_steps defaults to 600
        with pytest.raises(TrainerRefusal,
                           match="warmup|MEDZEN_WARMUP_STEPS"):
            make_config(MEDZEN_TRAIN_MODE="full", MEDZEN_LR="1e-5",
                        MEDZEN_WARMUP_STEPS=bad,
                        MEDZEN_LR_SCHEDULE="constant")
    assert make_config(MEDZEN_TRAIN_MODE="full", MEDZEN_LR="1e-5",
                       MEDZEN_WARMUP_STEPS="100",
                       MEDZEN_LR_SCHEDULE="constant").warmup_steps == 100


def test_full_mode_requires_a_declared_post_warmup_schedule():
    """Codex review #4: no silent schedule in full mode."""
    with pytest.raises(TrainerRefusal, match="MEDZEN_LR_SCHEDULE"):
        make_config(MEDZEN_TRAIN_MODE="full", MEDZEN_LR="1e-5",
                    MEDZEN_WARMUP_STEPS="100")
    with pytest.raises(TrainerRefusal, match="MEDZEN_LR_SCHEDULE"):
        make_config(MEDZEN_TRAIN_MODE="full", MEDZEN_LR="1e-5",
                    MEDZEN_WARMUP_STEPS="100", MEDZEN_LR_SCHEDULE="step")
    config = make_config(MEDZEN_TRAIN_MODE="full", MEDZEN_LR="1e-5",
                          MEDZEN_WARMUP_STEPS="100",
                          MEDZEN_LR_SCHEDULE="cosine")
    assert config.lr_schedule == "cosine"


def test_cosine_schedule_decays_to_the_ten_percent_floor():
    from pipeline.omniasr_train import scheduled_lr
    base, warm, total = 1e-5, 100, 1100
    assert scheduled_lr(base, 0, warm, total, "cosine") == pytest.approx(1e-7)
    assert scheduled_lr(base, warm, warm, total, "cosine") == pytest.approx(base)
    mid = scheduled_lr(base, warm + 500, warm, total, "cosine")
    assert 0.5 * base < mid < 0.6 * base
    assert scheduled_lr(base, total, warm, total, "cosine") == pytest.approx(
        0.1 * base)
    assert scheduled_lr(base, 10**9, warm, total, "cosine") == pytest.approx(
        0.1 * base)


def test_full_mode_trains_exactly_one_language_per_job():
    with pytest.raises(TrainerRefusal, match="one language"):
        make_config(MEDZEN_TRAIN_MODE="full", MEDZEN_LR="1e-5",
                    MEDZEN_WARMUP_STEPS="100",
                    MEDZEN_LR_SCHEDULE="constant",
                    MEDZEN_LANGUAGES="yemba,kinyarwanda")


def test_warmup_is_linear_stateless_and_off_by_default():
    from pipeline.omniasr_train import warmup_lr
    assert make_config().warmup_steps == 0
    assert warmup_lr(1e-5, step=0, warmup_steps=0) == 1e-5
    assert warmup_lr(1e-5, step=0, warmup_steps=100) == pytest.approx(1e-7)
    assert warmup_lr(1e-5, step=49, warmup_steps=100) == pytest.approx(5e-6)
    assert warmup_lr(1e-5, step=100, warmup_steps=100) == 1e-5
    assert warmup_lr(1e-5, step=5000, warmup_steps=100) == 1e-5


def test_disk_envelope_refuses_before_gpu_hours(tmp_path):
    from pipeline.omniasr_train import check_disk_envelope
    config = make_config(MEDZEN_TRAIN_MODE="full", MEDZEN_LR="1e-5",
                          MEDZEN_WARMUP_STEPS="100",
                          MEDZEN_LR_SCHEDULE="constant",
                          MEDZEN_MAX_STEPS="40000",
                          MEDZEN_CHECKPOINT_EVERY="2000",
                          MEDZEN_CHECKPOINT_DIR=str(tmp_path / "ckpt"))
    mix = [{"duration_s": 3600.0}] * 1440   # ~1,440 h at 32 kB/s
    with pytest.raises(TrainerRefusal, match="disk envelope"):
        check_disk_envelope(config, mix, cache_root=tmp_path / "cache",
                            free_bytes=lambda p: 100_000_000_000)  # 100 GB
    report = check_disk_envelope(config, mix, cache_root=tmp_path / "cache",
                                 free_bytes=lambda p: 10**12)      # 1 TB
    assert report["audio_cache_bytes"] > 150_000_000_000
    assert report["checkpoint_bytes"] > 50_000_000_000


def test_disk_envelope_sums_needs_that_share_a_filesystem(tmp_path):
    """Codex review #2 reproduction: on the 300 h/12k-step shape, separate
    per-path checks approved a 100 GB SHARED filesystem for a ~116.8 GB
    total (38.0 audio + 72.8 checkpoints + 6.0 staging). Needs on one
    device must be summed."""
    from pipeline.omniasr_train import check_disk_envelope
    config = make_config(MEDZEN_TRAIN_MODE="full", MEDZEN_LR="1e-5",
                          MEDZEN_WARMUP_STEPS="100",
                          MEDZEN_LR_SCHEDULE="constant",
                          MEDZEN_LANGUAGES="kinyarwanda",
                          MEDZEN_MAX_STEPS="12000",
                          MEDZEN_CHECKPOINT_EVERY="500",
                          MEDZEN_CHECKPOINT_DIR=str(tmp_path / "ckpt"))
    mix = [{"duration_s": 3600.0}] * 300
    (tmp_path / "cache").mkdir()
    (tmp_path / "ckpt").mkdir()
    with pytest.raises(TrainerRefusal, match="disk envelope"):
        check_disk_envelope(config, mix, cache_root=tmp_path / "cache",
                            free_bytes=lambda p: 100_000_000_000,
                            device_of=lambda p: "one-device")
    # genuinely separate filesystems may each pass at 100 GB
    report = check_disk_envelope(config, mix, cache_root=tmp_path / "cache",
                                 free_bytes=lambda p: 100_000_000_000,
                                 device_of=lambda p: str(p))
    assert report["checkpoint_bytes"] < 100_000_000_000


@_needs_torch
def test_full_mode_export_skips_the_lora_merge_and_binds_the_manifest(tmp_path):
    import torch
    from torch import nn
    from pipeline.omniasr_export import ExportRefusal, export_merged_checkpoint
    from pipeline.omniasr_lora import LoRALinear

    model = nn.Sequential(nn.Linear(4, 4), nn.Linear(4, 2))
    export = export_merged_checkpoint(
        model, output_dir=tmp_path / "full",
        base_model_card="card", tokenizer_reference="tok",
        decode_config={"strategy": "ctc_greedy"},
        gate_report_reference=None, train_mode="full",
        training_run_identity={"wrap_audit": {"mode": "full"}})
    assert export["status"] == "PASS_MERGED_EXPORT"
    manifest = json.loads(Path(export["manifest"]).read_bytes())
    assert manifest["train_mode"] == "full"
    assert manifest["merged_modules"] == []
    # fresh-process reload: the artifact must load standalone
    state = torch.load(export["checkpoint"], map_location="cpu",
                       weights_only=True)
    reloaded = nn.Sequential(nn.Linear(4, 4), nn.Linear(4, 2))
    reloaded.load_state_dict(state)

    # a full-mode model with adapters still attached is a wiring bug —
    # refuse, never merge silently
    adapterful = nn.Sequential(LoRALinear(nn.Linear(4, 4), rank=2, alpha=4.0,
                                           dropout=0.0))
    with pytest.raises(ExportRefusal, match="adapter-free"):
        export_merged_checkpoint(
            adapterful, output_dir=tmp_path / "bad",
            base_model_card="card", tokenizer_reference="tok",
            decode_config={"strategy": "ctc_greedy"},
            gate_report_reference=None, train_mode="full",
            training_run_identity={})


@_needs_torch
def test_full_checkpoint_resume_is_trajectory_equivalent(tmp_path):
    """The Codex core risk: without optimizer moments a resumed AdamW run
    diverges from the paid trajectory. Straight-through 4 steps must equal
    2 steps + save + FRESH model/optimizer + load + 2 steps, bit-for-bit."""
    import torch
    from torch import nn
    from pipeline.omniasr_train import load_full_state, save_full_state

    def build():
        torch.manual_seed(7)
        model = nn.Linear(8, 8)
        return model, torch.optim.AdamW(model.parameters(), lr=1e-2)

    torch.manual_seed(123)
    data = [torch.randn(4, 8) for _ in range(4)]

    def step(model, optimizer, batch):
        optimizer.zero_grad(set_to_none=True)
        loss = model(batch).pow(2).mean()
        loss.backward()
        optimizer.step()

    straight, opt_a = build()
    for batch in data:
        step(straight, opt_a, batch)

    resumed, opt_b = build()
    for batch in data[:2]:
        step(resumed, opt_b, batch)
    save_full_state(tmp_path / "step-2.pt", model=resumed, optimizer=opt_b,
                    step=2)
    fresh, opt_c = build()   # fresh process stand-in: brand-new objects
    assert load_full_state(tmp_path / "step-2.pt", model=fresh,
                           optimizer=opt_c) == 2
    for batch in data[2:]:
        step(fresh, opt_c, batch)

    for p_straight, p_fresh in zip(straight.parameters(), fresh.parameters()):
        assert torch.equal(p_straight, p_fresh), (
            "resumed trajectory diverged from the uninterrupted one")


@_needs_torch
def test_full_resume_refuses_a_torn_checkpoint_pair(tmp_path):
    import torch
    from torch import nn
    from pipeline.omniasr_train import (OPTIMIZER_SIDECAR, load_full_state,
                                         save_full_state)

    model = nn.Linear(4, 4)
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-2)
    save_full_state(tmp_path / "step-1.pt", model=model, optimizer=optimizer,
                    step=1)
    # sidecar from a different step = crash landed between the two writes
    torch.save({"step": 9, "optimizer": optimizer.state_dict()},
               tmp_path / OPTIMIZER_SIDECAR)
    with pytest.raises(TrainerRefusal, match="torn"):
        load_full_state(tmp_path / "step-1.pt", model=model,
                        optimizer=optimizer)
    (tmp_path / OPTIMIZER_SIDECAR).unlink()
    with pytest.raises(TrainerRefusal, match="optimizer"):
        load_full_state(tmp_path / "step-1.pt", model=model,
                        optimizer=optimizer)


@_needs_torch
def test_one_step_full_training_checkpoints_through_the_real_loop(tmp_path):
    """Codex verify item: one-step full-parameter training through
    run_training_loop with the REAL full-mode save path, then reload."""
    import torch
    from torch import nn
    from pipeline.omniasr_train import (load_full_state, run_training_loop,
                                         save_full_state)

    config = make_config(MEDZEN_TRAIN_MODE="full", MEDZEN_LR="1e-4",
                          MEDZEN_WARMUP_STEPS="1",
                          MEDZEN_LR_SCHEDULE="constant",
                          MEDZEN_MAX_STEPS="2", MEDZEN_BATCH_SIZE="1",
                          MEDZEN_GRAD_ACCUM="1", MEDZEN_CHECKPOINT_EVERY="1",
                          MEDZEN_CHECKPOINT_DIR=str(tmp_path))
    torch.manual_seed(config.seed)
    model = nn.Linear(6, 6)
    for parameter in model.parameters():
        parameter.requires_grad_(True)
    optimizer = torch.optim.AdamW(model.parameters(),
                                  lr=config.learning_rate)
    outcome = run_training_loop(
        model=model, optimizer=optimizer,
        batches=lambda i: torch.randn(2, 6),
        batch_loss=lambda m, b: m(b).pow(2).mean(),
        config=config, fingerprint="f" * 64,
        save_state=lambda path, step: save_full_state(
            path, model=model, optimizer=optimizer, step=step),
        load_state=lambda path: load_full_state(
            path, model=model, optimizer=optimizer))
    assert outcome["status"] == "COMPLETED"
    assert outcome["step"] == 2
    saved = torch.load(tmp_path / "step-0000002.pt", map_location="cpu",
                       weights_only=False)
    assert set(saved) >= {"step", "model", "torch_rng"}
    assert (tmp_path / "optimizer-LATEST.pt").exists()


@_needs_torch
def test_full_resume_refuses_corrupted_optimizer_moments(tmp_path):
    """Codex review #2 reproduction, verbatim: poison every AdamW moment
    while PRESERVING the step. Step-equality alone accepted this; the
    hash chain (marker -> model checkpoint -> sidecar) must refuse it."""
    import torch
    from torch import nn
    from pipeline.omniasr_train import (OPTIMIZER_SIDECAR, load_full_state,
                                         save_full_state)

    model = nn.Linear(4, 4)
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-2)
    model(torch.randn(2, 4)).sum().backward()
    optimizer.step()
    save_full_state(tmp_path / "step-1.pt", model=model, optimizer=optimizer,
                    step=1)
    sidecar = torch.load(tmp_path / OPTIMIZER_SIDECAR, weights_only=False)
    for state in sidecar["optimizer"]["state"].values():
        for key, value in state.items():
            if torch.is_tensor(value):
                state[key] = torch.full_like(value, 9e9)
    torch.save(sidecar, tmp_path / OPTIMIZER_SIDECAR)   # step still says 1
    with pytest.raises(TrainerRefusal, match="hash"):
        load_full_state(tmp_path / "step-1.pt", model=model,
                        optimizer=optimizer)


def test_disk_envelope_audio_bound_is_draws_aware(tmp_path):
    """ml.g6.xlarge ground truth (launch refusal 2026-08-20): storage is a
    fixed 250 GB NVMe. The cache can only hold rows the sampler draws, so
    the audio bound is the top-K longest rows (K = draws), not the whole
    mix — the whole-mix bound wrongly refused the v2 shape that fits."""
    from pipeline.omniasr_train import check_disk_envelope
    config = make_config(MEDZEN_TRAIN_MODE="full", MEDZEN_LR="1e-5",
                          MEDZEN_WARMUP_STEPS="500",
                          MEDZEN_LR_SCHEDULE="constant",
                          MEDZEN_LANGUAGES="kinyarwanda",
                          MEDZEN_MAX_STEPS="40000",
                          MEDZEN_BATCH_SIZE="2", MEDZEN_GRAD_ACCUM="8",
                          MEDZEN_CHECKPOINT_EVERY="2000",
                          MEDZEN_CHECKPOINT_DIR=str(tmp_path / "ckpt"))
    # ~1.02M-row corpus at ~5.1 s avg: whole-mix bound would be ~183 GB,
    # but 40k steps can draw at most 640k rows
    mix = [{"duration_s": 5.1}] * 1_018_628
    report = check_disk_envelope(config, mix, cache_root=tmp_path / "cache",
                                 free_bytes=lambda p: 250_000_000_000,
                                 device_of=lambda p: "one-device")
    draws = 40_000 * 2 * 8
    assert report["audio_cache_bytes"] == int(draws * 5.1 * 32_000 * 1.10)
    assert report["audio_cache_bytes"] < 120_000_000_000
    # a SHORT run over the same corpus needs almost nothing
    tiny = make_config(MEDZEN_TRAIN_MODE="full", MEDZEN_LR="1e-5",
                        MEDZEN_WARMUP_STEPS="10",
                        MEDZEN_LR_SCHEDULE="constant",
                        MEDZEN_LANGUAGES="kinyarwanda",
                        MEDZEN_MAX_STEPS="30",
                        MEDZEN_BATCH_SIZE="2", MEDZEN_GRAD_ACCUM="8",
                        MEDZEN_CHECKPOINT_EVERY="10",
                        MEDZEN_CHECKPOINT_DIR=str(tmp_path / "ckpt2"))
    small = check_disk_envelope(tiny, mix, cache_root=tmp_path / "cache",
                                free_bytes=lambda p: 250_000_000_000,
                                device_of=lambda p: "one-device")
    assert small["audio_cache_bytes"] < 100_000_000


@_needs_torch
def test_sweep_merge_tool_handles_full_checkpoints(tmp_path, monkeypatch):
    """Self-review catch 2026-08-20: t6_checkpoint_merge.py was LoRA-only
    (wrap_lora + state['lora']) and would have crashed on v2's full
    checkpoints. The full branch extracts state['model'] as the servable
    dict, refuses adapter residue, and never touches fairseq2."""
    import torch
    from torch import nn

    root = Path(__file__).resolve().parents[1]
    src_path = root / "scripts" / "t6_checkpoint_merge.py"
    if src_path.exists():   # scripts/ is not shipped into the trainer image
        src = src_path.read_text()
        assert '"model" in state and "lora" not in state' in src
        assert "refusing an ambiguous artifact" in src
        assert "NON-FINITE" in src

    # behavioural check of the same extraction logic on a real full ckpt
    model = nn.Linear(4, 4)
    ckpt = {"step": 2000, "model": model.state_dict(),
            "torch_rng": torch.get_rng_state(), "cuda_rng": None,
            "optimizer_sidecar_sha256": "ab" * 32,
            "optimizer_sidecar_step": 2000}
    path = tmp_path / "step-0002000.pt"
    torch.save(ckpt, path)
    state = torch.load(path, map_location="cpu", weights_only=False)
    assert "model" in state and "lora" not in state
    reloaded = nn.Linear(4, 4)
    reloaded.load_state_dict(state["model"])
    for a, b in zip(model.parameters(), reloaded.parameters()):
        assert torch.equal(a, b)


@_needs_torch
def test_nonfinite_loss_fails_closed_without_persisting(tmp_path):
    """Codex review #4 reproduction (was: NaN loss -> COMPLETED with
    non-finite parameters). Now the loop stops BEFORE the optimizer step,
    reports TRAINING_DIVERGED_NONFINITE, and persists nothing."""
    import torch
    from torch import nn
    from pipeline.omniasr_train import run_training_loop

    config = make_config(MEDZEN_TRAIN_MODE="full", MEDZEN_LR="1e-4",
                          MEDZEN_WARMUP_STEPS="1",
                          MEDZEN_LR_SCHEDULE="constant",
                          MEDZEN_MAX_STEPS="3", MEDZEN_BATCH_SIZE="1",
                          MEDZEN_GRAD_ACCUM="1", MEDZEN_CHECKPOINT_EVERY="3",
                          MEDZEN_CHECKPOINT_DIR=str(tmp_path))
    model = nn.Linear(4, 4)
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-4)
    outcome = run_training_loop(
        model=model, optimizer=optimizer,
        batches=lambda i: torch.randn(1, 4),
        batch_loss=lambda m, b: m(b).pow(2).mean() * float("nan"),
        config=config, fingerprint="f" * 64,
        save_state=lambda p, s: p.write_bytes(b"poison"),
        load_state=lambda p: 0)
    assert outcome["status"] == "TRAINING_DIVERGED_NONFINITE"
    assert outcome["nonfinite"] == "loss"
    assert not list(tmp_path.glob("step-*.pt")), "poison must not persist"
    assert all(torch.isfinite(p).all() for p in model.parameters()), (
        "the guard must fire BEFORE the optimizer step")


@_needs_torch
def test_poisoned_parameters_refuse_the_checkpoint_boundary(tmp_path):
    """Self-review 2026-08-20: the checkpoint-time parameter scan was an
    untested branch. Finite losses with silently poisoned weights must
    stop at the boundary WITHOUT persisting."""
    import torch
    from torch import nn
    from pipeline.omniasr_train import run_training_loop

    config = make_config(MEDZEN_TRAIN_MODE="full", MEDZEN_LR="1e-4",
                          MEDZEN_WARMUP_STEPS="1",
                          MEDZEN_LR_SCHEDULE="constant",
                          MEDZEN_MAX_STEPS="2", MEDZEN_BATCH_SIZE="1",
                          MEDZEN_GRAD_ACCUM="1", MEDZEN_CHECKPOINT_EVERY="2",
                          MEDZEN_CHECKPOINT_DIR=str(tmp_path))
    model = nn.Linear(4, 4)
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-4)
    calls = {"n": 0}

    def sneaky_loss(m, b):
        calls["n"] += 1
        if calls["n"] == 2:   # poison AFTER step 1 succeeded
            with torch.no_grad():
                m.weight[0, 0] = float("inf")
        return m(b).pow(2).mean().nan_to_num(0.0, 0.0, 0.0)

    outcome = run_training_loop(
        model=model, optimizer=optimizer,
        batches=lambda i: torch.randn(1, 4),
        batch_loss=sneaky_loss,
        config=config, fingerprint="f" * 64,
        save_state=lambda p, s: p.write_bytes(b"poison"),
        load_state=lambda p: 0)
    assert outcome["status"] == "TRAINING_DIVERGED_NONFINITE"
    assert outcome["nonfinite"] in ("parameters", "grad_norm", "loss")
    assert not list(tmp_path.glob("step-*.pt"))


def test_multilingual_full_ft_requires_the_architecture_ack():
    """Codex review #6: the pilot (ARCH-2026-001) needs multilingual full
    FT, which the review-#1 guard refused outright. The guard now opens
    ONLY to a run that explicitly cites the architecture record."""
    with pytest.raises(TrainerRefusal, match="ARCH-2026-001"):
        make_config(MEDZEN_TRAIN_MODE="full", MEDZEN_LR="1e-5",
                    MEDZEN_WARMUP_STEPS="100", MEDZEN_LR_SCHEDULE="constant",
                    MEDZEN_LANGUAGES="kinyarwanda,english,french")
    config = make_config(MEDZEN_TRAIN_MODE="full", MEDZEN_LR="1e-5",
                          MEDZEN_WARMUP_STEPS="100",
                          MEDZEN_LR_SCHEDULE="constant",
                          MEDZEN_LANGUAGES="kinyarwanda,english,french",
                          MEDZEN_MULTILINGUAL_FULL_ACK="ARCH-2026-001")
    assert sorted(config.languages) == ["english", "french", "kinyarwanda"]


def test_mix_refuses_silent_partial_language_coverage():
    """Codex review #6: requesting languages the version does not carry
    used to silently train on the remainder."""
    rows = {"yemba": [_row("yemba", "cc0", index=i) for i in range(5)]}
    config = make_config(MEDZEN_LANGUAGES="yemba,english")
    with pytest.raises(SystemExit, match="contribute no eligible rows"):
        build_gated_mix(config, client=FakeS3(rows))
