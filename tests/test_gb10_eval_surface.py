from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / "platform/evidence/GB10-ELEVEN-LANGUAGE-EVAL-SURFACE-2026-001.json"


def _record() -> dict:
    return json.loads(RECORD.read_bytes())


def _all_entries(value: dict):
    for language in value["per_language"].values():
        for bucket in language.values():
            yield from bucket


def test_every_pinned_surface_is_deduplicated_by_checksum():
    # eval/gbaya/asr/soreva-v1 ships 101 rows over 100 clips, one clip carrying
    # two different references. The record pins the r2 correction; this asserts
    # no pinned surface can regress to a row count above its clip count.
    for entry in _all_entries(_record()):
        assert entry["rows"] == entry["clips"], entry["surface"]


def test_every_surface_is_pinned_by_sha_and_version_id():
    for entry in _all_entries(_record()):
        assert len(entry["sha256"]) == 64, entry["surface"]
        assert entry["version_id"], entry["surface"]


def test_primary_surfaces_never_include_a_partition_or_a_duplicate():
    value = _record()
    for language in value["per_language"].values():
        primary = {e["surface"] for e in language["primary_surfaces"]}
        partitions = {e["surface"] for e in
                      language["partitions_of_a_primary_never_pool"]}
        duplicates = {e["surface"] for e in
                      language["identical_audio_duplicates_do_not_score_both"]}
        assert not (primary & partitions)
        assert not (primary & duplicates)


def test_the_three_contaminated_sets_are_never_primary():
    contaminated = {"eval/ewe/asr/v1", "eval/lingala/asr/v1",
                    "eval/lingala/asr/v2-holdout"}
    value = _record()
    for language in value["per_language"].values():
        assert not ({e["surface"] for e in language["primary_surfaces"]}
                    & contaminated)


def test_the_scorer_rule_names_the_checksum_field():
    assert _record()["MANDATORY_scorer_rule"]["deduplicate_by"] == \
        "audio_checksum_sha256"


# --- the executable scorer must dedupe, and must refuse a conflict ----------

import sys

sys.path.insert(0, str(ROOT / "scripts"))
from build_eleven_language_scoring_manifest import (  # noqa: E402
    ScoringManifestRefusal, collect_rows, primary_surfaces)

import pytest  # noqa: E402


def _surface(name, language="gbaya"):
    return {"surface": name, "manifest": f"{name}/manifest.jsonl",
            "language": language}


def _row(checksum, text, path="s3://medzen-speech/x.wav"):
    return {"audio_checksum_sha256": checksum, "text_normalized": text,
            "audio_filepath": path}


def test_duplicate_audio_with_the_same_reference_is_deduplicated():
    # the real shape of eval/gbaya/asr/soreva-v1: 101 rows, 100 clips
    rows = [_row("a" * 64, "gee-mɔ"), _row("a" * 64, "gee-mɔ"),
            _row("b" * 64, "ninꞌam nɛ́ paul")]
    out = collect_rows([_surface("eval/gbaya/asr/soreva-v1")],
                       lambda _key: rows)
    assert len(out) == 2
    assert {r["audio_checksum_sha256"] for r in out} == {"a" * 64, "b" * 64}


def test_duplicate_audio_with_conflicting_references_REFUSES():
    # the actual defect: one clip, two different references
    rows = [_row("a" * 64, "gee-mɔ"), _row("a" * 64, "ninꞌam nɛ́ paul")]
    with pytest.raises(ScoringManifestRefusal, match="two different references"):
        collect_rows([_surface("eval/gbaya/asr/soreva-v1")], lambda _key: rows)


def test_the_same_clip_across_two_surfaces_is_counted_once():
    def fetch(key):
        return [_row("c" * 64, "shared clip")]
    out = collect_rows([_surface("eval/lingala/asr/fleurs-v1", "lingala"),
                        _surface("eval/lingala/asr/soreva-v1", "lingala")],
                       fetch)
    assert len(out) == 1


def test_reference_whitespace_differences_are_not_a_conflict():
    rows = [_row("d" * 64, "gee  mɔ"), _row("d" * 64, " gee mɔ ")]
    assert len(collect_rows([_surface("eval/gbaya/asr/soreva-v1")],
                            lambda _key: rows)) == 1


def test_only_primary_surfaces_are_read_never_partitions_or_duplicates():
    value = _record()
    names = {s["surface"] for s in primary_surfaces(value)}
    for language in value["per_language"].values():
        for bucket in ("partitions_of_a_primary_never_pool",
                       "identical_audio_duplicates_do_not_score_both",
                       "diagnostic_only"):
            assert not (names & {e["surface"] for e in language[bucket]})
