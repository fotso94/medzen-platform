"""Arm-2 Phase-A nomination-split MINTING harness — deterministic mint + the
adversarial leakage proofs, corrected per Codex rounds 34 AND 35. All
host-safe and OFFLINE: the pure core is driven by committed fixtures, AWS is
proven IMPOSSIBLE in the test path, and every reproduced bypass from both
rounds is locked as a regression:

round 34: unverified bytes accepted as FROZEN; candidate leak passing the
verifier; cross-language duplicates accepted.
round 35: empty training evidence as complete proof; sealed reclassification
through a caller-supplied index; incomplete exclusion evidence minting FROZEN;
forged declared counts/status/record passing the verifier; sealed manifests
being read at all; substring role authorization.
"""
from __future__ import annotations

import builtins
import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "scripts"))

import mint_arm2_nomination_split as mint_mod  # noqa: E402
from mint_arm2_nomination_split import (  # noqa: E402
    NOMINATION_LANGUAGES, TRAINING_INDEX_RECORD, TRAINING_SOURCE_RECORDS,
    LiveMintForbidden, MintRefusal, candidate_pinned_pool_keys,
    committed_training_source_digests, load_index, live_mint, main,
    mint_phase_a_split, nomination_pool_keys, read_pool_keys, sealed_pool_keys,
    validate_caller_identity, validate_sealed_authorities,
    validate_training_index, veto_surface_checksums, verify_frozen_manifest)
from build_arm2_exposure_index import used_union_checksums  # noqa: E402

_INDEX = load_index()
_PACKET_PATH = (_REPO / "platform/decisions/"
                "B5-UNIVERSAL-ARM2-NOMINATION-LIVE-MINT-PACKET-2026-001.json")
_HEX = set("0123456789abcdef")


def _ck(tag: str, i: int) -> str:
    return hashlib.sha256(f"{tag}:{i}".encode()).hexdigest()


def _agg(checksums):
    return mint_mod._agg(checksums)


def _clean_pool_identities(n: int = 5) -> dict[str, list[str]]:
    """Identities for EXACTLY the reviewed read set (nomination + candidate
    pools — sealed pools are NEVER read). Synthetic sha256(tag:i) values never
    collide with the real candidate union or veto surfaces."""
    pools: dict[str, list[str]] = {}
    for keys in nomination_pool_keys(_INDEX).values():
        for k in keys:
            pools[k] = [_ck(k, i) for i in range(n)]
    for k in candidate_pinned_pool_keys(_INDEX):
        pools[k] = [_ck(k, i) for i in range(n)]
    return pools


def _training_artifact(n: int = 7) -> dict:
    """A STRUCTURED training identity index citing the real committed adoption
    digests (the structure/self-consistency contract) with synthetic
    identities."""
    identities = sorted(_ck("training-corpus", i) for i in range(n))
    unique, aggregate = _agg(identities)
    return {
        "record": TRAINING_INDEX_RECORD,
        "identity_key": "audio_checksum_sha256",
        "source_manifests": [
            {"dataset": dataset, "source_record": TRAINING_SOURCE_RECORDS[dataset],
             "complete_raw_sha256": digest}
            for dataset, digest in sorted(
                committed_training_source_digests().items())],
        "producer": {"script": "scripts/build_arm2_training_identity_index.py"},
        "row_count": n,
        "unique_count": unique,
        "aggregate_sha256": aggregate,
        "identities": identities,
    }


def _sealed_authorities(index=None) -> list[dict]:
    """Identity-only sealed exclusion authorities covering EVERY sealed pool in
    the index, each matching its pin exactly (rows included)."""
    index = index if index is not None else _INDEX
    pins = {s["key"]: s for s in index["pinned_sources"]
            if s.get("class") == "SEALED" and s.get("key")
            and s.get("sha256") and s.get("s3_version_id")}
    out = []
    for key in sorted(pins):
        pin = pins[key]
        rows = int(pin.get("rows") or 1)
        identities = sorted(_ck(f"sealed:{key}", i) for i in range(rows))
        _, aggregate = _agg(identities)
        out.append({"key": key, "class": "SEALED", "rows": pin.get("rows"),
                    "sha256": pin["sha256"],
                    "s3_version_id": pin["s3_version_id"],
                    "identities": identities,
                    "aggregate_sha256": aggregate,
                    "governance": {"adjudication":
                                   "TEST-FIXTURE-ADJUDICATION-2026-001"}})
    return out


def _first_pool(lang: str) -> str:
    return nomination_pool_keys(_INDEX)[lang][0]


def _nonveto_candidate() -> str:
    return sorted(used_union_checksums() - veto_surface_checksums())[0]


def _full_mint(pools=None, train=None, sealed=None, status="MINTED_OFFLINE_FIXTURE"):
    return mint_phase_a_split(
        _INDEX, pools if pools is not None else _clean_pool_identities(),
        training_index=train if train is not None else _training_artifact(),
        sealed_authorities=sealed if sealed is not None else _sealed_authorities(),
        status=status)


# --------------------------------------------------------------------------
# happy path — full artifacts => every overlap a RECOMPUTED zero
# --------------------------------------------------------------------------

def test_clean_mint_with_full_artifacts_is_nonempty_disjoint_and_verifies():
    pools = _clean_pool_identities()
    train = _training_artifact()
    sealed = _sealed_authorities()
    manifest = mint_phase_a_split(_INDEX, pools, training_index=train,
                                  sealed_authorities=sealed)
    for lang in NOMINATION_LANGUAGES:
        assert manifest["split"][lang], f"{lang} split is empty"
    assert manifest["aggregate_overlap_counts"] == {
        "candidate_exposed": 0, "sealed": 0, "veto_surface": 0,
        "training_exposed": 0}
    assert manifest["sealed_pools_never_read"] == sealed_pool_keys(_INDEX)
    assert verify_frozen_manifest(manifest, _INDEX, pools,
                                  training_index=train,
                                  sealed_authorities=sealed) == []


def test_mint_is_deterministic():
    a = _full_mint()
    b = _full_mint()
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_frozen_manifest_carries_identities_only_never_text_or_audio():
    manifest = _full_mint()
    forbidden = {"text", "text_normalized", "transcript", "audio",
                 "audio_filepath", "waveform", "wav"}

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                assert k not in forbidden, f"manifest leaks a {k!r} field"
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(manifest)
    for rows in manifest["split"].values():
        for c in rows:
            assert len(c) == 64 and set(c) <= _HEX


# --------------------------------------------------------------------------
# round 35 finding 1: training evidence must be a structured, NONEMPTY,
# hash-bound artifact — an empty or arbitrary collection refuses
# --------------------------------------------------------------------------

def test_overlaps_without_artifacts_are_unverified_never_zero():
    m = mint_phase_a_split(_INDEX, _clean_pool_identities())
    assert m["aggregate_overlap_counts"]["training_exposed"] == "UNVERIFIED"
    assert m["aggregate_overlap_counts"]["sealed"] == "UNVERIFIED"
    assert m["exclusion_provenance"]["training_exposed_unique"] == "UNVERIFIED"
    assert m["exclusion_provenance"]["sealed_unique"] == "UNVERIFIED"


def test_frozen_refuses_without_training_index_or_sealed_authorities():
    with pytest.raises(MintRefusal, match="training identity index"):
        mint_phase_a_split(_INDEX, _clean_pool_identities(), status="FROZEN",
                           sealed_authorities=_sealed_authorities())
    with pytest.raises(MintRefusal, match="sealed identity-only"):
        mint_phase_a_split(_INDEX, _clean_pool_identities(), status="FROZEN",
                           training_index=_training_artifact())


def test_an_empty_training_index_refuses_even_as_frozen():
    """Round-35 reproduction (1): training_identities=[] used to mint FROZEN
    with training overlap 0 — empty evidence must refuse."""
    empty = _training_artifact()
    empty["identities"] = []
    empty["unique_count"] = 0
    empty["row_count"] = 0
    empty["aggregate_sha256"] = _agg([])[1]
    with pytest.raises(MintRefusal, match="NO identities"):
        _full_mint(train=empty, status="FROZEN")


def test_a_bare_collection_is_not_a_training_index():
    with pytest.raises(MintRefusal, match="STRUCTURED artifact"):
        _full_mint(train=[_ck("t", 0)])
    with pytest.raises(MintRefusal, match="STRUCTURED artifact"):
        validate_training_index({_ck("t", 0)})


@pytest.mark.parametrize("mutate,pattern", [
    (lambda a: a.update(record="SOMETHING-ELSE"), "record"),
    (lambda a: a["source_manifests"][0].update(complete_raw_sha256="0" * 64),
     "not derived from the pinned corpus"),
    (lambda a: a["source_manifests"].pop(0), "required pinned corpora"),
    (lambda a: a["source_manifests"][0].update(source_record="x.json"),
     "committed record"),
    (lambda a: a.update(aggregate_sha256="0" * 64), "does not reproduce"),
    (lambda a: a.update(unique_count=999), "unique_count"),
    (lambda a: a.update(row_count=0), "row_count"),
    (lambda a: a.pop("producer"), "producer receipt"),
    (lambda a: a["identities"].append("NOT-HEX"), "malformed"),
    (lambda a: a["identities"].append(a["identities"][0]), "duplicate"),
])
def test_training_index_tampering_refuses(mutate, pattern):
    artifact = _training_artifact()
    mutate(artifact)
    with pytest.raises(MintRefusal, match=pattern):
        validate_training_index(artifact)


def test_a_training_row_in_a_nomination_pool_is_anti_joined_out():
    pools = _clean_pool_identities()
    train = _training_artifact()
    leaked = train["identities"][0]
    pools[_first_pool("english")].append(leaked)
    manifest = _full_mint(pools=pools, train=train)
    assert leaked not in manifest["split"]["english"]
    assert manifest["per_language"]["english"]["removed_training"] == 1
    assert manifest["aggregate_overlap_counts"]["training_exposed"] == 0


# --------------------------------------------------------------------------
# round 35 findings 3/6: completeness is mandatory; sealed pools are never read
# --------------------------------------------------------------------------

def test_missing_candidate_pool_refuses_incomplete():
    """Round-35 reproduction (3): omitting off-repo exclusion sources used to
    mint FROZEN with sealed/candidate overlap 0."""
    pools = _clean_pool_identities()
    pools.pop(candidate_pinned_pool_keys(_INDEX)[0])
    with pytest.raises(MintRefusal, match="INCOMPLETE"):
        _full_mint(pools=pools, status="FROZEN")


def test_missing_nomination_pool_refuses_incomplete():
    pools = _clean_pool_identities()
    pools.pop(_first_pool("pidgin"))
    with pytest.raises(MintRefusal, match="INCOMPLETE"):
        _full_mint(pools=pools)


def test_extra_unreviewed_pool_refuses():
    pools = _clean_pool_identities()
    pools["eval/unreviewed/manifest.jsonl"] = [_ck("x", 0)]
    with pytest.raises(MintRefusal, match="NOT in the reviewed read set"):
        _full_mint(pools=pools)


def test_sealed_pool_identities_are_not_accepted_as_pool_inputs():
    """Sealed manifests are NEVER mint inputs — supplying one as a pool is an
    unreviewed-extra refusal, not a silent acceptance."""
    pools = _clean_pool_identities()
    pools[sealed_pool_keys(_INDEX)[0]] = [_ck("s", 0)]
    with pytest.raises(MintRefusal, match="NOT in the reviewed read set"):
        _full_mint(pools=pools)


def test_incomplete_sealed_authorities_refuse():
    sealed = _sealed_authorities()
    sealed.pop()
    with pytest.raises(MintRefusal, match="INCOMPLETE"):
        _full_mint(sealed=sealed)


@pytest.mark.parametrize("mutate,pattern", [
    (lambda a: a[0].update(rows=(a[0]["rows"] or 0) + 1), "the index pins"),
    (lambda a: a[0].update(sha256="0" * 64), "the index pins"),
    (lambda a: a[0].update(s3_version_id="v-FORGED"), "the index pins"),
    (lambda a: a[0].pop("governance"), "governance adjudication"),
    (lambda a: a[0].update(governance={"adjudication": ""}),
     "governance adjudication"),
    (lambda a: a[0].update(aggregate_sha256="0" * 64), "does not reproduce"),
    (lambda a: a[0].update(identities=a[0]["identities"][:-1]),
     "the index pins rows"),
    (lambda a: a[0].update(key="eval/not/a/sealed/pool.jsonl"),
     "does not correspond"),
])
def test_sealed_authority_tampering_refuses(mutate, pattern):
    sealed = _sealed_authorities()
    mutate(sealed)
    with pytest.raises(MintRefusal, match=pattern):
        validate_sealed_authorities(sealed, _INDEX)


def test_a_sealed_authority_identity_in_a_nomination_pool_is_filtered_out():
    pools = _clean_pool_identities()
    sealed = _sealed_authorities()
    leaked = sealed[0]["identities"][0]
    pools[_first_pool("swahili")].append(leaked)
    manifest = _full_mint(pools=pools, sealed=sealed)
    assert leaked not in manifest["split"]["swahili"]
    assert manifest["per_language"]["swahili"]["removed_sealed"] >= 1
    assert manifest["aggregate_overlap_counts"]["sealed"] == 0


# --------------------------------------------------------------------------
# adversarial leakage + duplicate refusals (rounds 34/35)
# --------------------------------------------------------------------------

def test_a_candidate_exposed_row_in_a_nomination_pool_is_filtered_out():
    pools = _clean_pool_identities()
    leaked = _nonveto_candidate()
    pools[_first_pool("english")].append(leaked)
    manifest = _full_mint(pools=pools)
    assert leaked not in manifest["split"]["english"]
    assert manifest["per_language"]["english"]["removed_candidate"] >= 1
    assert manifest["aggregate_overlap_counts"]["candidate_exposed"] == 0


def test_a_veto_surface_row_in_a_nomination_pool_refuses():
    pools = _clean_pool_identities()
    pools[_first_pool("french")].append(next(iter(veto_surface_checksums())))
    with pytest.raises(MintRefusal, match="veto"):
        _full_mint(pools=pools)


def test_an_empty_language_split_refuses():
    pools = _clean_pool_identities()
    for key in nomination_pool_keys(_INDEX)["english"]:
        pools[key] = [_nonveto_candidate()]
    with pytest.raises(MintRefusal, match="EMPTY"):
        _full_mint(pools=pools)


def test_a_malformed_identity_refuses():
    pools = _clean_pool_identities()
    pools[_first_pool("english")].append("NOT-A-SHA")
    with pytest.raises(MintRefusal, match="malformed"):
        _full_mint(pools=pools)


def test_within_language_duplicate_identities_refuse():
    pools = _clean_pool_identities()
    key = _first_pool("english")
    pools[key] = pools[key] + [pools[key][0]]
    with pytest.raises(MintRefusal, match="duplicate identities within"):
        _full_mint(pools=pools)


def test_duplicate_identities_in_a_candidate_pool_refuse():
    pools = _clean_pool_identities()
    key = candidate_pinned_pool_keys(_INDEX)[0]
    pools[key] = pools[key] + [pools[key][0]]
    with pytest.raises(MintRefusal, match="candidate pool.*duplicate"):
        _full_mint(pools=pools)


def test_cross_language_duplicate_identities_refuse():
    pools = _clean_pool_identities()
    dup = _ck("shared-across-langs", 0)
    pools[_first_pool("english")].append(dup)
    pools[_first_pool("french")].append(dup)
    with pytest.raises(MintRefusal, match="MORE THAN ONE language"):
        _full_mint(pools=pools)


# --------------------------------------------------------------------------
# round 35 finding 4: the verifier regenerates the ENTIRE canonical manifest
# --------------------------------------------------------------------------

def _verify(manifest, pools, train, sealed):
    return verify_frozen_manifest(manifest, _INDEX, pools,
                                  training_index=train,
                                  sealed_authorities=sealed)


@pytest.mark.parametrize("tamper", [
    lambda m: m["aggregate_overlap_counts"].update(candidate_exposed=99),
    lambda m: m["aggregate_overlap_counts"].update(sealed=1),
    lambda m: m.update(status="FORGED-STATUS"),
    lambda m: m.update(record="FORGED-RECORD"),
    lambda m: m.update(protocol="FORGED-PROTOCOL"),
    lambda m: m["pool_pins"].pop(),
    lambda m: m["pool_pins"][0].update(s3_version_id="v-FORGED"),
    lambda m: m["per_language"]["english"].update(eligible=999),
    lambda m: m["per_language"]["english"].update(removed_candidate=0xBAD),
    lambda m: m["exclusion_provenance"].update(sealed_unique=0),
    lambda m: m.update(injected_extra_key=True),
    lambda m: m["split_identity"].update(unique=m["split_identity"]["unique"] + 1),
    lambda m: m.pop("sealed_pools_never_read"),
])
def test_verifier_catches_any_forged_field(tamper):
    """Round-35 reproduction (4): forged declared counts/status/record/pins/
    provenance used to pass — the canonical regeneration catches every one."""
    pools = _clean_pool_identities()
    train = _training_artifact()
    sealed = _sealed_authorities()
    manifest = mint_phase_a_split(_INDEX, pools, training_index=train,
                                  sealed_authorities=sealed)
    tamper(manifest)
    assert _verify(manifest, pools, train, sealed) != []


def test_verifier_catches_a_candidate_leak_even_with_a_fixed_aggregate():
    pools = _clean_pool_identities()
    train = _training_artifact()
    sealed = _sealed_authorities()
    manifest = mint_phase_a_split(_INDEX, pools, training_index=train,
                                  sealed_authorities=sealed)
    manifest["split"]["english"].append(_nonveto_candidate())
    manifest["per_language"]["english"]["split_aggregate_sha256"] = \
        _agg(manifest["split"]["english"])[1]
    assert _verify(manifest, pools, train, sealed) != []


def test_verifier_catches_a_dropped_row():
    pools = _clean_pool_identities()
    train = _training_artifact()
    sealed = _sealed_authorities()
    manifest = mint_phase_a_split(_INDEX, pools, training_index=train,
                                  sealed_authorities=sealed)
    manifest["split"]["english"].pop()
    manifest["per_language"]["english"]["split_aggregate_sha256"] = \
        _agg(manifest["split"]["english"])[1]
    assert _verify(manifest, pools, train, sealed) != []


def test_verifier_fails_when_inputs_cannot_regenerate():
    """Incomplete verification inputs are a FAILURE, never a pass (round-35
    reproduction 3, verifier half)."""
    pools = _clean_pool_identities()
    train = _training_artifact()
    sealed = _sealed_authorities()
    manifest = mint_phase_a_split(_INDEX, pools, training_index=train,
                                  sealed_authorities=sealed)
    incomplete = dict(pools)
    incomplete.pop(candidate_pinned_pool_keys(_INDEX)[0])
    failures = verify_frozen_manifest(manifest, _INDEX, incomplete,
                                      training_index=train,
                                      sealed_authorities=sealed)
    assert failures and "cannot be regenerated" in failures[0]


def test_verifier_rejects_a_numeric_claim_without_the_artifacts():
    """A manifest claiming numeric sealed/training overlap can never verify
    without the artifacts that would prove it."""
    pools = _clean_pool_identities()
    manifest = _full_mint(pools=pools)
    failures = verify_frozen_manifest(manifest, _INDEX, pools)
    assert failures != []


# --------------------------------------------------------------------------
# round 35 findings 2/7 + round 34 finding 2: the live trusted path
# --------------------------------------------------------------------------

_SYN_KMS = "arn:aws:kms:eu-central-1:558069890522:key/synthetic-cmk"
_SYN_ACCOUNT = "558069890522"
_SYN_ROLE = "medzen-arm2-nomination-mint-role"


def _synthetic_world(n: int = 3):
    """A synthetic exposure index + packet + object store whose pins are
    COMPUTED from the fixture bytes: the index is bound by canonical bytes, the
    packet pins every read-set object with FULL fields, and sealed pools exist
    only as exclusion-authority references (never fetched)."""
    sources, store = [], {}

    def add(key, cls, role, language, rows_n):
        rows = [{"audio_checksum_sha256": _ck(key, i), "language": language}
                for i in range(rows_n)]
        body = "".join(json.dumps(r) + "\n" for r in rows).encode()
        sources.append({"key": key, "class": cls, "role": role,
                        "language": language, "rows": rows_n,
                        "sha256": hashlib.sha256(body).hexdigest(),
                        "s3_version_id": f"v-{key}"})
        store[key] = body

    for lang in NOMINATION_LANGUAGES:
        add(f"eval/{lang}/dev/manifest.jsonl", "BASE_EXPOSED",
            "eval_dev_half", lang, n)
    add("eval/english/sealed/manifest.jsonl", "SEALED",
        "sealed_holdout_half", "english", n)
    add("eval/kw/candidate/manifest.jsonl", "CANDIDATE_EXPOSED",
        "kinyarwanda_eval", "kinyarwanda", n)
    index = {"pinned_sources": sources}
    index_bytes = json.dumps(index, sort_keys=True).encode()
    read_keys = {s["key"] for s in sources if s["class"] != "SEALED"}
    packet = {
        "aws": {"kms_key": _SYN_KMS, "account": _SYN_ACCOUNT},
        "minimal_read_role": {"role_name": _SYN_ROLE},
        "exposure_index_sha256": hashlib.sha256(index_bytes).hexdigest(),
        "pinned_objects": [
            {"key": s["key"], "class": s["class"], "role": s["role"],
             "language": s["language"], "rows": s["rows"],
             "sha256": s["sha256"], "s3_version_id": s["s3_version_id"]}
            for s in sources if s["key"] in read_keys],
    }
    calls = []

    def reader(key, s3_version_id):
        calls.append(key)
        return {"body": store[key], "version_id": f"v-{key}",
                "kms_key_arn": _SYN_KMS}

    sealed = []
    for s in sources:
        if s["class"] != "SEALED":
            continue
        identities = sorted(json.loads(line)["audio_checksum_sha256"]
                            for line in store[s["key"]].decode().splitlines())
        sealed.append({"key": s["key"], "class": "SEALED", "rows": s["rows"],
                       "sha256": s["sha256"],
                       "s3_version_id": s["s3_version_id"],
                       "identities": identities,
                       "aggregate_sha256": _agg(identities)[1],
                       "governance": {"adjudication": "SYN-ADJUDICATION"}})
    caller = {"Account": _SYN_ACCOUNT,
              "Arn": f"arn:aws:sts::{_SYN_ACCOUNT}:assumed-role/{_SYN_ROLE}/run"}
    return index, index_bytes, packet, store, reader, sealed, caller, calls


def _live(index_bytes, packet, reader, sealed, caller, train=None):
    return live_mint(packet, index_bytes=index_bytes, s3_reader=reader,
                     caller_identity=caller,
                     training_index=train if train is not None
                     else _training_artifact(),
                     sealed_authorities=sealed)


def test_live_mint_happy_path_is_frozen_and_never_reads_sealed():
    _, index_bytes, packet, _, reader, sealed, caller, calls = _synthetic_world()
    manifest = _live(index_bytes, packet, reader, sealed, caller)
    assert manifest["status"] == "FROZEN"
    assert manifest["aggregate_overlap_counts"] == {
        "candidate_exposed": 0, "sealed": 0, "veto_surface": 0,
        "training_exposed": 0}
    # round 35 finding 6: the reader is NEVER asked for a sealed key
    assert not [k for k in calls if "sealed" in k]


def test_live_mint_refuses_a_tampered_or_reclassified_index():
    """Round-35 reproduction (2): reclassifying a sealed pool as
    BASE_EXPOSED/eval_dev_half in the caller-supplied index used to be
    accepted — the canonical-bytes binding refuses it."""
    index, _, packet, _, reader, sealed, caller, _ = _synthetic_world()
    tampered = copy.deepcopy(index)
    for s in tampered["pinned_sources"]:
        if s["class"] == "SEALED":
            s["class"] = "BASE_EXPOSED"
            s["role"] = "eval_dev_half"
    tampered_bytes = json.dumps(tampered, sort_keys=True).encode()
    with pytest.raises(MintRefusal, match="tampered or unreviewed index"):
        _live(tampered_bytes, packet, reader, sealed, caller)


def test_live_mint_refuses_a_packet_without_the_index_binding():
    _, index_bytes, packet, _, reader, sealed, caller, _ = _synthetic_world()
    packet.pop("exposure_index_sha256")
    with pytest.raises(MintRefusal, match="exposure_index_sha256"):
        _live(index_bytes, packet, reader, sealed, caller)


@pytest.mark.parametrize("field,value", [
    ("class", "SEALED"), ("role", "sealed_holdout_half"),
    ("language", "french"), ("rows", 999),
    ("sha256", "0" * 64), ("s3_version_id", "v-FORGED"),
])
def test_live_mint_refuses_any_packet_pin_field_disagreement(field, value):
    """Round-35 finding 2 (second half): EVERY pin field is compared — class,
    role, language, rows, sha256 AND VersionId."""
    _, index_bytes, packet, _, reader, sealed, caller, _ = _synthetic_world()
    packet["pinned_objects"][0][field] = value
    with pytest.raises(MintRefusal, match="disagrees"):
        _live(index_bytes, packet, reader, sealed, caller)


def test_live_mint_refuses_a_packet_that_pins_a_sealed_fetch():
    _, index_bytes, packet, _, reader, sealed, caller, _ = _synthetic_world()
    packet["pinned_objects"].append(
        {"key": "eval/english/sealed/manifest.jsonl", "sha256": "0" * 64,
         "s3_version_id": "v"})
    with pytest.raises(MintRefusal, match="NEVER reads sealed"):
        _live(index_bytes, packet, reader, sealed, caller)


@pytest.mark.parametrize("caller,pattern", [
    (None, "no STS caller identity"),
    ({"Account": "999999999999",
      "Arn": f"arn:aws:sts::999999999999:assumed-role/{_SYN_ROLE}/run"},
     "account"),
    ({"Account": _SYN_ACCOUNT,
      "Arn": f"arn:aws:sts::{_SYN_ACCOUNT}:assumed-role/other-role/run"},
     "assumed-role"),
    ({"Account": _SYN_ACCOUNT,
      "Arn": f"arn:aws:iam::{_SYN_ACCOUNT}:user/{_SYN_ROLE}"},
     "assumed-role"),
    ({"Account": _SYN_ACCOUNT,
      "Arn": f"arn:aws:sts::{_SYN_ACCOUNT}:assumed-role/{_SYN_ROLE}"},
     "assumed-role"),
])
def test_live_mint_asserts_the_exact_caller_identity(caller, pattern):
    """Round-35 finding 7: the trusted path itself asserts the EXACT account
    and role — a same-named role in another account, a user ARN, or a
    truncated ARN all refuse."""
    _, index_bytes, packet, _, reader, sealed, _, _ = _synthetic_world()
    with pytest.raises(MintRefusal, match=pattern):
        _live(index_bytes, packet, reader, sealed, caller)


def test_validate_caller_identity_rejects_substring_tricks():
    """The round-35 substring bypass: an ARN merely CONTAINING
    ':assumed-role/<name>/' (e.g. another account, or a session name crafted
    to embed it) must refuse under exact parsing."""
    with pytest.raises(MintRefusal):
        validate_caller_identity(
            {"Account": _SYN_ACCOUNT,
             "Arn": (f"arn:aws:sts::999999999999:assumed-role/{_SYN_ROLE}/"
                     "run")}, account=_SYN_ACCOUNT, role_name=_SYN_ROLE)
    validate_caller_identity(
        {"Account": _SYN_ACCOUNT,
         "Arn": f"arn:aws:sts::{_SYN_ACCOUNT}:assumed-role/{_SYN_ROLE}/x"},
        account=_SYN_ACCOUNT, role_name=_SYN_ROLE)


def test_live_mint_refuses_fabricated_bytes():
    _, index_bytes, packet, _, _, sealed, caller, _ = _synthetic_world()

    def evil(key, s3_version_id):
        ck = hashlib.sha256(f"fabricated:{key}".encode()).hexdigest()
        return {"body": json.dumps({"audio_checksum_sha256": ck}).encode()
                + b"\n", "version_id": f"v-{key}", "kms_key_arn": _SYN_KMS}

    with pytest.raises(MintRefusal, match="unverified bytes"):
        _live(index_bytes, packet, evil, sealed, caller)


def test_live_mint_refuses_wrong_version_kms_rows_language_and_bare_bytes():
    _, index_bytes, packet, store, _, sealed, caller, _ = _synthetic_world()

    def wrong_version(key, s3_version_id):
        return {"body": store[key], "version_id": "v-ELSE",
                "kms_key_arn": _SYN_KMS}

    with pytest.raises(MintRefusal, match="VersionId"):
        _live(index_bytes, packet, wrong_version, sealed, caller)

    def wrong_kms(key, s3_version_id):
        return {"body": store[key], "version_id": f"v-{key}",
                "kms_key_arn": "arn:aws:kms:eu-central-1:999:key/other"}

    with pytest.raises(MintRefusal, match="CMK"):
        _live(index_bytes, packet, wrong_kms, sealed, caller)

    with pytest.raises(MintRefusal, match="echoed"):
        _live(index_bytes, packet,
              lambda key, s3_version_id: store[key], sealed, caller)


def test_live_mint_refuses_without_reader_before_any_sdk():
    _, index_bytes, packet, _, _, sealed, caller, _ = _synthetic_world()
    with pytest.raises(LiveMintForbidden, match="reader"):
        live_mint(packet, index_bytes=index_bytes, s3_reader=None,
                  caller_identity=caller, training_index=_training_artifact(),
                  sealed_authorities=sealed)


# --------------------------------------------------------------------------
# AWS is IMPOSSIBLE in the test path
# --------------------------------------------------------------------------

def _forbid_aws(monkeypatch):
    real_import = builtins.__import__

    def no_aws(name, *a, **k):
        if name.split(".")[0] in ("boto3", "botocore"):
            raise AssertionError("AWS must be impossible in test mode")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_aws)


def test_pure_mint_and_live_paths_touch_no_aws_sdk(monkeypatch):
    _forbid_aws(monkeypatch)
    assert _full_mint()["aggregate_overlap_counts"]["sealed"] == 0
    _, index_bytes, packet, _, reader, sealed, caller, _ = _synthetic_world()
    frozen = _live(index_bytes, packet, reader, sealed, caller)
    assert frozen["status"] == "FROZEN"
    with pytest.raises(LiveMintForbidden):
        live_mint(packet, index_bytes=index_bytes, s3_reader=None,
                  caller_identity=caller, training_index=_training_artifact(),
                  sealed_authorities=sealed)


def test_no_token_authorization_exists():
    assert not hasattr(mint_mod, "LIVE_AUTHORIZATION_TOKEN")
    assert not hasattr(mint_mod, "LIVE_AUTHORIZATION_ENV")
    source = (_REPO / "scripts/mint_arm2_nomination_split.py").read_text()
    assert "OWNER-AUTHORIZED-ARM2-NOMINATION-LIVE-MINT" not in source


def test_cli_offline_mints_nothing():
    with pytest.raises(SystemExit, match="mints nothing"):
        main([])


def test_cli_live_fails_closed_without_aws_sdk(monkeypatch):
    _forbid_aws(monkeypatch)
    with pytest.raises((SystemExit, AssertionError)) as excinfo:
        main(["--live"])
    if isinstance(excinfo.value, SystemExit):
        assert "protected workflow" in str(excinfo.value)


# --------------------------------------------------------------------------
# packet / IAM / terraform / workflow governance
# --------------------------------------------------------------------------

def _packet() -> dict:
    return json.loads(_PACKET_PATH.read_bytes())


def test_packet_reads_only_nomination_and_candidate_pools():
    packet = _packet()
    assert packet["status"].startswith("PENDING")
    classes = {o["class"] for o in packet["pinned_objects"]}
    assert "SEALED" not in classes
    assert {o["key"] for o in packet["pinned_objects"]} == read_pool_keys(_INDEX)
    assert packet["reads_count"] == len(packet["pinned_objects"]) == 7
    assert packet["sealed_exclusion"]["mint_reads_sealed_bytes"] is False


def test_packet_pins_match_the_index_on_every_field():
    packet = _packet()
    pins = {s["key"]: s for s in _INDEX["pinned_sources"] if s.get("key")}
    for o in packet["pinned_objects"]:
        pin = pins[o["key"]]
        for field in ("class", "role", "language", "rows", "sha256",
                      "s3_version_id"):
            assert o[field] == pin[field], (o["key"], field)


def test_packet_binds_the_exposure_index_by_canonical_bytes():
    packet = _packet()
    actual = hashlib.sha256(
        (_REPO / "platform/manifests/"
         "B5-UNIVERSAL-ARM2-EXPOSURE-INDEX-2026-001.json").read_bytes()).hexdigest()
    assert packet["exposure_index_sha256"] == actual


def test_mint_policy_is_exact_version_pinned_and_deny_backed():
    packet = _packet()
    policy = packet["minimal_read_role"]["policy"]
    committed = json.loads(
        (_REPO / "platform/iam/medzen-arm2-nomination-mint-role.json").read_bytes())
    assert committed == policy
    pins = {o["key"]: o["s3_version_id"] for o in packet["pinned_objects"]}
    version_allows = [s for s in policy["Statement"]
                      if s["Effect"] == "Allow"
                      and s["Action"] == ["s3:GetObjectVersion"]]
    assert len(version_allows) == len(pins)
    for stmt in version_allows:
        key = stmt["Resource"][0].split("medzen-speech/", 1)[1]
        assert stmt["Condition"]["StringEquals"]["s3:VersionId"] == pins[key]
    sids = {s["Sid"] for s in policy["Statement"]}
    assert "DenyUnversionedReadsEverywhere" in sids
    assert "DenyVersionedReadsOutsideThePinnedObjects" in sids
    kms_allow = next(s for s in policy["Statement"]
                     if s["Effect"] == "Allow" and s["Action"] == ["kms:Decrypt"])
    assert kms_allow["Condition"]["StringEquals"]["kms:ViaService"] == \
        "s3.eu-central-1.amazonaws.com"


def test_producer_policy_can_never_touch_eval_or_sealed():
    policy = json.loads(
        (_REPO / "platform/iam/medzen-arm2-training-index-role.json").read_bytes())
    denies = [s for s in policy["Statement"] if s["Effect"] == "Deny"]
    assert any("eval/*" in json.dumps(s.get("Resource", "")) for s in denies)
    assert any("NotResource" in s for s in denies)
    allows = [s for s in policy["Statement"] if s["Effect"] == "Allow"]
    assert all("eval" not in json.dumps(s.get("Resource", "")) for s in allows)
    assert not any(s.get("Action") == ["s3:ListBucket"] for s in allows)


def test_terraform_declares_both_dark_roles_bound_to_the_environment():
    tf = (_REPO / "infra/arm2_nomination_mint_role.tf").read_text()
    assert 'default     = false' in tf
    assert tf.count("environment:arm2-nomination-mint") == 2
    assert tf.count("arm2-nomination-mint-exec.yml@refs/heads/master") == 2
    assert "medzen-arm2-nomination-mint-role.json" in tf
    assert "medzen-arm2-training-index-role.json" in tf


def test_workflows_exist_and_are_environment_gated():
    caller = (_REPO / ".github/workflows/arm2-nomination-mint.yml").read_text()
    exec_body = (_REPO / ".github/workflows/"
                 "arm2-nomination-mint-exec.yml").read_text()
    assert "verify_protected_environments.py --only-supplied" in caller
    assert "arm2-nomination-mint=env.json" in caller
    assert "uses: ./.github/workflows/arm2-nomination-mint-exec.yml" in caller
    assert exec_body.count("environment: arm2-nomination-mint") == 2
    assert "merge-base --is-ancestor" in exec_body
    assert "--require-hashes" in exec_body
    assert ("assumed-role/medzen-arm2-training-index-role/" in exec_body)
    assert ("assumed-role/medzen-arm2-nomination-mint-role/" in exec_body)
    assert "confirm_mint == 'MINT'" in exec_body


def test_known_environments_include_the_mint_env_without_widening_the_gate():
    from verify_protected_environments import (KNOWN_ENVIRONMENTS,
                                               REQUIRED_ENVIRONMENTS)
    assert REQUIRED_ENVIRONMENTS == ("trainer-image-publish", "arm2-calibration")
    assert "arm2-nomination-mint" in KNOWN_ENVIRONMENTS
