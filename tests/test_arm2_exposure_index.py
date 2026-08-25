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
    # every pinned source carries a class + a REAL CONTENT identity — a bare
    # source_record path is NOT an identity (owner blocker 1)
    def _hex64(v):
        return isinstance(v, str) and len(v) == 64 and \
            all(c in "0123456789abcdef" for c in v)
    for x in ps:
        assert x.get("class")
        has_identity = _hex64(x.get("sha256")) or _hex64(x.get("complete_raw_sha256")) \
            or (isinstance(x.get("s3_version_id"), str) and x["s3_version_id"])
        assert has_identity, f"pinned source lacks a real identity: {x}"


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


def test_kinyarwanda_eval_identities_are_present_and_exact():
    """Owner blocker 1: the three Kinyarwanda cv17 eval sources must be pinned
    with their exact identities read from the authoritative records."""
    import json
    d = json.loads(INDEX.read_bytes())
    kw = {x["pool"]: x for x in d["pinned_sources"]
          if x.get("language") == "kinyarwanda"}
    dev = kw["dev-selection"]
    assert dev["class"] == "CANDIDATE_EXPOSED" and dev["rows"] == 1642
    assert dev["sha256"] == "9bad83475139cf399634edcf26e4a5455a3c9cbeb5ce2f515364c9190a5c0569"
    assert dev["s3_version_id"] == "_ZIjFLQ51QiCNgo3PGzF_tQ3Fwyd52Q6"
    us = kw["universal-sealed"]
    assert us["class"] == "SEALED" and us["rows"] == 1642
    assert us["sha256"] == "5ca3ef62e6f7447c5b8a2479e51b1f245ed792795240ac46022de4d6391805df"
    q = kw["cv17-test-v1-sealed-QUARANTINED"]
    assert q["class"] == "SEALED" and q["rows"] == 3373
    assert q["sha256"] == "f6f50bcfc473a12026efefe94b1fbbebcf42e6006623860c18be21e6583e70b9"
    assert q["s3_version_id"] == "Z5afijMNC6DAL2kCp2g5tWSlTcY_8BrJ"


def test_gb3_training_has_a_real_content_digest_not_a_path():
    """Owner blocker 1: the GB3 Kinyarwanda training entry must carry a real
    content digest, not a bare file path."""
    import json
    d = json.loads(INDEX.read_bytes())
    gb3 = next(x for x in d["pinned_sources"]
               if x.get("dataset") == "gb3-kinyarwanda")
    assert gb3["complete_raw_sha256"] == (
        "25427f567756ef28c2e8d8dc0e3c485a8a385f54e4c714177bffbd86b871166a")


def test_no_pinned_source_relies_on_a_path_only_identity():
    """Adversarial (owner blocker 1): assert NO pinned source would pass on a
    path alone — every one has a real hash or S3 VersionId."""
    import json
    d = json.loads(INDEX.read_bytes())
    for x in d["pinned_sources"]:
        real = (isinstance(x.get("sha256"), str) and len(x["sha256"]) == 64) \
            or (isinstance(x.get("complete_raw_sha256"), str)
                and len(x["complete_raw_sha256"]) == 64) \
            or bool(x.get("s3_version_id"))
        assert real, f"path-only identity: {x.get('pool') or x.get('dataset')}"
