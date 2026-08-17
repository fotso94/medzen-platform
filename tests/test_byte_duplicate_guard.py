"""Byte-duplicate guard: identical audio bytes listed more than once.

yemba_egra gb1 shipped 8 such pairs — 4 re-listings (occ_1/occ_2 of one
recording, same text) and 4 pairs where different word ids point at identical
bytes, i.e. conflicting transcripts. The hand-built gb2 dropped the conflicts
mechanically; ingest must instead dedup re-listings automatically and REFUSE
on conflicts with a named error, so the source gets repaired.

All offline: tiny synthetic WAVs, no S3, no network.
"""
from __future__ import annotations

import io
import struct
import sys
import wave
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.adapters.base import (ConflictingDuplicateAudioError,  # noqa: E402
                                    dedupe_byte_duplicates)
from pipeline.adapters.yemba_egra import YembaEGRAAdapter  # noqa: E402


# --------------------------------------------------------------------------- #
# fixtures: a miniature YembaEGRA archive
# --------------------------------------------------------------------------- #
def _wav(seed: int, frames: int = 8000) -> bytes:
    """0.5 s of 16 kHz mono PCM_16, deterministic per seed, distinct across
    seeds — long enough to clear the 0.3 s asr_words floor."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(struct.pack(
            f"<{frames}h",
            *(((seed * 7919 + i * 40503) % 20000) - 10000 for i in range(frames))))
    return buf.getvalue()


def _corpus(tmp_path, monkeypatch, wavs: dict[str, bytes],
            words: dict[str, str]) -> YembaEGRAAdapter:
    root = tmp_path / "YembaEGRA"
    (root / "metadata").mkdir(parents=True)
    audio = root / "audio" / "batch1"
    audio.mkdir(parents=True)
    (root / "metadata" / "words_corpus.csv").write_text(
        "id_word,Yemba\n" + "".join(f"{k},{v}\n" for k, v in words.items()),
        encoding="utf-8")
    (root / "metadata" / "speakers_description.csv").write_text(
        "SpeakerId,Gender\n1,female\n7,male\n", encoding="utf-8")
    for name, data in wavs.items():
        (audio / name).write_bytes(data)
    monkeypatch.setenv("MEDZEN_YEMBA_EGRA_DIR", str(root))
    return YembaEGRAAdapter("yemba")


WORDS = {"1": "ŋgya meŋwa ne", "2": "mba ŋgɔpna apaka alā",
         "14": "ŋgya meŋwa ne", "15": "mba ŋgɔpna apaka alā"}


# --------------------------------------------------------------------------- #
# adapter: same-text re-listing -> keep first
# --------------------------------------------------------------------------- #
def test_same_text_byte_dup_keeps_first_listing(tmp_path, monkeypatch):
    """occ_1/occ_2 pointing at one recording is a re-listing, not new data:
    exactly one row survives, and it is the first in listing order."""
    dup = _wav(1)
    a = _corpus(tmp_path, monkeypatch, {
        "spkr_1_word_1_occ_1_ci_1_l_1.wav": dup,
        "spkr_1_word_1_occ_2_ci_1_l_1.wav": dup,
        "spkr_1_word_2_occ_1_ci_1_l_1.wav": _wav(2),
    }, WORDS)
    items = list(a.items())
    assert len(items) == 2, "re-listing must collapse to one row"
    shas = [it["record"]["audio_checksum_sha256"] for it in items]
    assert len(set(shas)) == 2, "distinct recordings must keep distinct checksums"
    kept = [it["stem"] for it in items if "word_1_" in it["stem"]]
    assert kept and kept[0].startswith("spkr_1_word_1_occ_1"), \
        "the FIRST listing must be the one kept"


def test_distinct_audio_is_untouched(tmp_path, monkeypatch):
    a = _corpus(tmp_path, monkeypatch, {
        "spkr_1_word_1_occ_1_ci_1_l_1.wav": _wav(1),
        "spkr_1_word_1_occ_2_ci_1_l_1.wav": _wav(2),
        "spkr_1_word_2_occ_1_ci_1_l_1.wav": _wav(3),
    }, WORDS)
    assert len(list(a.items())) == 3


# --------------------------------------------------------------------------- #
# adapter: conflicting-text byte-dup -> named refusal
# --------------------------------------------------------------------------- #
def test_conflicting_text_byte_dup_refuses_with_named_error(tmp_path, monkeypatch):
    """Identical bytes under word_14 and word_15 (the gb1 dc278a25… case):
    at least one label is wrong, so ingest must refuse — never pick a side."""
    dup = _wav(7)
    a = _corpus(tmp_path, monkeypatch, {
        "spkr_7_word_14_occ_1_ci_1_l_1.wav": dup,
        "spkr_7_word_15_occ_1_ci_1_l_1.wav": dup,
    }, WORDS)
    with pytest.raises(ConflictingDuplicateAudioError) as ei:
        list(a.items())
    e = ei.value
    assert len(e.pairs) == 1
    msg = str(e)
    assert "spkr_7_word_14_occ_1_ci_1_l_1.wav" in msg
    assert "spkr_7_word_15_occ_1_ci_1_l_1.wav" in msg
    assert "ŋgya meŋwa ne" in msg and "mba ŋgɔpna apaka alā" in msg, \
        "the error is the repair list — it must name both transcripts"


def test_every_conflicting_pair_is_reported_in_one_run(tmp_path, monkeypatch):
    """gb1 had 4 conflicting pairs. Raising on the first would take 4 failed
    runs to enumerate them; one run must name them all."""
    d1, d2 = _wav(10), _wav(11)
    a = _corpus(tmp_path, monkeypatch, {
        "spkr_1_word_1_occ_1_ci_1_l_1.wav": d1,
        "spkr_1_word_2_occ_1_ci_1_l_1.wav": d1,
        "spkr_7_word_14_occ_1_ci_1_l_1.wav": d2,
        "spkr_7_word_15_occ_1_ci_1_l_1.wav": d2,
    }, WORDS)
    with pytest.raises(ConflictingDuplicateAudioError) as ei:
        list(a.items())
    assert len(ei.value.pairs) == 2


# --------------------------------------------------------------------------- #
# shared guard: manifest-level, adapter-agnostic
# --------------------------------------------------------------------------- #
def _item(sha: str, text: str, path: str) -> dict:
    return {"record": {"audio_checksum_sha256": sha, "text_verbatim": text,
                       "text_normalized": text.lower(), "audio_filepath": path}}


def test_shared_guard_dedups_same_text_keeping_first():
    items = [_item("aa", "ŋgya", "s3://b/1.wav"),
             _item("aa", "ŋgya", "s3://b/2.wav"),
             _item("bb", "mba", "s3://b/3.wav")]
    kept = dedupe_byte_duplicates(items)
    assert [it["record"]["audio_filepath"] for it in kept] == \
        ["s3://b/1.wav", "s3://b/3.wav"]


def test_shared_guard_refuses_conflicting_text():
    items = [_item("aa", "ŋgya meŋwa ne", "s3://b/1.wav"),
             _item("aa", "mba ŋgɔpna", "s3://b/2.wav")]
    with pytest.raises(ConflictingDuplicateAudioError) as ei:
        dedupe_byte_duplicates(items)
    (sha, ref_a, text_a, ref_b, text_b), = ei.value.pairs
    assert (sha, ref_a, ref_b) == ("aa", "s3://b/1.wav", "s3://b/2.wav")
    assert (text_a, text_b) == ("ŋgya meŋwa ne", "mba ŋgɔpna")


def test_shared_guard_passes_distinct_audio_through_unchanged():
    items = [_item("aa", "one", "s3://b/1.wav"),
             _item("bb", "two", "s3://b/2.wav")]
    assert dedupe_byte_duplicates(items) == items


# --------------------------------------------------------------------------- #
# ingest wiring: the guard must run before validation/upload
# --------------------------------------------------------------------------- #
def test_ingest_runs_the_guard_before_validation_and_upload():
    src = (ROOT / "pipeline" / "ingest.py").read_text()
    assert "dedupe_byte_duplicates" in src, "ingest must run the shared guard"
    assert "ConflictingDuplicateAudioError" in src, \
        "conflicts must refuse by NAME, not crash with a stack trace"
    assert src.index("dedupe_byte_duplicates(") < src.index("validating against A3"), \
        "guard must run before validation, so nothing dup'd is ever uploaded"
