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


def test_ctc_defaults_bind_the_eval_proven_identity():
    config = make_config()
    assert config.model_card == "medzen_omniASR_CTC_1B_v2"
    assert config.audio_cap_hours == 100.0
    assert sorted(config.allowed_policies) == ["cc0", "cc_by_4_0", "commercial_ok", "sharealike_review"]


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
