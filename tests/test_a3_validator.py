"""A3 validator must accept a clean manifest and reject each known defect.

If a check is ever weakened, one of these turns red. Run: .venv/bin/python -m pytest tests -q
"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
VALIDATOR = ROOT / "scripts" / "validate_manifest.py"
FIX = ROOT / "tests" / "fixtures"


def run(fixture: str) -> tuple[int, str]:
    p = subprocess.run([sys.executable, str(VALIDATOR), str(FIX / fixture)],
                       capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def test_clean_manifest_passes():
    rc, out = run("good.jsonl")
    assert rc == 0, out
    assert "pass all A3 checks" in out


@pytest.mark.parametrize("fixture,marker", [
    ("bad_speaker_leak.jsonl", "LEAK"),
    ("bad_audio.jsonl",        "sample_rate=44100"),
    ("bad_neardup.jsonl",      "NEAR-DUP across splits"),
    ("bad_provenance.jsonl",   "provenance field consent_id missing"),
])
def test_defect_is_rejected(fixture, marker):
    rc, out = run(fixture)
    assert rc == 1, f"{fixture} should have been rejected\n{out}"
    assert marker in out, f"expected '{marker}' in output\n{out}"


def test_near_dup_survives_disjoint_speakers():
    """The case neither leak check can catch: same content, different speaker
    and session, split across train/test."""
    rc, out = run("bad_neardup.jsonl")
    assert rc == 1
    assert "LEAK" not in out, "leak checks should NOT fire here"
    assert "NEAR-DUP" in out, "near-dup check must be what catches it"


def test_architecture_stays_consistent():
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "validate_architecture.py")],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr


def test_generated_artifacts_are_current():
    """Generated IAM/k8s must match services.yaml — catches hand-edits."""
    p = subprocess.run([sys.executable, str(ROOT / "platform" / "generate.py"), "--check"],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr


# --------------------------------------------------------------------------- #
# A4 — registry
# --------------------------------------------------------------------------- #
import shutil

import yaml

REG = ROOT / "registry" / "languages"


def run_registry() -> tuple[int, str]:
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "validate_registry.py")],
                       capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def test_registry_is_valid():
    rc, out = run_registry()
    assert rc == 0, out


def test_all_languages_start_unapproved():
    """Nothing is trained yet — a language claiming otherwise is a bug."""
    for f in REG.glob("*.yaml"):
        d = yaml.safe_load(f.read_text())
        assert d["status"] in ("declared", "data_only"), \
            f"{f.stem} claims status {d['status']}"
        if d["status"] == "data_only":
            # The rung below declared: evaluation data only, nothing shipped.
            assert d["asr"]["artifact"] is None, f"{f.stem} claims an artifact"
            assert d["asr"]["approved_version"] is None
            assert d["asr"]["decode_strategy"] == "pending_experiment"
            continue
        assert d["tts"]["approved"] is False, f"{f.stem} claims an approved voice"
        ds = d["asr"]["decode_strategy"]
        if ds["mode"] == "pending_experiment":
            assert ds["chosen_by_run"] is None
        else:
            # A non-pending mode is only legitimate with evidence behind it.
            assert ds.get("chosen_by_run"), f"{f.stem}: decode mode without a run"
            assert ds.get("evidence"), f"{f.stem}: decode mode without paired evidence"


@pytest.mark.parametrize("mutate,expect", [
    (lambda d: d.update(status="production"),
     "requires an ASR artifact"),
    (lambda d: (d.update(status="gated"),
                d["asr"].update(artifact="s3://x/", approved_version="v1")),
     "decode_strategy is still pending_experiment"),
    (lambda d: (d.update(status="gated"),
                d["asr"].update(artifact="s3://x/", approved_version="v1"),
                d["asr"]["decode_strategy"].update(mode="en_token")),
     "no chosen_by_run"),
])
def test_readiness_ladder_blocks_premature_promotion(mutate, expect, tmp_path):
    """Runs against a COPY of the repository.

    This previously mutated the real registry/languages/acholi.yaml and
    restored it in a finally block. Two problems: it cannot run against a
    read-only checkout, which is how the verified bundle is mounted on the
    evaluation instance, and a crash between write and restore would leave the
    working tree silently modified with a promotion-ready acholi.
    """
    work = tmp_path / "repo"
    # schemas/ too: validate_registry.py resolves the JSON schema from its own
    # location, so a copy without it fails for the wrong reason.
    for sub in ("scripts", "registry", "schemas"):
        shutil.copytree(ROOT / sub, work / sub)
    target = work / "registry" / "languages" / "acholi.yaml"

    d = yaml.safe_load(target.read_text())
    mutate(d)
    target.write_text(yaml.dump(d, sort_keys=False))

    p = subprocess.run([sys.executable, str(work / "scripts" / "validate_registry.py")],
                       capture_output=True, text=True, cwd=work)
    rc, out = p.returncode, p.stdout + p.stderr
    assert rc == 1, f"promotion should have been blocked\n{out}"
    assert expect in out, f"expected '{expect}'\n{out}"
    # the real registry is untouched
    assert yaml.safe_load((REG / "acholi.yaml").read_text())["status"] != "production"


def test_manifest_language_check_uses_registry():
    p = subprocess.run([sys.executable, str(VALIDATOR), str(FIX / "good.jsonl"),
                        "--registry", str(REG)], capture_output=True, text=True)
    assert p.returncode == 0
    assert "check 5" not in p.stdout, "language check should no longer be skipped"


# --------------------------------------------------------------------------- #
# B2 — normalizers and split strategy
# --------------------------------------------------------------------------- #
import sys as _sys
_sys.path.insert(0, str(ROOT))
from pipeline.normalizers import for_language, strip_tones  # noqa: E402


def test_pidgin_normalizer_maps_unambiguous_variants():
    n = for_language("pidgin")
    assert n("Weting dey do you?") == "wetin dey do you"
    assert n("Abek, de pickin nor well") == "abeg de pikin no well"
    assert n.version == "pidgin-norm-v1"


@pytest.mark.parametrize("token", ["de", "wan", "shop", "sef"])
def test_ambiguous_pidgin_tokens_are_left_alone(token):
    """A mapping that is wrong half the time corrupts WER worse than none.
    These need context to disambiguate, so they pass through untouched."""
    assert for_language("pidgin")(token) == token


def test_tonal_languages_keep_diacritics_but_normalise_composition():
    y = for_language("yoruba")
    assert y.tonal is True
    composed, decomposed = "ọmọ náà", "ọmọ náà"
    assert y(composed) == y(decomposed), "NFC must make the two forms identical"
    assert "á" in y(composed), "tone marks are phonemic — never stripped by default"


def test_tone_stripping_is_available_separately():
    assert strip_tones("Ọmọ náà ń ṣàìsàn") == "Omo naa n saisan"


def test_split_strategy_selects_the_right_leak_check(tmp_path):
    """Parallel TTS corpora share speakers across splits BY DESIGN; ASR corpora
    must not. The declared strategy picks which invariant is enforced."""
    import json as _json
    base = _json.loads((FIX / "good.jsonl").read_text().splitlines()[0])

    def row(spk, txt, split, strat):
        r = dict(base)
        r.update(speaker_id=spk, session_id=f"ses_{spk}", text_normalized=txt,
                 text_verbatim=txt, split=split, split_strategy=strat)
        return r

    # same speaker both sides, different text -> OK under text_disjoint
    ok = tmp_path / "tts.jsonl"
    ok.write_text("\n".join(_json.dumps(r) for r in [
        row("spk1", "wetin dey happen for house", "train", "text_disjoint"),
        row("spk1", "the pikin get fever this morning", "test", "text_disjoint"),
    ]) + "\n")
    p = subprocess.run([sys.executable, str(VALIDATOR), str(ok)],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stdout

    # identical rows but declared speaker_disjoint -> must be rejected
    bad = tmp_path / "asr.jsonl"
    bad.write_text("\n".join(_json.dumps(r) for r in [
        row("spk1", "wetin dey happen for house", "train", "speaker_disjoint"),
        row("spk1", "the pikin get fever this morning", "test", "speaker_disjoint"),
    ]) + "\n")
    p = subprocess.run([sys.executable, str(VALIDATOR), str(bad)],
                       capture_output=True, text=True)
    assert p.returncode == 1 and "LEAK" in p.stdout


def test_near_dup_still_blocks_under_text_disjoint(tmp_path):
    """Relaxing the identity check must NOT relax text-disjointness."""
    import json as _json
    base = _json.loads((FIX / "good.jsonl").read_text().splitlines()[0])
    same = "take this medicine two times a day"
    rows = []
    for spk, split in (("spk1", "train"), ("spk2", "test")):
        r = dict(base)
        r.update(speaker_id=spk, session_id=f"ses_{spk}", text_normalized=same,
                 text_verbatim=same, split=split, split_strategy="text_disjoint")
        rows.append(r)
    f = tmp_path / "dup.jsonl"
    f.write_text("\n".join(_json.dumps(r) for r in rows) + "\n")
    p = subprocess.run([sys.executable, str(VALIDATOR), str(f)],
                       capture_output=True, text=True)
    assert p.returncode == 1 and "NEAR-DUP" in p.stdout
