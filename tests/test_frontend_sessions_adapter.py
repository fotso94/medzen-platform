"""Phase 4: the frontend_sessions adapter never emits unconsented or
unreviewed material, and rows carry the consent/allowed_use provenance."""
from __future__ import annotations

import io
import json
import struct
import sys
import wave
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipeline.adapters.frontend_sessions import (  # noqa: E402
    CONSENT_VERSION, FrontendSessionsAdapter, load_session)


def _wav(seconds: float = 2.0) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(struct.pack("<h", 0) * int(16000 * seconds))
    return buf.getvalue()


def _session(root: Path, rid: str, *, consent=True, version=CONSENT_VERSION,
             review=None, language="pcm"):
    d = root / "2026-09-02" / rid
    d.mkdir(parents=True)
    (d / "audio.wav").write_bytes(_wav())
    (d / "meta.json").write_text(json.dumps({
        "request_id": rid, "language": language, "session_pseudonym": "abc123",
        "asr_hypothesis": "i beg di money wey you send",
        "consent": {"granted": consent, "version": version},
        "retention": {"days": 90}}))
    if review is not None:
        (d / "review.json").write_text(json.dumps(review))
    return d


def test_only_consented_reviewed_sessions_become_rows(tmp_path, monkeypatch):
    _session(tmp_path, "a" * 36, review={"approved": True, "kind": "corrected",
                                         "text": "i beg, the money wey you send me"})
    _session(tmp_path, "b" * 36, review=None)                       # unreviewed
    _session(tmp_path, "c" * 36, consent=False, review={"approved": True, "text": "x y z"})
    _session(tmp_path, "d" * 36, version="old", review={"approved": True, "text": "x y z"})
    _session(tmp_path, "e" * 36, review={"approved": False, "text": "rejected text"})
    _session(tmp_path, "f" * 36, review={"approved": True, "text": ""})   # no text
    monkeypatch.setenv("MEDZEN_FRONTEND_SESSIONS_ROOT", str(tmp_path))
    rows = list(FrontendSessionsAdapter("pidgin").rows())
    assert len(rows) == 1
    row = rows[0]
    assert row["text_verbatim"] == "i beg, the money wey you send me"
    assert row["allowed_use"] == ["asr_train", "asr_eval"]
    assert row["consent_id"] == f"frontend-consent:{CONSENT_VERSION}"
    assert row["speaker_id"].startswith("fe-")
    assert row["source_id"] == "frontend_sessions"
    assert row["sample_rate"] == 16000 and row["channels"] == 1


def test_language_filter_and_unsupported_language(tmp_path, monkeypatch):
    _session(tmp_path, "g" * 36, language="fr", review={"approved": True, "text": "bonjour docteur"})
    monkeypatch.setenv("MEDZEN_FRONTEND_SESSIONS_ROOT", str(tmp_path))
    assert list(FrontendSessionsAdapter("pidgin").rows()) == []
    assert len(list(FrontendSessionsAdapter("french").rows())) == 1
    with pytest.raises(ValueError):
        FrontendSessionsAdapter("ewe")


def test_load_session_refuses_without_review_or_consent(tmp_path):
    d = _session(tmp_path, "h" * 36, review=None)
    assert load_session(d) is None
