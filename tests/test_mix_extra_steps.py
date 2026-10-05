"""Opt-in extra single-language steps (MEDMIX design, revised 2026-10-04 after review).

MEDZEN_MIX_EXTRA_STEPS='medumba=2800' inserts 2,800 optimizer steps made only of
Medumba clips, evenly between the steps of the UNCHANGED base schedule. Every
other language must therefore train on exactly the recordings, in exactly the
order and step composition, of the run without the knob. Unset, the trainer is
exactly as before: the parsed fingerprint matches the pre-knob golden and
load_mix reproduces goldens captured before any MEDMIX change. Host-safe.
"""
from __future__ import annotations

import collections
import hashlib
import io
import json

import pytest

import pipeline.omniasr_train as T
from pipeline.omniasr_data import batch_rows
from pipeline.omniasr_train import (
    TrainerRefusal,
    build_gated_mix,
    parse_config,
    parse_mix_extra_steps,
    run_fingerprint,
    splice_extra_steps,
)
from pipeline.train_asr import load_mix

BASE_ENV = {"MEDZEN_VARIANT": "ctc", "MEDZEN_MANIFEST_VERSION": "v9",
            "MEDZEN_LANGUAGES": "yemba", "MEDZEN_SEED": "7"}
PROV = {"manifest_version": "gb11", "eligible_rows": 82769}
# captured from the unmodified trainer (also pinned in test_train_augmentation_knobs)
GOLDEN_BASE_ENV = "ac20f2ce8334969c03cb8f831ac45fe355135f44bd8b19d506b3e5a743679cb2"
RTN15W_ENV = {
    "MEDZEN_AUDIO_CAP_HOURS": "15", "MEDZEN_BATCH_SIZE": "2", "MEDZEN_CHECKPOINT_EVERY": "500",
    "MEDZEN_EXCLUSIONS_REF": "s3://medzen-speech/curated/_versions/gb3/DQ-2026-006-gb3-pulaar-question-mark-deferral.json",
    "MEDZEN_EXPECT_EXCLUDED": "1579", "MEDZEN_GRAD_ACCUM": "8", "MEDZEN_KD_ENABLE": "0",
    "MEDZEN_LANGUAGES": "bafia,basaa,english,ewe,ewondo,french,gbaya,kinyarwanda,lingala,medumba,ngiemboon,ngombala,pidgin,swahili,yangben",
    "MEDZEN_LORA_ALPHA": "32", "MEDZEN_LORA_DROPOUT": "0.05", "MEDZEN_LORA_RANK": "16", "MEDZEN_LR": "1e-4",
    "MEDZEN_MANIFEST_VERSION": "gb11", "MEDZEN_MAX_STEPS": "3000", "MEDZEN_SEED": "20260904",
    "MEDZEN_STUDENT_INIT_MODE": "base", "MEDZEN_TEMPERATURE": "0", "MEDZEN_TRAIN_MODE": "lora",
    "MEDZEN_VARIANT": "ctc", "MEDZEN_LORA_TRAINABLE_DTYPE": "float32",
    "MEDZEN_LORA_TARGETS": "self_attn.q_proj,self_attn.k_proj,self_attn.v_proj,self_attn.output_proj,ffn.inner_proj,ffn.output_proj",
}
MEDMIX_ENV = dict(RTN15W_ENV, MEDZEN_MIX_EXTRA_STEPS="medumba=2800", MEDZEN_MAX_STEPS="5800")

# ---------------------------------------------------------------- fixture
VERSION = "vw1"
LANGS = ["aa", "bb", "cc"]
# load_mix goldens captured on master 57bbf40, before any MEDMIX change
GOLDEN_MIX_T0 = "9d104ba81156920da716f437299f0cfe65500b4d417cfc9594ea01e0c5411410"
GOLDEN_MIX_T05 = "7548a5d9ab31dd946897196eaf5fc075b3580ab0f0eba35207b87cb60c73c346"


def _rows(lang, n):
    return [{"audio_checksum_sha256": hashlib.sha256(f"{lang}-{i}".encode()).hexdigest(),
             "duration_s": round(2.0 + (i % 7) * 0.5, 2), "split": "train",
             "allowed_use": ["asr_train"], "license_policy": "cc0",
             "primary_language": lang, "speaker_id": f"{lang}-s{i % 3}"} for i in range(n)]


class _Stub:
    def __init__(self, sizes=(("aa", 40), ("bb", 10), ("cc", 25))):
        self.store, manifests = {}, {}
        for lang, n in sizes:
            body = "".join(json.dumps(r, sort_keys=True) + "\n" for r in _rows(lang, n)).encode()
            key = f"curated/{lang}/asr/cv_{lang}/{VERSION}/manifest.jsonl"
            self.store[key] = body
            manifests[f"{lang}/asr/cv_{lang}"] = {"key": key, "sha256": hashlib.sha256(body).hexdigest()}
        comp = json.dumps({"version": VERSION, "manifests": manifests}, sort_keys=True).encode()
        self.store[f"curated/_versions/{VERSION}/COMPLETE.json"] = comp
        self.store[f"curated/_versions/{VERSION}/ADOPTION.json"] = json.dumps(
            {"status": "approved", "complete_raw_sha256": hashlib.sha256(comp).hexdigest()}).encode()

    def get_object(self, Bucket, Key, **kw):
        return {"Body": io.BytesIO(self.store[Key])}

    def list_objects_v2(self, **kw):
        return {"Contents": [{"Key": k} for k in sorted(self.store) if k.endswith("manifest.jsonl")],
                "IsTruncated": False}


def _digest(rows):
    return hashlib.sha256("\n".join(f"{r['_lang']}:{r['audio_checksum_sha256']}" for r in rows).encode()).hexdigest()


def _mix(temperature=0.0, sizes=(("aa", 40), ("bb", 10), ("cc", 25)), seed=7):
    return load_mix(_Stub(sizes), temperature=temperature, seed=seed, languages=[s for s, _ in sizes], version=VERSION)


def _base_draws(mix, steps, batch, accum):
    """What the run WITHOUT the knob consumes: batch_rows over the mix, step by step."""
    return [batch_rows(mix, batch, s * accum + m) for s in range(steps) for m in range(accum)]


# ---------------------------------------------------------------- unset = unchanged
@pytest.mark.parametrize("extra", [{}, {"MEDZEN_MIX_EXTRA_STEPS": ""}, {"MEDZEN_MIX_EXTRA_STEPS": "yemba=0"},
                                   {"MEDZEN_MIX_EXTRA_STEPS": " yemba = 000 "}])
def test_default_spellings_keep_the_pre_knob_fingerprint(extra):
    config = parse_config(dict(BASE_ENV, **extra))
    assert config.mix_extra_steps == ()
    assert "mix_extra_steps" not in config.fingerprint_payload()
    assert run_fingerprint(config, PROV) == GOLDEN_BASE_ENV


def test_load_mix_is_byte_identical_to_its_pre_medmix_self():
    assert _digest(_mix(0.0)[0]) == GOLDEN_MIX_T0
    assert _digest(_mix(0.5)[0]) == GOLDEN_MIX_T05
    assert "mix_extra_steps" not in _mix(0.0)[1]


# ---------------------------------------------------------------- the splice
@pytest.mark.parametrize("max_steps,extra,batch,accum", [(29, 14, 2, 8), (10, 1, 2, 4), (10, 9, 2, 4), (7, 3, 3, 2)])
def test_base_steps_survive_intact_and_in_order(max_steps, extra, batch, accum):
    mix, _ = _mix(0.0)
    schedule, rep = splice_extra_steps(mix, "bb", extra, max_steps=max_steps, batch_size=batch,
                                       grad_accum=accum, seed=7)
    per_step = batch * accum
    assert len(schedule) == max_steps * per_step
    steps = [schedule[p * per_step:(p + 1) * per_step] for p in range(max_steps)]
    extra_positions = [p for p in range(max_steps) if (p + 1) * extra // max_steps > p * extra // max_steps]
    base_steps = [st for p, st in enumerate(steps) if p not in extra_positions]
    expected = _base_draws(mix, max_steps - extra, batch, accum)
    got = [st[m * batch:(m + 1) * batch] for st in base_steps for m in range(accum)]
    assert got == expected                                  # same rows, same order, same micro-batches
    assert all(r["_lang"] == "bb" for p in extra_positions for r in steps[p])
    assert len(extra_positions) == extra == rep["extra_steps"]
    assert rep["base_steps"] == max_steps - extra and rep["base_draws"] == (max_steps - extra) * per_step
    # batch_rows never wraps over the schedule
    assert [batch_rows(schedule, batch, i) for i in range(max_steps * accum)] == \
        [schedule[i * batch:(i + 1) * batch] for i in range(max_steps * accum)]


def test_other_languages_consume_exactly_the_base_runs_recordings():
    mix, _ = _mix(0.0)
    schedule, _ = splice_extra_steps(mix, "bb", 28, max_steps=58, batch_size=2, grad_accum=8, seed=7)
    base = [r for mb in _base_draws(mix, 30, 2, 8) for r in mb]
    for lang in ("aa", "cc"):
        assert [r["audio_checksum_sha256"] for r in schedule if r["_lang"] == lang] == \
            [r["audio_checksum_sha256"] for r in base if r["_lang"] == lang]


def test_extra_steps_are_spread_evenly():
    mix, _ = _mix(0.0)
    _, rep = splice_extra_steps(mix, "bb", 2800, max_steps=5800, batch_size=2, grad_accum=8, seed=7)
    positions = [p for p in range(5800) if (p + 1) * 2800 // 5800 > p * 2800 // 5800]
    gaps = {b - a for a, b in zip(positions, positions[1:])}
    assert gaps <= {2, 3} and len(positions) == 2800
    assert rep["first_extra_steps"] == positions[:8]


def test_extra_rows_cycle_the_whole_pool_evenly_and_deterministically():
    mix, _ = _mix(0.0)
    a_sched, a = splice_extra_steps(mix, "bb", 14, max_steps=29, batch_size=2, grad_accum=8, seed=7)
    b_sched, b = splice_extra_steps(mix, "bb", 14, max_steps=29, batch_size=2, grad_accum=8, seed=7)
    _, c = splice_extra_steps(mix, "bb", 14, max_steps=29, batch_size=2, grad_accum=8, seed=8)
    assert a == b and a_sched == b_sched                      # deterministic
    assert c["base_draws_sha256"] == a["base_draws_sha256"]   # the seed only moves the extra stream
    assert c["extra_draws_sha256"] != a["extra_draws_sha256"]
    assert a["extra_pool_distinct"] == 10                     # every bb recording in the mix
    positions = [p for p in range(29) if (p + 1) * 14 // 29 > p * 14 // 29]
    extra_rows = [r["audio_checksum_sha256"] for p in positions for r in a_sched[p * 16:(p + 1) * 16]]
    counts = collections.Counter(extra_rows)
    assert len(counts) == 10 and max(counts.values()) - min(counts.values()) <= 1


def test_splice_refuses_bad_inputs():
    mix, _ = _mix(0.0)
    for extra in (0, 29, 30):
        with pytest.raises(TrainerRefusal):
            splice_extra_steps(mix, "bb", extra, max_steps=29, batch_size=2, grad_accum=8, seed=7)
    with pytest.raises(TrainerRefusal, match="no 'zz' rows"):
        splice_extra_steps(mix, "zz", 3, max_steps=29, batch_size=2, grad_accum=8, seed=7)
    with pytest.raises(TrainerRefusal, match="empty mix"):
        splice_extra_steps([], "bb", 3, max_steps=29, batch_size=2, grad_accum=8, seed=7)


# ---------------------------------------------------------------- parsing
def test_parse_is_canonical():
    langs = ("bafia", "ewe", "medumba")
    assert parse_mix_extra_steps(" Medumba = 2800 ", langs) == ("medumba", 2800)
    assert parse_mix_extra_steps("medumba=0", langs) == ()
    assert parse_mix_extra_steps("", langs) == ()


@pytest.mark.parametrize("raw,match", [
    ("medumba", "not language=steps"), ("medumba=", "not language=steps"), ("=3", "not language=steps"),
    ("medumba=2.5", "whole number"), ("medumba=-3", "whole number"), ("medumba=abc", "whole number"),
    ("medumba=3,ewe=2", "exactly one"), (" , ", "exactly one"), ("yemba=3", "not in MEDZEN_LANGUAGES"),
])
def test_bad_values_fail_closed(raw, match):
    with pytest.raises(TrainerRefusal, match=match):
        parse_mix_extra_steps(raw, ("bafia", "ewe", "medumba"))


def test_medmix_environment_parses_and_binds_the_fingerprint():
    medmix, rep = parse_config(MEDMIX_ENV), parse_config(RTN15W_ENV)
    assert medmix.mix_extra_steps == ("medumba", 2800) and medmix.max_steps == 5800
    assert rep.mix_extra_steps == () and "mix_extra_steps" not in rep.fingerprint_payload()
    assert medmix.fingerprint_payload()["mix_extra_steps"] == ["medumba", 2800]
    assert run_fingerprint(medmix, PROV) != run_fingerprint(parse_config(dict(RTN15W_ENV, MEDZEN_MAX_STEPS="5800")), PROV)


@pytest.mark.parametrize("extra,match", [
    ({"MEDZEN_TEMPERATURE": "0.5"}, "MEDZEN_TEMPERATURE=0"),
    ({"MEDZEN_LANGUAGES": "medumba"}, "at least two languages"),
    ({"MEDZEN_MAX_STEPS": "2800"}, "leaves no base steps"),
    ({"MEDZEN_TRAIN_MODE": "full", "MEDZEN_LORA_TARGETS": "", "MEDZEN_LORA_TRAINABLE_DTYPE": "",
      "MEDZEN_WARMUP_STEPS": "100", "MEDZEN_LR_SCHEDULE": "constant", "MEDZEN_MULTILINGUAL_FULL_ACK": "ARCH-2026-001",
      "MEDZEN_CHECKPOINT_EVERY": "1000"}, "MEDZEN_TRAIN_MODE"),
])
def test_refused_outside_plain_temperature_zero_lora(extra, match):
    with pytest.raises(TrainerRefusal, match=match):
        parse_config(dict(MEDMIX_ENV, **extra))


def test_refused_with_kd():
    env = dict(MEDMIX_ENV, MEDZEN_LORA_TARGETS="", MEDZEN_LORA_TRAINABLE_DTYPE="", MEDZEN_KD_ENABLE="1",
               MEDZEN_KD_PRESERVATION_LANGUAGES="medumba")
    with pytest.raises(TrainerRefusal, match="KD"):
        parse_config(env)


# ---------------------------------------------------------------- trainer plumbing
def _fake_mix(monkeypatch):
    mix, prov = _mix(0.0, sizes=(("medumba", 12), ("ewe", 30)))
    monkeypatch.setattr(T, "load_mix", lambda cli, **kw: (mix, prov))
    return mix, prov


def test_unset_plumbing_returns_the_mix_untouched(monkeypatch, capsys):
    mix, prov = _fake_mix(monkeypatch)
    got, got_prov = build_gated_mix(parse_config(dict(RTN15W_ENV, MEDZEN_EXCLUSIONS_REF="",
                                                      MEDZEN_LANGUAGES="ewe,medumba")), client=object())
    assert got is mix and got_prov is prov
    assert "MIX_EXTRA_STEPS_APPLIED" not in capsys.readouterr().out


def test_set_plumbing_returns_the_schedule_and_prints_the_marker(monkeypatch, capsys):
    mix, _ = _fake_mix(monkeypatch)
    config = parse_config(dict(MEDMIX_ENV, MEDZEN_EXCLUSIONS_REF="", MEDZEN_LANGUAGES="ewe,medumba",
                               MEDZEN_MAX_STEPS="10", MEDZEN_MIX_EXTRA_STEPS="medumba=4"))
    schedule, prov = build_gated_mix(config, client=object())
    assert len(schedule) == 10 * 16 and prov["mix_extra_steps"]["extra_steps"] == 4
    lines = [l for l in capsys.readouterr().out.splitlines() if "MIX_EXTRA_STEPS_APPLIED" in l]
    assert len(lines) == 1 and json.loads(lines[0])["schedule_sha256"] == prov["mix_extra_steps"]["schedule_sha256"]
    base = [r for mb in _base_draws(mix, 6, 2, 8) for r in mb]
    assert [r["audio_checksum_sha256"] for r in schedule if r["_lang"] == "ewe"] == \
        [r["audio_checksum_sha256"] for r in base if r["_lang"] == "ewe"]
