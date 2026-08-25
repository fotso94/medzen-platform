"""Deterministic Arm-2 Phase-A nomination-split MINTING harness.

Phase-A held-out development nomination (protocol
B5-UNIVERSAL-ARM2-KD-COMPARISON-PROTOCOL-2026-001): mint, from the PINNED
eval-dev pools, a FROZEN per-language nomination split of the four
NOMINATION-GATED languages (english / french / pidgin / swahili). A row is
eligible IFF its ``audio_checksum_sha256`` is NOT in CANDIDATE_EXPOSED, NOT in
TRAINING_EXPOSED and NOT in SEALED (BASE_EXPOSED rows remain eligible for
Phase A — nomination compares candidates, so base-blindness is not required).

CODEX ROUND 35 CORRECTIONS (all seven findings reproduced + fixed):

1. Training evidence is a STRUCTURED, HASH-BOUND artifact
   (:func:`validate_training_index`): nonempty identities, self-consistent
   row/unique counts and aggregate sha, and source manifests bound to the
   committed gb9/gb8/gb3 adoption records' ``complete_raw_sha256`` — an empty
   or arbitrary Python collection refuses.
2. The exposure index is BOUND BY CANONICAL BYTES: ``live_mint`` takes raw
   ``index_bytes`` whose sha256 must equal the packet's
   ``exposure_index_sha256``, and every packet pin is compared to the index pin
   on key, class, role, language, rows, sha256 AND s3_version_id — a caller
   cannot reclassify a sealed pool as eligible.
3. COMPLETENESS is mandatory: the supplied pool identities must cover EXACTLY
   the reviewed read set (nomination + pinned-candidate pools — nothing
   missing, nothing extra), and sealed exclusion authorities must cover EXACTLY
   the index's sealed pools. Incomplete exclusion evidence refuses; it can
   never mint FROZEN.
4. The verifier REGENERATES the entire canonical expected manifest from the
   same inputs and compares it COMPLETELY — a forged count, status, record,
   pin or provenance field anywhere in the artifact fails verification.
5/6. The mint NEVER reads sealed manifests. Sealed exclusion comes from
   separately-governed IDENTITY-ONLY exclusion authorities
   (:func:`validate_sealed_authorities`) whose production requires explicit
   owner ledger adjudication (see the live-mint packet) — full sealed manifest
   bytes (which include references/metadata) are never fetched by this path,
   and the quarantined cv17 seal is never read. The training-index producer has
   its OWN role scoped to the training corpus (curated/*), separate from the
   mint role's 7 pinned eval-dev/candidate objects.
7. The EXECUTABLE TRUSTED PATH asserts the caller identity: ``live_mint``
   requires the STS caller document and refuses unless the ACCOUNT is exactly
   the packet's account and the ARN parses to exactly
   ``arn:aws:sts::<account>:assumed-role/<role_name>/...`` — a same-named role
   in another account refuses.

TWO MODES, FAIL-CLOSED TO OFFLINE
---------------------------------
* The PURE core (:func:`mint_phase_a_split`) operates ONLY on in-memory
  identities the caller passes in. It imports NO AWS SDK and touches NO
  network, so the class rules and every disjointness proof are exercised with
  committed fixtures and AWS is IMPOSSIBLE in the test path.
* The LIVE path (:func:`live_mint`) reads ONLY the nomination + pinned-
  candidate identity manifests through an injected ``s3_reader``; without one
  it refuses BEFORE importing or touching any AWS SDK. It extracts ONLY
  ``audio_checksum_sha256`` and emits ONLY the frozen nomination manifest +
  aggregate overlap counts — never sealed rows, text or audio. Raw manifest
  bytes exist only in the protected job's memory, never on a workstation.

This module MINTS NOTHING and reads NO S3 on import.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

# pure, in-repo identity helpers (NO AWS): the in-repo CANDIDATE_EXPOSED union
# and the source-record readers are reused verbatim so the artifacts stay
# consistent and machine-derived.
from build_arm2_exposure_index import (DEV_SELECTION, LINGALA_SENTINEL, ROOT,
                                       used_union_checksums)

INDEX = ROOT / "platform/manifests/B5-UNIVERSAL-ARM2-EXPOSURE-INDEX-2026-001.json"
LIVE_MINT_PACKET = (ROOT / "platform/decisions/"
                    "B5-UNIVERSAL-ARM2-NOMINATION-LIVE-MINT-PACKET-2026-001.json")
SPLIT_RECORD = "B5-UNIVERSAL-ARM2-NOMINATION-SPLIT-2026-001"
TRAINING_INDEX_RECORD = "B5-UNIVERSAL-ARM2-TRAINING-IDENTITY-INDEX-2026-001"

# the committed adoption/provenance records whose complete_raw_sha256 values a
# training identity index MUST cite (Codex round 35 finding 1: the index is
# derived from exact pinned sources, never an arbitrary caller list)
TRAINING_SOURCE_RECORDS = {
    "gb9": "platform/evidence/B5-GB9-ADOPTION-2026-001.json",
    "gb8": "platform/evidence/B5-GB8-ADOPTION-2026-001.json",
    "gb3": "platform/evidence/B5-GB3-MIX-PROVENANCE-2026-001.json",
}

# the four nomination-gated languages (protocol phase_A nomination_gated_languages)
NOMINATION_LANGUAGES = ("english", "french", "pidgin", "swahili")
# the three directional-veto languages — a DISJOINT surface used ONLY for the
# safety veto; never part of any nomination split
VETO_LANGUAGES = ("ewe", "kinyarwanda", "lingala")
_ELIGIBLE_DEV_CLASSES = ("BASE_EXPOSED", "BASE_BLIND_CANDIDATE_ELIGIBLE")

_HEX = frozenset("0123456789abcdef")
# fail-closed status enum: FROZEN comes only from the live chain (with full
# artifacts); MINTED_OFFLINE_FIXTURE marks fixture-driven offline mints. Any
# other value refuses — a forged status can never regenerate.
ALLOWED_STATUSES = ("MINTED_OFFLINE_FIXTURE", "FROZEN")


class MintRefusal(RuntimeError):
    """Fail-closed: the nomination split could not be minted with a proof that
    it is disjoint from every excluded class and the veto surfaces."""


class LiveMintForbidden(RuntimeError):
    """The live (S3-reading) mint was invoked without an injected reader — the
    harness has NO default AWS client; refused before any AWS SDK is touched."""


# --------------------------------------------------------------------------
# pure helpers (no AWS, no network)
# --------------------------------------------------------------------------

def load_index() -> dict:
    return json.loads(INDEX.read_bytes())


def load_packet() -> dict:
    return json.loads(LIVE_MINT_PACKET.read_bytes())


def _agg(checksums) -> tuple[int, str]:
    """(unique count, sha256 over the sorted unique set) — the same identity
    aggregation the exposure-index generator uses."""
    uniq = sorted(set(checksums))
    return len(uniq), hashlib.sha256("\n".join(uniq).encode()).hexdigest()


def _is_identity(value) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and set(value) <= _HEX)


def _is_pinned(src: dict) -> bool:
    """A pool is FROZEN-mintable only with a full immutable pin: key + content
    sha256 + S3 VersionId."""
    return bool(src.get("key") and src.get("sha256") and src.get("s3_version_id"))


def nomination_pool_keys(index: dict, *, pinned_only: bool = True
                         ) -> dict[str, list[str]]:
    """{language: [S3 manifest keys]} of the Phase-A-eligible eval-dev halves
    for the four nomination-gated languages. With ``pinned_only`` (the default,
    and what the mint uses) only fully-pinned pools are returned."""
    out: dict[str, list[str]] = {lang: [] for lang in NOMINATION_LANGUAGES}
    for src in index.get("pinned_sources", []):
        if (src.get("class") in _ELIGIBLE_DEV_CLASSES
                and src.get("role") == "eval_dev_half"
                and src.get("language") in out
                and src.get("key")
                and (not pinned_only or _is_pinned(src))):
            out[src["language"]].append(src["key"])
    return {lang: sorted(keys) for lang, keys in out.items()}


def unpinned_eligible_pools(index: dict) -> list[str]:
    """Eligible-class eval-dev pools that are NOT fully pinned — excluded from
    the frozen mint and surfaced for transparency (never silently dropped)."""
    return sorted({src["key"] for src in index.get("pinned_sources", [])
                   if src.get("class") in _ELIGIBLE_DEV_CLASSES
                   and src.get("role") == "eval_dev_half"
                   and src.get("language") in NOMINATION_LANGUAGES
                   and src.get("key") and not _is_pinned(src)})


def sealed_pool_keys(index: dict, *, pinned_only: bool = True) -> list[str]:
    return sorted({src["key"] for src in index.get("pinned_sources", [])
                   if src.get("class") == "SEALED" and src.get("key")
                   and (not pinned_only or _is_pinned(src))})


def candidate_pinned_pool_keys(index: dict, *, pinned_only: bool = True
                               ) -> list[str]:
    """S3-pinned CANDIDATE_EXPOSED pools whose per-row identities live off-repo
    and must be supplied to exclude."""
    return sorted({src["key"] for src in index.get("pinned_sources", [])
                   if src.get("class") == "CANDIDATE_EXPOSED" and src.get("key")
                   and (not pinned_only or _is_pinned(src))})


def read_pool_keys(index: dict) -> set[str]:
    """The COMPLETE, EXACT set of pools the mint reads identities from: the
    nomination eval-dev pools + the pinned CANDIDATE_EXPOSED pools. SEALED
    pools are NEVER in this set (Codex round 35 finding 6): sealed exclusion
    comes from identity-only authorities, not fetched manifest bytes."""
    return (set(sum(nomination_pool_keys(index).values(), []))
            | set(candidate_pinned_pool_keys(index)))


def veto_surface_checksums() -> set[str]:
    """The directional-veto identities, ALL in-repo: the 386-row lingala
    sentinel PLUS the kinyarwanda + ewe dev-selection rows."""
    veto: set[str] = set()
    ds = json.loads(DEV_SELECTION.read_bytes())
    veto |= {r["audio_checksum_sha256"] for r in ds["rows"]
             if r.get("language") in VETO_LANGUAGES}
    ls = json.loads(LINGALA_SENTINEL.read_bytes())
    veto |= {r["audio_checksum_sha256"] for r in ls["rows"]}
    return veto


def committed_training_source_digests() -> dict[str, str]:
    """{dataset: complete_raw_sha256} read from the COMMITTED adoption /
    provenance records — the only acceptable source binding for a training
    identity index."""
    out: dict[str, str] = {}
    for dataset, rel in TRAINING_SOURCE_RECORDS.items():
        doc = json.loads((ROOT / rel).read_bytes())
        digest = (doc.get("complete_raw_sha256")
                  or (doc.get("provenance") or {}).get("complete_raw_sha256"))
        out[dataset] = str(digest)
    return out


def validate_training_index(artifact, *, committed=None) -> set[str]:
    """Codex round 35 finding 1: the training identity index must be a
    STRUCTURED, HASH-BOUND, NONEMPTY artifact derived from the exact pinned
    gb9/gb8/gb3 sources — never an arbitrary Python collection. Returns the
    identity set on success; raises MintRefusal otherwise. ``committed``
    defaults to the digests read from the COMMITTED adoption records; the
    producer's self-check passes its own verified set through."""
    if not isinstance(artifact, dict):
        raise MintRefusal(
            "the training identity index must be the STRUCTURED artifact "
            f"(record {TRAINING_INDEX_RECORD}), not a bare "
            f"{type(artifact).__name__} — an arbitrary collection carries no "
            "source binding")
    if artifact.get("record") != TRAINING_INDEX_RECORD:
        raise MintRefusal(
            f"training index record is {artifact.get('record')!r}, expected "
            f"{TRAINING_INDEX_RECORD!r}")
    if artifact.get("identity_key") != "audio_checksum_sha256":
        raise MintRefusal("training index identity_key must be "
                          "audio_checksum_sha256")
    sources = artifact.get("source_manifests")
    if not isinstance(sources, list) or not sources:
        raise MintRefusal("training index declares no source_manifests")
    if committed is None:
        committed = committed_training_source_digests()
    declared = {}
    for entry in sources:
        dataset = str(entry.get("dataset") or "")
        declared[dataset] = str(entry.get("complete_raw_sha256") or "")
        if str(entry.get("source_record") or "") != \
                TRAINING_SOURCE_RECORDS.get(dataset, ""):
            raise MintRefusal(
                f"training index source {dataset!r} cites "
                f"{entry.get('source_record')!r}, not the committed record")
    if set(declared) != set(committed):
        raise MintRefusal(
            f"training index sources {sorted(declared)} != the required "
            f"pinned corpora {sorted(committed)}")
    for dataset, digest in sorted(committed.items()):
        if declared[dataset] != digest:
            raise MintRefusal(
                f"training index source {dataset!r} declares "
                f"complete_raw_sha256 {declared[dataset][:16]}, the committed "
                f"adoption record pins {digest[:16]} — the index is not "
                "derived from the pinned corpus")
    if not isinstance(artifact.get("producer"), dict) \
            or not str(artifact["producer"].get("script") or "").strip():
        raise MintRefusal("training index carries no producer receipt")
    identities = artifact.get("identities")
    if not isinstance(identities, list) or not identities:
        raise MintRefusal(
            "training index has NO identities — an empty index proves nothing "
            "and can never stand in for the training corpus (Codex round 35 "
            "finding 1)")
    for value in identities:
        if not _is_identity(value):
            raise MintRefusal(
                f"training index carries a malformed identity "
                f"{str(value)[:24]!r}")
    if len(set(identities)) != len(identities):
        raise MintRefusal("training index carries duplicate identities")
    unique, aggregate = _agg(identities)
    if artifact.get("unique_count") != unique:
        raise MintRefusal(
            f"training index unique_count {artifact.get('unique_count')!r} != "
            f"recomputed {unique}")
    if not isinstance(artifact.get("row_count"), int) \
            or artifact["row_count"] < unique:
        raise MintRefusal(
            f"training index row_count {artifact.get('row_count')!r} is not an "
            f"int >= unique_count {unique}")
    if artifact.get("aggregate_sha256") != aggregate:
        raise MintRefusal(
            f"training index aggregate {str(artifact.get('aggregate_sha256'))[:16]} "
            f"does not reproduce from its identities ({aggregate[:16]})")
    return set(identities)


def validate_sealed_authorities(authorities, index: dict) -> tuple[set[str], list[dict]]:
    """Codex round 35 findings 3/6: sealed exclusion comes from separately-
    governed IDENTITY-ONLY authorities — one per sealed pool, covering the
    index's sealed pools EXACTLY — never from fetched sealed manifest bytes.
    Each authority is validated against the index pin (key/class/rows/sha256/
    VersionId), must carry a governance adjudication, and its identities must
    reproduce its aggregate. Returns (identity union, provenance rows)."""
    if not isinstance(authorities, list) or not authorities:
        raise MintRefusal(
            "no sealed exclusion authorities were supplied — sealed "
            "disjointness cannot be proven (and sealed manifests are NEVER "
            "fetched by the mint)")
    want = set(sealed_pool_keys(index))
    pins = {src["key"]: src for src in index.get("pinned_sources", [])
            if src.get("key") in want and src.get("class") == "SEALED"}
    got = set()
    union: set[str] = set()
    provenance: list[dict] = []
    for auth in authorities:
        if not isinstance(auth, dict):
            raise MintRefusal("sealed authority is not a structured artifact")
        key = str(auth.get("key") or "")
        if key not in want:
            raise MintRefusal(
                f"sealed authority for {key!r} does not correspond to a sealed "
                "pool in the exposure index")
        if key in got:
            raise MintRefusal(f"duplicate sealed authority for {key!r}")
        got.add(key)
        pin = pins[key]
        for field in ("sha256", "s3_version_id", "rows"):
            if auth.get(field) != pin.get(field):
                raise MintRefusal(
                    f"sealed authority for {key!r} declares {field}="
                    f"{str(auth.get(field))[:24]!r}, the index pins "
                    f"{str(pin.get(field))[:24]!r}")
        gov = auth.get("governance")
        if not isinstance(gov, dict) \
                or not str(gov.get("adjudication") or "").strip():
            raise MintRefusal(
                f"sealed authority for {key!r} carries no governance "
                "adjudication — sealed identity extraction requires explicit "
                "owner ledger adjudication")
        identities = auth.get("identities")
        if not isinstance(identities, list) or not identities:
            raise MintRefusal(f"sealed authority for {key!r} has no identities")
        for value in identities:
            if not _is_identity(value):
                raise MintRefusal(
                    f"sealed authority for {key!r} carries a malformed "
                    f"identity {str(value)[:24]!r}")
        if len(set(identities)) != len(identities):
            raise MintRefusal(
                f"sealed authority for {key!r} carries duplicate identities")
        if pin.get("rows") is not None and len(identities) != int(pin["rows"]):
            raise MintRefusal(
                f"sealed authority for {key!r} holds {len(identities)} "
                f"identities, the index pins rows={pin['rows']}")
        unique, aggregate = _agg(identities)
        if auth.get("aggregate_sha256") != aggregate:
            raise MintRefusal(
                f"sealed authority for {key!r} aggregate does not reproduce "
                "from its identities")
        union |= set(identities)
        provenance.append({"key": key, "rows": pin.get("rows"),
                           "identities_unique": unique,
                           "aggregate_sha256": aggregate,
                           "adjudication": str(gov.get("adjudication"))})
    missing = sorted(want - got)
    if missing:
        raise MintRefusal(
            f"sealed exclusion authorities are INCOMPLETE — missing "
            f"{missing}; a partial exclusion set can never prove disjointness")
    return union, sorted(provenance, key=lambda p: p["key"])


def _pool_pins(index: dict, keys) -> list[dict]:
    keyset = set(keys)
    pins = []
    for src in index.get("pinned_sources", []):
        if src.get("key") in keyset:
            pins.append({"key": src["key"], "class": src["class"],
                         "language": src.get("language"),
                         "role": src.get("role"), "rows": src.get("rows"),
                         "sha256": src.get("sha256"),
                         "s3_version_id": src.get("s3_version_id")})
    return sorted(pins, key=lambda p: (p["key"] or ""))


def mint_phase_a_split(index: dict, pool_identities: dict[str, list[str]],
                       *, training_index=None, sealed_authorities=None,
                       status: str = "MINTED_OFFLINE_FIXTURE") -> dict:
    """Mint the Phase-A nomination split from the supplied identities. PURE:
    no AWS, no network. Refuses (MintRefusal) on any leak, any duplicate
    identity within or across languages, any empty language split, any
    nomination/veto collision, any INCOMPLETE or EXTRA input evidence, and —
    for a FROZEN mint — on missing/invalid training-index or sealed-authority
    artifacts.

    ``pool_identities`` must cover EXACTLY the reviewed read set
    (:func:`read_pool_keys`: nomination + pinned-candidate pools) — nothing
    missing, nothing extra (Codex round 35 finding 3). ``training_index`` is
    the structured artifact (:func:`validate_training_index`);
    ``sealed_authorities`` the identity-only authorities
    (:func:`validate_sealed_authorities`). Absent, the respective overlap is
    reported ``"UNVERIFIED"`` — never zero — and a FROZEN mint refuses."""
    if status not in ALLOWED_STATUSES:
        raise MintRefusal(
            f"unknown mint status {status!r} — statuses fail closed "
            f"({ALLOWED_STATUSES})")
    if status == "FROZEN":
        if training_index is None:
            raise MintRefusal(
                "a FROZEN mint requires the structured training identity "
                "index — without it the training overlap is UNVERIFIED")
        if sealed_authorities is None:
            raise MintRefusal(
                "a FROZEN mint requires the sealed identity-only exclusion "
                "authorities — without them the sealed overlap is UNVERIFIED")
    training = (validate_training_index(training_index)
                if training_index is not None else None)
    if sealed_authorities is not None:
        sealed, sealed_provenance = validate_sealed_authorities(
            sealed_authorities, index)
    else:
        sealed, sealed_provenance = None, None

    # COMPLETE, EXACT read set (finding 3): nothing missing, nothing extra
    required = read_pool_keys(index)
    supplied = set(pool_identities)
    missing = sorted(required - supplied)
    if missing:
        raise MintRefusal(
            f"pool identities are INCOMPLETE — missing {missing}; the mint "
            "requires the complete reviewed read set")
    extra = sorted(supplied - required)
    if extra:
        raise MintRefusal(
            f"pool identities include {extra} which are NOT in the reviewed "
            "read set — refusing unreviewed inputs")

    candidate = set(used_union_checksums())
    for key in candidate_pinned_pool_keys(index):
        rows = list(pool_identities[key])
        for value in rows:
            if not _is_identity(value):
                raise MintRefusal(
                    f"candidate pool {key!r} carries a malformed identity "
                    f"{str(value)[:24]!r}")
        if len(rows) != len(set(rows)):
            raise MintRefusal(
                f"candidate pool {key!r} contains duplicate identities — a "
                "repeated identity means a corrupted or double-counted "
                "manifest")
        candidate |= set(rows)
    veto = veto_surface_checksums()
    excluded_all = set(candidate)
    if sealed is not None:
        excluded_all |= sealed
    if training is not None:
        excluded_all |= training
    pools_by_lang = nomination_pool_keys(index)

    split: dict[str, list[str]] = {}
    per_language: dict[str, dict] = {}
    for lang in NOMINATION_LANGUAGES:
        keys = pools_by_lang.get(lang, [])
        if not keys:
            raise MintRefusal(
                f"nomination language {lang!r} has no fully-PINNED eligible "
                "eval-dev pool in the exposure index")
        raw: list[str] = []
        for key in keys:
            raw += list(pool_identities[key])
        for value in raw:
            if not _is_identity(value):
                raise MintRefusal(
                    f"nomination pool for {lang!r} carries a malformed "
                    f"identity {str(value)[:24]!r}")
        if len(raw) != len(set(raw)):
            raise MintRefusal(
                f"nomination pools for {lang!r} contain duplicate identities "
                "within the language — refusing a double-counted split")
        raw_unique = sorted(raw)
        veto_here = sorted(set(raw_unique) & veto)
        if veto_here:
            raise MintRefusal(
                f"nomination pool for {lang!r} contains {len(veto_here)} "
                "directional-veto identities — nomination and veto surfaces "
                "must be DISJOINT")
        eligible = [c for c in raw_unique if c not in excluded_all]
        if not eligible:
            raise MintRefusal(
                f"nomination language {lang!r} split is EMPTY after excluding "
                "candidate/sealed/training identities — refusing")
        count, agg = _agg(eligible)
        split[lang] = eligible
        per_language[lang] = {
            "pool_keys": keys,
            "raw_unique": len(raw_unique),
            "removed_candidate": len(set(raw_unique) & candidate),
            "removed_sealed": (len(set(raw_unique) & sealed)
                               if sealed is not None else "UNVERIFIED"),
            "removed_training": (len(set(raw_unique) & training)
                                 if training is not None else "UNVERIFIED"),
            "eligible": count,
            "split_aggregate_sha256": agg,
        }

    all_split_list = [c for lang in NOMINATION_LANGUAGES for c in split[lang]]
    all_split = sorted(set(all_split_list))
    if len(all_split_list) != len(all_split):
        dupes = sorted({c for c in all_split_list
                        if all_split_list.count(c) > 1})
        raise MintRefusal(
            f"{len(dupes)} identities appear in MORE THAN ONE language split "
            "— cross-language duplicates are refused")

    overlap = {
        "candidate_exposed": len(set(all_split) & candidate),
        "sealed": (len(set(all_split) & sealed)
                   if sealed is not None else "UNVERIFIED"),
        "veto_surface": len(set(all_split) & veto),
        "training_exposed": (len(set(all_split) & training)
                             if training is not None else "UNVERIFIED"),
    }
    numeric = {k: v for k, v in overlap.items() if isinstance(v, int)}
    if any(numeric.values()):
        raise MintRefusal(
            f"the minted split is NOT disjoint from the excluded classes "
            f"{overlap} — refusing to emit a leaking nomination split")

    cand_n, cand_agg = _agg(candidate)
    veto_n, veto_agg = _agg(veto)
    split_n, split_agg = _agg(all_split)
    provenance: dict = {
        "candidate_exposed_unique": cand_n,
        "candidate_exposed_aggregate_sha256": cand_agg,
        "veto_surface_unique": veto_n,
        "veto_surface_aggregate_sha256": veto_agg,
    }
    if sealed is not None:
        s_n, s_agg = _agg(sealed)
        provenance["sealed_unique"] = s_n
        provenance["sealed_aggregate_sha256"] = s_agg
        provenance["sealed_authorities"] = sealed_provenance
    else:
        provenance["sealed_unique"] = "UNVERIFIED"
        provenance["sealed_note"] = ("no identity-only sealed exclusion "
            "authorities were supplied — sealed overlap is UNVERIFIED (not "
            "zero); a FROZEN mint refuses in this state")
    if training is not None:
        t_n, t_agg = _agg(training)
        provenance["training_exposed_unique"] = t_n
        provenance["training_exposed_aggregate_sha256"] = t_agg
    else:
        provenance["training_exposed_unique"] = "UNVERIFIED"
        provenance["training_note"] = ("no structured training identity index "
            "was supplied — training overlap is UNVERIFIED (not zero); a "
            "FROZEN mint refuses in this state")

    return {
        "record": SPLIT_RECORD,
        "status": status,
        "phase": "A_held_out_development_nomination",
        "protocol": "B5-UNIVERSAL-ARM2-KD-COMPARISON-PROTOCOL-2026-001",
        "identity_key": "audio_checksum_sha256",
        "generator": "scripts/mint_arm2_nomination_split.py",
        "eligibility_rule": "audio_checksum_sha256 NOT in CANDIDATE_EXPOSED, "
                            "NOT in TRAINING_EXPOSED, NOT in SEALED "
                            "(BASE_EXPOSED remains eligible for Phase A)",
        "nomination_languages": list(NOMINATION_LANGUAGES),
        "veto_languages": list(VETO_LANGUAGES),
        "split": {lang: split[lang] for lang in NOMINATION_LANGUAGES},
        "per_language": per_language,
        "split_identity": {"unique": split_n, "aggregate_sha256": split_agg},
        "aggregate_overlap_counts": overlap,
        "exclusion_provenance": provenance,
        "pool_pins": _pool_pins(index, read_pool_keys(index)),
        "sealed_pools_never_read": sealed_pool_keys(index),
        "unpinned_excluded_pools": unpinned_eligible_pools(index),
        "pinned_only_note": "the frozen mint draws ONLY from fully-pinned "
                            "(key+sha256+VersionId) eval-dev pools; any eligible "
                            "but unpinned pool is listed in "
                            "unpinned_excluded_pools, never silently used",
        "NEVER_INCLUDES": "sealed rows, transcript text or audio — the frozen "
                          "manifest carries eligible IDENTITIES only",
    }


def verify_frozen_manifest(manifest: dict, index: dict,
                           pool_identities: dict[str, list[str]],
                           *, training_index=None,
                           sealed_authorities=None) -> list[str]:
    """Codex round 35 finding 4: REGENERATE the entire canonical expected
    manifest from the same inputs and compare it COMPLETELY — a forged value
    anywhere in the artifact (declared counts, status, record, pins,
    provenance, per-language numbers, extra keys) fails verification. Returns a
    list of failures (empty == verified)."""
    failures: list[str] = []
    try:
        expected = mint_phase_a_split(
            index, pool_identities, training_index=training_index,
            sealed_authorities=sealed_authorities,
            status=str(manifest.get("status") or ""))
    except (MintRefusal, LiveMintForbidden) as exc:
        return [f"the canonical manifest cannot be regenerated from these "
                f"inputs: {exc}"]
    if json.dumps(expected, sort_keys=True) == \
            json.dumps(manifest, sort_keys=True):
        return []
    for key in sorted(set(expected) | set(manifest)):
        want = expected.get(key)
        got = manifest.get(key)
        if json.dumps(want, sort_keys=True, default=str) != \
                json.dumps(got, sort_keys=True, default=str):
            failures.append(
                f"{key!r} does not match the regenerated canonical manifest")
    return failures or ["manifest bytes differ from the regenerated canonical "
                        "manifest"]


# --------------------------------------------------------------------------
# LIVE path — refuses before touching any AWS SDK; verifies every byte it uses
# --------------------------------------------------------------------------

def validate_caller_identity(caller, *, account: str, role_name: str) -> None:
    """Codex round 35 finding 7: the EXECUTABLE TRUSTED PATH asserts the exact
    STS account AND role. The ARN must parse to exactly
    arn:aws:sts::<account>:assumed-role/<role_name>/<session> — a same-named
    role in another account, a substring match, or a user ARN all refuse."""
    if not isinstance(caller, dict):
        raise MintRefusal("no STS caller identity was supplied to the "
                          "trusted path")
    got_account = str(caller.get("Account") or "")
    arn = str(caller.get("Arn") or "")
    if got_account != account:
        raise MintRefusal(
            f"caller account {got_account!r} != the packet's pinned account "
            f"{account!r}")
    parts = arn.split(":")
    ok = (len(parts) == 6 and parts[0] == "arn" and parts[1] == "aws"
          and parts[2] == "sts" and parts[4] == account
          and parts[5].startswith(f"assumed-role/{role_name}/")
          and len(parts[5].split("/")) == 3
          and parts[5].split("/")[2])
    if not ok:
        raise MintRefusal(
            f"caller ARN {arn!r} is not exactly "
            f"arn:aws:sts::{account}:assumed-role/{role_name}/<session> — "
            "refusing any other principal")


def _verify_fetched_object(pin: dict, fetched: dict, *, expected_kms: str,
                           key: str) -> list[str]:
    """Trusted-path integrity checks on ONE fetched identity manifest (round
    34 finding 2): actual-bytes sha256, echoed VersionId, echoed KMS key, exact
    pinned row count, per-row schema and language."""
    body = fetched.get("body")
    if not isinstance(body, (bytes, bytearray)):
        raise MintRefusal(f"{key}: reader returned no raw bytes")
    actual_sha = hashlib.sha256(bytes(body)).hexdigest()
    if actual_sha != pin.get("sha256"):
        raise MintRefusal(
            f"{key}: downloaded bytes hash to {actual_sha[:16]}, the pin "
            f"declares {str(pin.get('sha256'))[:16]} — refusing unverified "
            "bytes")
    if str(fetched.get("version_id") or "") != str(pin.get("s3_version_id")):
        raise MintRefusal(
            f"{key}: S3 returned VersionId "
            f"{str(fetched.get('version_id'))[:24]!r}, the pin declares "
            f"{str(pin.get('s3_version_id'))[:24]!r}")
    if str(fetched.get("kms_key_arn") or "") != expected_kms:
        raise MintRefusal(
            f"{key}: object is encrypted with "
            f"{str(fetched.get('kms_key_arn'))[:48]!r}, not the pinned CMK")
    identities: list[str] = []
    for line in bytes(body).decode().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise MintRefusal(f"{key}: non-JSON manifest row") from exc
        value = row.get("audio_checksum_sha256")
        if not _is_identity(value):
            raise MintRefusal(
                f"{key}: row carries a malformed audio_checksum_sha256 "
                f"{str(value)[:24]!r}")
        row_lang = row.get("language")
        if row_lang is not None and pin.get("language") is not None \
                and str(row_lang).strip().lower() != str(pin["language"]).lower():
            raise MintRefusal(
                f"{key}: row language {row_lang!r} does not match the pinned "
                f"pool language {pin.get('language')!r}")
        identities.append(value)
    expected_rows = pin.get("rows")
    if expected_rows is not None and len(identities) != int(expected_rows):
        raise MintRefusal(
            f"{key}: manifest holds {len(identities)} rows, the pin declares "
            f"{expected_rows}")
    fetched["_identities"] = identities
    return identities


_PIN_FIELDS = ("class", "role", "language", "rows", "sha256", "s3_version_id")


def live_mint(packet: dict, *, index_bytes: bytes, s3_reader,
              caller_identity, training_index, sealed_authorities) -> dict:
    """Read ONLY the nomination + pinned-candidate identity manifests through
    the injected ``s3_reader`` and mint the FROZEN split.

    Codex round 35 hardening: (2) the exposure index arrives as RAW BYTES and
    must hash to the packet's ``exposure_index_sha256`` — a tampered or
    reclassified index refuses; every packet pin is compared to the index pin
    on ALL of {class, role, language, rows, sha256, s3_version_id}. (6) SEALED
    manifests are NEVER fetched — sealed exclusion comes from the validated
    identity-only authorities. (7) the caller identity is asserted IN this
    executable path: exact account + exact assumed-role ARN.

    Refuses (LiveMintForbidden) BEFORE importing or touching any AWS SDK when
    no reader is supplied. Emits ONLY identities + aggregate counts."""
    if s3_reader is None:
        raise LiveMintForbidden(
            "no s3_reader was injected — this harness has NO default AWS client; "
            "the authorized protected job must pass a minimal read-only reader")
    account = str((packet.get("aws") or {}).get("account") or "")
    role_name = str(((packet.get("minimal_read_role") or {})
                     .get("role_name")) or "")
    if not account or not role_name:
        raise MintRefusal("the live-mint packet pins no account/role identity")
    validate_caller_identity(caller_identity, account=account,
                             role_name=role_name)
    expected_index_sha = str(packet.get("exposure_index_sha256") or "")
    if not expected_index_sha:
        raise MintRefusal(
            "the live-mint packet pins no exposure_index_sha256 — the index "
            "must be bound by canonical bytes")
    actual_index_sha = hashlib.sha256(bytes(index_bytes)).hexdigest()
    if actual_index_sha != expected_index_sha:
        raise MintRefusal(
            f"the exposure index bytes hash to {actual_index_sha[:16]}, the "
            f"packet pins {expected_index_sha[:16]} — refusing a tampered or "
            "unreviewed index")
    index = json.loads(bytes(index_bytes).decode())
    expected_kms = str(((packet.get("aws") or {}).get("kms_key")) or "")
    if not expected_kms:
        raise MintRefusal("the live-mint packet pins no KMS key")

    packet_pins = {p.get("key"): p for p in packet.get("pinned_objects", [])}
    wanted = read_pool_keys(index)          # NEVER includes sealed pools
    for key in sorted(sealed_pool_keys(index)):
        if key in packet_pins and packet_pins[key].get("fetch") is not False:
            # a packet that asks the mint to FETCH sealed bytes is invalid by
            # construction (finding 6) unless the pin is explicitly marked as
            # an exclusion-authority reference (fetch=false)
            raise MintRefusal(
                f"the packet pins sealed object {key!r} for fetching — the "
                "mint NEVER reads sealed manifests; sealed exclusion is "
                "identity-authority-based")
    index_pins = {}
    for src in index.get("pinned_sources", []):
        key = src.get("key")
        if key in wanted and key not in index_pins:
            index_pins[key] = src
    pool_identities: dict[str, list[str]] = {}
    for key in sorted(wanted):
        pin = index_pins.get(key)
        pkt_pin = packet_pins.get(key)
        if pkt_pin is None:
            raise MintRefusal(
                f"{key}: the live-mint packet does not pin this object — the "
                "reviewed packet must pin EVERYTHING the mint reads")
        for field in _PIN_FIELDS:
            if json.dumps(pkt_pin.get(field), sort_keys=True) != \
                    json.dumps(pin.get(field), sort_keys=True):
                raise MintRefusal(
                    f"{key}: packet pin {field}={pkt_pin.get(field)!r} "
                    f"disagrees with the exposure index "
                    f"{pin.get(field)!r} — refusing a torn pin pair")
        fetched = s3_reader(key=key, s3_version_id=pin.get("s3_version_id"))
        if not isinstance(fetched, dict):
            raise MintRefusal(
                f"{key}: reader must return the body WITH its echoed "
                "VersionId + KMS metadata — got bare "
                f"{type(fetched).__name__}")
        pool_identities[key] = _verify_fetched_object(
            pin, fetched, expected_kms=expected_kms, key=key)
    return mint_phase_a_split(index, pool_identities,
                              training_index=training_index,
                              sealed_authorities=sealed_authorities,
                              status="FROZEN")


def _dump(obj) -> str:
    return json.dumps(obj, indent=1, sort_keys=True) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live", action="store_true",
                    help="perform the LIVE S3-reading mint — only inside the "
                         "owner-approved protected workflow, under the "
                         "dedicated read role")
    args = ap.parse_args(argv)
    if not args.live:
        raise SystemExit(
            "offline invocation mints nothing: the pure core "
            "(mint_phase_a_split) is driven by committed fixtures in "
            "tests/test_arm2_nomination_mint.py; the FROZEN mint is the "
            "owner-authorized `--live` step in the live-mint packet")
    packet = load_packet()
    account = str((packet.get("aws") or {}).get("account") or "")
    role_name = str(((packet.get("minimal_read_role") or {})
                     .get("role_name")) or "")
    try:
        import boto3  # lazy: never imported on the offline/test path
    except ImportError as exc:
        raise SystemExit(
            "boto3 is unavailable — the live mint runs only inside the "
            "approved protected workflow with the dedicated role; refusing "
            f"({exc})")
    caller = boto3.client("sts").get_caller_identity()
    # the same exact-account + exact-role assertion the trusted path enforces
    validate_caller_identity({"Account": caller.get("Account"),
                              "Arn": caller.get("Arn")},
                             account=account, role_name=role_name)
    raise SystemExit(
        "caller identity verified as the dedicated mint role, but the FROZEN "
        "mint requires the structured training identity index AND the sealed "
        "identity-only exclusion authorities (Codex round 35) — produce them "
        "via their owner-approved governed steps per the live-mint packet; "
        "refusing until then")


if __name__ == "__main__":
    raise SystemExit(main())
