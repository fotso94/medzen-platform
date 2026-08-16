"""The gb1/eval isolation audit: overlaps detected, isolation proven, licences inventoried."""

import json
from pathlib import Path

from scripts.gb1_eval_isolation_audit import audit


def _manifest(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def _row(lang, checksum, speaker, session, text, source="src", lic="cc0"):
    return {
        "audio_checksum_sha256": checksum,
        "text_normalized": text,
        "primary_language": lang,
        "speaker_id": speaker,
        "session_id": session,
        "source_id": source,
        "license_policy": lic,
        "consent_id": "dataset-level:test",
        "dataset_release": "test@abc",
    }


def test_disjoint_pools_pass(tmp_path):
    _manifest(tmp_path / "train/a.jsonl", [_row("alpha", "c1", "s1", "sess1", "hello there")])
    _manifest(tmp_path / "eval/a.jsonl", [_row("alpha", "c2", "s2", "sess2", "different text")])
    report = audit({"a.jsonl": tmp_path / "train/a.jsonl"}, {"a.jsonl": tmp_path / "eval/a.jsonl"})
    assert report["status"] == "PASS_ISOLATED"
    assert report["violations"] == []


def test_shared_audio_speaker_and_session_are_hard_violations(tmp_path):
    shared = _row("alpha", "cSAME", "sSAME", "sessSAME", "identical sentence")
    _manifest(tmp_path / "train/a.jsonl", [shared])
    _manifest(tmp_path / "eval/a.jsonl", [dict(shared)])
    report = audit({"a.jsonl": tmp_path / "train/a.jsonl"}, {"a.jsonl": tmp_path / "eval/a.jsonl"})
    assert report["status"] == "ISOLATION_VIOLATIONS_FOUND"
    kinds = {v["kind"] for v in report["violations"]}
    assert kinds == {"AUDIO_CONTENT", "SPEAKER", "SESSION"}


def test_same_speaker_id_across_sources_is_not_a_violation(tmp_path):
    _manifest(tmp_path / "train/a.jsonl", [_row("alpha", "c1", "42", "sess1", "x", source="srcA")])
    _manifest(tmp_path / "eval/a.jsonl", [_row("alpha", "c2", "42", "sess1", "y", source="srcB")])
    report = audit({"a.jsonl": tmp_path / "train/a.jsonl"}, {"a.jsonl": tmp_path / "eval/a.jsonl"})
    assert report["status"] == "PASS_ISOLATED"


def test_text_collisions_are_counted_not_fatal(tmp_path):
    _manifest(tmp_path / "train/a.jsonl", [_row("alpha", "c1", "s1", "e1", "common phrase")])
    _manifest(tmp_path / "eval/a.jsonl", [_row("alpha", "c2", "s2", "e2", "common phrase")])
    report = audit({"a.jsonl": tmp_path / "train/a.jsonl"}, {"a.jsonl": tmp_path / "eval/a.jsonl"})
    assert report["status"] == "PASS_ISOLATED"
    assert report["exact_text_collisions_total"] == 1


def test_research_only_licences_are_flagged(tmp_path):
    _manifest(tmp_path / "train/a.jsonl", [_row("alpha", "c1", "s1", "e1", "x", lic="afrispeech_nc_research")])
    _manifest(tmp_path / "eval/a.jsonl", [_row("alpha", "c2", "s2", "e2", "y")])
    report = audit({"a.jsonl": tmp_path / "train/a.jsonl"}, {"a.jsonl": tmp_path / "eval/a.jsonl"})
    assert report["licence_inventory"]["train:a.jsonl"]["research_only_flag"] is True
    assert report["licence_inventory"]["eval:a.jsonl"]["research_only_flag"] is False
