"""The machine-derived Arm-2 exposure index (Codex/owner design re-review, rev
003) must be PROVABLY derived from the committed source records, capture full
per-row identities for the in-repo used surfaces, and provide working
intersection tests that a future held-out nomination / confirmation split will
have to pass. These tests fail if the committed index is hand-edited or stale,
if a known subset relationship breaks, or if the disjointness harness stops
catching an overlapping row."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_arm2_exposure_index as ix  # noqa: E402

INDEX = ROOT / "platform/manifests/B5-UNIVERSAL-ARM2-EXPOSURE-INDEX-2026-001.json"


def test_committed_index_is_machine_derived_and_current():
    # a fresh build must byte-equal the committed file (never hand-edited)
    assert ix.main(["--check"]) == 0


def test_index_captures_the_known_surfaces_with_full_identities():
    d = json.loads(INDEX.read_bytes())
    by = {s["surface"]: s for s in d["surfaces"]}
    assert by["arm1-dev-selection"]["row_count"] == 420
    assert by["arm1-dev-selection"]["unique_checksums"] == 420
    assert by["arm1-lingala-sentinel-386"]["row_count"] == 386
    assert by["dev-sentinel-lingala-60"]["row_count"] == 60
    assert by["dev-sentinel-swahili-60"]["row_count"] == 60
    assert d["identity_key"] == "audio_checksum_sha256"
    assert d["candidate_exposed_union"]["unique_checksums"] == 746
    assert {"CANDIDATE_EXPOSED","BASE_EXPOSED","TRAINING_EXPOSED","SEALED","BASE_BLIND_CANDIDATE_ELIGIBLE"} == set(d["exposure_classes"])
    assert "phase_A_nomination" in d["phase_eligibility"]
    # rev 006: EVERY S3-pinned exposure source is classified + pinned
    ps = d["pinned_sources"]
    classes = {x["class"] for x in ps}
    assert {"BASE_EXPOSED", "SEALED", "TRAINING_EXPOSED"} <= classes
    assert "BASE_BLIND_CANDIDATE_ELIGIBLE" in classes  # pidgin av-heldout dev
    # every pinned source carries a class + an identity (sha/version or raw sha)
    for x in ps:
        assert x.get("class")
        assert x.get("sha256") or x.get("s3_version_id") or \
            x.get("complete_raw_sha256") or x.get("source_record")


def test_sentinels_are_subsets_of_the_dev_selection_from_source():
    ds = json.loads((ROOT / "platform/manifests/"
                     "B5-UNIVERSAL-ARM1-DEV-SELECTION-2026-001.json").read_bytes())
    by_lang = {}
    for r in ds["rows"]:
        by_lang.setdefault(r["language"], set()).add(r["audio_checksum_sha256"])
    for lang in ("lingala", "swahili"):
        cks = set()
        for line in (ROOT / f"platform/manifests/dev-sentinels/{lang}.jsonl"
                     ).read_text().splitlines():
            if line.strip():
                cks.add(json.loads(line)["audio_checksum_sha256"])
        assert cks <= by_lang[lang], f"{lang} sentinel not a subset of selection"
    # the 60-row lingala sentinel is inside the 386-row lingala sentinel
    ls = json.loads((ROOT / "platform/manifests/"
                     "B5-ARM1-LINGALA-SENTINEL-2026-001.json").read_bytes())
    ls_cks = {r["audio_checksum_sha256"] for r in ls["rows"]}
    lingala60 = set()
    for line in (ROOT / "platform/manifests/dev-sentinels/lingala.jsonl"
                 ).read_text().splitlines():
        if line.strip():
            lingala60.add(json.loads(line)["audio_checksum_sha256"])
    assert lingala60 <= ls_cks


def test_disjointness_harness_rejects_overlap_and_accepts_novel_rows():
    union = ix.used_union_checksums()
    assert len(union) == 746
    a_used = next(iter(union))
    ok, overlap = ix.split_is_disjoint([a_used, "0" * 64])
    assert ok is False and a_used in overlap, "an overlapping row must be caught"
    ok, overlap = ix.split_is_disjoint(["f" * 64, "e" * 64])
    assert ok is True and overlap == [], "genuinely novel rows must pass"
