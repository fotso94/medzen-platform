"""Opt-in per-language mix weights (MEDMIX design 2026-10-04).

MEDZEN_MIX_LANGUAGE_WEIGHTS='medumba=15' gives a language w times the
temperature-0 mix share of an unweighted language. Unset, the trainer must be
exactly as it was: the same parsed fingerprint (the golden captured from the
unmodified trainer) and the same mix rows in the same order (goldens captured
from load_mix before the knob existed). Host-safe: no torch needed.
"""
from __future__ import annotations

import collections
import hashlib
import io
import json

import pytest

import pipeline.omniasr_train as T
from pipeline.omniasr_train import (
    TrainerRefusal,
    build_gated_mix,
    parse_config,
    parse_mix_language_weights,
    run_fingerprint,
)
from pipeline.train_asr import load_mix

BASE_ENV = {"MEDZEN_VARIANT": "ctc", "MEDZEN_MANIFEST_VERSION": "v9",
            "MEDZEN_LANGUAGES": "yemba", "MEDZEN_SEED": "7"}
PROV = {"manifest_version": "gb11", "eligible_rows": 82769}
# captured from the unmodified trainer (also pinned in test_train_augmentation_knobs)
GOLDEN_BASE_ENV = "ac20f2ce8334969c03cb8f831ac45fe355135f44bd8b19d506b3e5a743679cb2"
# the rtn15w retention-pilot environment (CM-PILOT-RTN15W packet), inlined so the
# image test stage does not need the manifests directory
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

# ---------------------------------------------------------------- fixture
VERSION = "vw1"
LANGS = ["aa", "bb", "cc"]
# load_mix goldens captured BEFORE the knob existed (master 57bbf40)
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


def _digest(mix):
    return hashlib.sha256("\n".join(f"{r['_lang']}:{r['audio_checksum_sha256']}" for r in mix).encode()).hexdigest()


def _mix(temperature=0.0, **kw):
    return load_mix(_Stub(), temperature=temperature, seed=7, languages=LANGS, version=VERSION, **kw)


# ---------------------------------------------------------------- unset = unchanged
@pytest.mark.parametrize("extra", [{}, {"MEDZEN_MIX_LANGUAGE_WEIGHTS": ""}, {"MEDZEN_MIX_LANGUAGE_WEIGHTS": " , "},
                                   {"MEDZEN_MIX_LANGUAGE_WEIGHTS": "yemba=1"}, {"MEDZEN_MIX_LANGUAGE_WEIGHTS": "yemba=1.00"}])
def test_default_spellings_keep_the_pre_knob_fingerprint(extra):
    if extra.get("MEDZEN_MIX_LANGUAGE_WEIGHTS", "").strip() == ",":
        with pytest.raises(TrainerRefusal):
            parse_config(dict(BASE_ENV, **extra))
        return
    config = parse_config(dict(BASE_ENV, **extra))
    assert config.mix_language_weights == ()
    assert "mix_language_weights" not in config.fingerprint_payload()
    assert run_fingerprint(config, PROV) == GOLDEN_BASE_ENV


@pytest.mark.parametrize("weights", [None, {}])
def test_unset_mix_is_byte_identical_to_the_pre_knob_mix(weights):
    for temperature, golden in ((0.0, GOLDEN_MIX_T0), (0.5, GOLDEN_MIX_T05)):
        mix, prov = _mix(temperature, mix_language_weights=weights)
        assert _digest(mix) == golden
        assert "mix_language_weights" not in prov
    mix, _ = _mix(0.0)  # parameter omitted entirely
    assert _digest(mix) == GOLDEN_MIX_T0


# ---------------------------------------------------------------- weighted mix
def test_weight_multiplies_the_temperature_zero_share():
    mix, prov = _mix(0.0, mix_language_weights={"bb": 3.0})
    counts = collections.Counter(r["_lang"] for r in mix)
    assert counts == {"aa": 15, "bb": 45, "cc": 15}           # 75 rows: 1/5, 3/5, 1/5
    rec = prov["mix_language_weights"]
    assert rec["weights"] == {"bb": 3.0}
    assert rec["mix_rows_by_language"] == {"aa": 15, "bb": 45, "cc": 15}
    assert len({r["audio_checksum_sha256"] for r in mix if r["_lang"] == "bb"}) == 10  # repeats from a 10-row pool


def test_medmix_shape_gives_medumba_fifteen_shares():
    sizes = [("l%02d" % i, 50) for i in range(14)] + [("medumba", 30)]
    mix, prov = load_mix(_Stub(sizes), temperature=0.0, seed=1, languages=[s for s, _ in sizes],
                         version=VERSION, mix_language_weights={"medumba": 15})
    counts = collections.Counter(r["_lang"] for r in mix)
    target = sum(n for _, n in sizes)
    assert counts["medumba"] == round(target * 15 / 29)
    assert all(counts[s] == round(target / 29) for s, _ in sizes if s != "medumba")


@pytest.mark.parametrize("temperature", [0.5, 1.0])
def test_weights_refuse_any_temperature_but_zero(temperature):
    with pytest.raises(SystemExit, match="only at temperature 0"):
        _mix(temperature, mix_language_weights={"bb": 2.0})


def test_weights_refuse_a_language_outside_the_mix():
    with pytest.raises(SystemExit, match="contribute no rows"):
        _mix(0.0, mix_language_weights={"zz": 2.0})


@pytest.mark.parametrize("bad", [0, -1.0, float("nan"), float("inf"), True, "2"])
def test_weights_refuse_non_positive_or_non_numeric(bad):
    with pytest.raises(SystemExit, match="finite > 0"):
        _mix(0.0, mix_language_weights={"bb": bad})


# ---------------------------------------------------------------- parsing
def test_parse_is_canonical_and_case_insensitive():
    langs = ("bafia", "ewe", "medumba")
    assert parse_mix_language_weights(" Medumba = 15 ", langs) == (("medumba", 15.0),)
    assert parse_mix_language_weights("medumba=15,ewe=2.5", langs) == (("ewe", 2.5), ("medumba", 15.0))
    assert parse_mix_language_weights("medumba=1,ewe=1.0", langs) == ()


@pytest.mark.parametrize("raw,match", [
    ("medumba", "not lang=weight"), ("medumba=", "not lang=weight"), ("=3", "not lang=weight"),
    ("medumba=abc", "not a number"), ("medumba=2,medumba=3", "more than once"),
    ("yemba=2", "not in MEDZEN_LANGUAGES"), ("medumba=0", r"\(0, 100\]"), ("medumba=-2", r"\(0, 100\]"),
    ("medumba=nan", r"\(0, 100\]"), ("medumba=inf", r"\(0, 100\]"), ("medumba=101", r"\(0, 100\]"),
    ("medumba=1.005", "two decimals"), (",", "names no language"),
])
def test_bad_weights_fail_closed(raw, match):
    with pytest.raises(TrainerRefusal, match=match):
        parse_mix_language_weights(raw, ("bafia", "ewe", "medumba"))


def test_medmix_environment_parses_and_binds_the_fingerprint():
    rtn = parse_config(RTN15W_ENV)
    medmix = parse_config(dict(RTN15W_ENV, MEDZEN_MIX_LANGUAGE_WEIGHTS="medumba=15", MEDZEN_MAX_STEPS="5800"))
    assert medmix.mix_language_weights == (("medumba", 15.0),)
    assert rtn.mix_language_weights == () and "mix_language_weights" not in rtn.fingerprint_payload()
    assert medmix.fingerprint_payload()["mix_language_weights"] == [["medumba", 15.0]]
    assert run_fingerprint(medmix, PROV) != run_fingerprint(
        parse_config(dict(RTN15W_ENV, MEDZEN_MAX_STEPS="5800")), PROV)


@pytest.mark.parametrize("extra,match", [
    ({"MEDZEN_TEMPERATURE": "0.5"}, "only at MEDZEN_TEMPERATURE=0"),
    ({"MEDZEN_LANGUAGES": "medumba"}, "at least two languages"),
    ({"MEDZEN_TRAIN_MODE": "full", "MEDZEN_LORA_TARGETS": "", "MEDZEN_LORA_TRAINABLE_DTYPE": "",
      "MEDZEN_WARMUP_STEPS": "100", "MEDZEN_LR_SCHEDULE": "constant", "MEDZEN_MULTILINGUAL_FULL_ACK": "ARCH-2026-001",
      "MEDZEN_CHECKPOINT_EVERY": "1000"}, "MEDZEN_TRAIN_MODE"),
])
def test_weights_are_refused_outside_plain_temperature_zero_lora(extra, match):
    env = dict(RTN15W_ENV, MEDZEN_MIX_LANGUAGE_WEIGHTS="medumba=15", **extra)
    with pytest.raises(TrainerRefusal, match=match):
        parse_config(env)


def test_weights_are_refused_with_kd(monkeypatch):
    env = dict(RTN15W_ENV, MEDZEN_MIX_LANGUAGE_WEIGHTS="medumba=15", MEDZEN_LORA_TARGETS="",
               MEDZEN_LORA_TRAINABLE_DTYPE="", MEDZEN_KD_ENABLE="1",
               MEDZEN_KD_PRESERVATION_LANGUAGES="medumba")
    with pytest.raises(TrainerRefusal, match="KD"):
        parse_config(env)


# ---------------------------------------------------------------- trainer plumbing
def _capture(monkeypatch, provenance):
    seen = {}

    def fake_load_mix(cli, **kw):
        seen.update(kw)
        return [{"_lang": "medumba", "duration_s": 3.0}], provenance

    monkeypatch.setattr(T, "load_mix", fake_load_mix)
    return seen


def test_unset_plumbing_passes_none(monkeypatch, capsys):
    seen = _capture(monkeypatch, {"manifest_version": "gb11"})
    build_gated_mix(parse_config(dict(RTN15W_ENV, MEDZEN_EXCLUSIONS_REF="")), client=object())
    assert seen["mix_language_weights"] is None
    assert "MIX_LANGUAGE_WEIGHTS_APPLIED" not in capsys.readouterr().out


def test_set_plumbing_passes_weights_and_prints_the_marker(monkeypatch, capsys):
    applied = {"weights": {"medumba": 15.0}, "mix_rows_by_language": {"medumba": 3}, "applied": "x"}
    seen = _capture(monkeypatch, {"manifest_version": "gb11", "mix_language_weights": applied})
    config = parse_config(dict(RTN15W_ENV, MEDZEN_EXCLUSIONS_REF="", MEDZEN_MIX_LANGUAGE_WEIGHTS="medumba=15"))
    build_gated_mix(config, client=object())
    assert seen["mix_language_weights"] == {"medumba": 15.0}
    line = [l for l in capsys.readouterr().out.splitlines() if "MIX_LANGUAGE_WEIGHTS_APPLIED" in l]
    assert len(line) == 1 and json.loads(line[0])["weights"] == {"medumba": 15.0}


def test_set_plumbing_refuses_a_mix_that_did_not_record_the_weights(monkeypatch):
    _capture(monkeypatch, {"manifest_version": "gb11"})
    config = parse_config(dict(RTN15W_ENV, MEDZEN_EXCLUSIONS_REF="", MEDZEN_MIX_LANGUAGE_WEIGHTS="medumba=15"))
    with pytest.raises(TrainerRefusal, match="unweighted mix"):
        build_gated_mix(config, client=object())
