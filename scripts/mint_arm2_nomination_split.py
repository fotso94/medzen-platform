"""Deterministic Arm-2 Phase-A nomination-split MINTING harness.

Phase-A held-out development nomination (protocol
B5-UNIVERSAL-ARM2-KD-COMPARISON-PROTOCOL-2026-001): mint, from the PINNED
eval-dev pools, a FROZEN per-language nomination split of the four
NOMINATION-GATED languages (english / french / pidgin / swahili). A row is
eligible IFF its ``audio_checksum_sha256`` is NOT in CANDIDATE_EXPOSED, NOT in
TRAINING_EXPOSED and NOT in SEALED (BASE_EXPOSED rows remain eligible for
Phase A — nomination compares candidates, so base-blindness is not required).

CODEX ROUND 36 — AUTHENTICATION, not mere self-consistency. The two prior
evidence files (training identity index, sealed exclusion authorities) could be
INVENTED by the caller because the consumer only checked internal consistency.
The fix roots trust in OWNER-COMMITTED ADMISSION LEDGERS, exactly as
scripts/holdout_ledger.py roots the sealed-holdout gate:

  * Two hash-chained JSONL ledgers under platform/evidence/ (ARM2-TRAINING-
    INDEX-ADMISSION-LEDGER.jsonl, ARM2-SEALED-EXCLUSION-LEDGER.jsonl), each
    entry embedding prev_sha256 of the previous line; verify_chain refuses any
    rewrite. They ship with a single LEDGER_OPENED_PENDING genesis entry, so
    the active-entry set is EMPTY and every FROZEN mint FAILS CLOSED today.
  * The ledgers are read from git HEAD (git show HEAD:<path>, path-guarded) —
    NEVER from a caller argument (a caller-supplied ledger would recreate the
    forgery one level up) and NEVER a bare working-tree read. Their head sha256
    is ALSO pinned in the reviewed live-mint packet.
  * The consumer canonicalises the supplied artifact, takes sha256 of the EXACT
    bytes, and admits it ONLY if that digest equals an ACTIVE ledger entry's
    artifact_sha256; then it cross-checks every binding field, requires the
    live source/index digests to agree, reproduces the committed identity
    aggregate, and verifies the entry's owner approval record (a committed
    platform/decisions/ record, git-show + sha, authorizes + owner_verbatim) —
    the holdout_ledger._verify_owner_approval pattern. A fabricated artifact has
    no active entry and is refused; a made-up adjudication string is worthless.
  * Sealed exclusion is DISPOSITION-DRIVEN and NEVER forces reading an
    untouched seal: BY_CONSTRUCTION_DISJOINT pools (dev/sealed complements, and
    the cross-language seals) satisfy completeness with NO identities via a
    committed disjointness record; only CLEARED_FOR_EXCLUSION pools carry
    identities. The quarantined cv17 pool needs a SEPARATE quarantine-clearance
    record. The mint NEVER reads a sealed manifest byte.

TWO MODES, FAIL-CLOSED TO OFFLINE
---------------------------------
* The PURE core operates ONLY on in-memory identities the caller passes in and
  the committed ledgers/records read through the module-level ``_read_committed``
  (which tests monkeypatch). It imports NO AWS SDK and touches NO network.
* The LIVE path (:func:`live_mint`) reads ONLY the 7 nomination + pinned-
  candidate identity manifests through an injected ``s3_reader``, loads the
  committed ledgers ITSELF, and emits ONLY the frozen nomination manifest +
  aggregate overlap counts + a hash-bound result — never sealed rows, text or
  audio.

This module MINTS NOTHING and reads NO S3 on import.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from build_arm2_exposure_index import (DEV_SELECTION, LINGALA_SENTINEL, ROOT,
                                       used_union_checksums)

INDEX = ROOT / "platform/manifests/B5-UNIVERSAL-ARM2-EXPOSURE-INDEX-2026-001.json"
LIVE_MINT_PACKET = (ROOT / "platform/decisions/"
                    "B5-UNIVERSAL-ARM2-NOMINATION-LIVE-MINT-PACKET-2026-001.json")
TRAINING_INDEX_LEDGER = "platform/evidence/ARM2-TRAINING-INDEX-ADMISSION-LEDGER.jsonl"
SEALED_EXCLUSION_LEDGER = "platform/evidence/ARM2-SEALED-EXCLUSION-LEDGER.jsonl"
SPLIT_RECORD = "B5-UNIVERSAL-ARM2-NOMINATION-SPLIT-2026-001"
TRAINING_INDEX_RECORD = "B5-UNIVERSAL-ARM2-TRAINING-IDENTITY-INDEX-2026-001"

TRAINING_SOURCE_RECORDS = {
    "gb9": "platform/evidence/B5-GB9-ADOPTION-2026-001.json",
    "gb8": "platform/evidence/B5-GB8-ADOPTION-2026-001.json",
    "gb3": "platform/evidence/B5-GB3-MIX-PROVENANCE-2026-001.json",
}

NOMINATION_LANGUAGES = ("english", "french", "pidgin", "swahili")
VETO_LANGUAGES = ("ewe", "kinyarwanda", "lingala")
_ELIGIBLE_DEV_CLASSES = ("BASE_EXPOSED", "BASE_BLIND_CANDIDATE_ELIGIBLE")

# fail-closed status enum: FROZEN comes only from the authenticated live chain;
# MINTED_OFFLINE_FIXTURE marks fixture-driven offline mints. Any other value
# refuses — a forged status can never regenerate.
ALLOWED_STATUSES = ("MINTED_OFFLINE_FIXTURE", "FROZEN")
# the sealed-exclusion dispositions that PERMIT a pool to satisfy completeness.
# BY_CONSTRUCTION_DISJOINT needs no identities (a committed disjointness proof);
# CLEARED_FOR_EXCLUSION needs the identity set. Anything else fails closed.
SEALED_DISPOSITIONS = ("CLEARED_FOR_EXCLUSION", "BY_CONSTRUCTION_DISJOINT")

_HEX = frozenset("0123456789abcdef")
_QUARANTINED_ROLE = "kinyarwanda_eval_quarantined"


class MintRefusal(RuntimeError):
    """Fail-closed: the nomination split could not be minted with an
    AUTHENTICATED proof that it is disjoint from every excluded class."""


class LiveMintForbidden(RuntimeError):
    """The live (S3-reading) mint was invoked without an injected reader — the
    harness has NO default AWS client; refused before any AWS SDK is touched."""


# --------------------------------------------------------------------------
# canonicalisation + strict parse (Codex round 36 finding-substrate / A10)
# --------------------------------------------------------------------------

def _canon(obj) -> bytes:
    """The ONE canonical serializer used by producer, admission tooling and
    consumer: sorted keys, tight separators, ASCII-escaped. artifact_sha256 is
    sha256 of the EXACT emitted bytes."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode()


def _reject_duplicate_keys(pairs):
    seen: dict = {}
    for key, value in pairs:
        if key in seen:
            raise MintRefusal(
                f"duplicate JSON key {key!r} — canonicalisation ambiguity is "
                "refused (a duplicate key silently last-wins)")
        seen[key] = value
    return seen


def _loads_strict(raw: bytes):
    """Parse rejecting duplicate keys, so what the owner reviewed is exactly
    what is hashed and used."""
    if not isinstance(raw, (bytes, bytearray)):
        raise MintRefusal("strict parse requires raw bytes")
    return json.loads(bytes(raw).decode(), object_pairs_hook=_reject_duplicate_keys)


def _require_canonical(raw: bytes, what: str):
    """The supplied bytes must already BE canonical (reviewed == hashed ==
    used): parse strictly, re-canonicalise, and refuse unless byte-identical."""
    obj = _loads_strict(raw)
    if _canon(obj) != bytes(raw):
        raise MintRefusal(
            f"{what} is not in canonical form — refusing (the reviewed bytes "
            "must equal the canonical bytes that are hashed and used)")
    return obj


def _is_identity(value) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and set(value) <= _HEX)


def _agg(checksums) -> tuple[int, str]:
    uniq = sorted(set(checksums))
    return len(uniq), hashlib.sha256("\n".join(uniq).encode()).hexdigest()


# --------------------------------------------------------------------------
# committed-source loader (Codex round 36 A5 — mirrors holdout_ledger #13)
# --------------------------------------------------------------------------

def _read_committed(relpath: str, *, allowed_prefixes: tuple[str, ...],
                    repo_root: Path = ROOT) -> bytes:
    """Read a TRUST-BEARING file from git HEAD (git show HEAD:<path>), never a
    bare working-tree read. Rejects absolute / traversal / non-normalised /
    symlinked / out-of-prefix / untracked paths. Tests monkeypatch THIS
    function to inject ledger + approval-record fixtures — it is the single
    committed-source seam, never a caller argument."""
    p = str(relpath)
    if (p != str(Path(p)) or Path(p).is_absolute() or ".." in Path(p).parts
            or not any(p.startswith(pre) for pre in allowed_prefixes)):
        raise MintRefusal(f"refusing untrusted committed path {p!r}")
    if (repo_root / p).is_symlink():
        raise MintRefusal(f"refusing symlinked committed path {p!r}")
    got = subprocess.run(["git", "-C", str(repo_root), "show", f"HEAD:{p}"],
                         capture_output=True)
    if got.returncode != 0:
        raise MintRefusal(f"{p} is not committed at git HEAD")
    return got.stdout


def _verify_approval_record(ref, *, authorizes: str, subject_field: str | None,
                            subject_value) -> None:
    """An owner authorization = a committed platform/decisions/ record (git-show
    + sha256), doc.record == record_id, doc.authorizes == <event>, optional
    doc.<subject_field> == <subject_value>, and a nonempty owner_verbatim.
    Mirrors holdout_ledger._verify_owner_approval. Raises MintRefusal on any
    failure — an inline string is never enough."""
    if not isinstance(ref, dict):
        raise MintRefusal("approval reference is not a {path,sha256,record_id}")
    raw = _read_committed(str(ref.get("path", "")),
                          allowed_prefixes=("platform/decisions/",))
    if hashlib.sha256(raw).hexdigest() != ref.get("sha256"):
        raise MintRefusal(
            f"approval record {ref.get('path')!r} sha256 mismatch — refusing")
    try:
        doc = _loads_strict(raw)
    except MintRefusal:
        raise
    if doc.get("record") != ref.get("record_id"):
        raise MintRefusal("approval record id mismatch")
    if doc.get("authorizes") != authorizes:
        raise MintRefusal(
            f"approval record authorizes {doc.get('authorizes')!r}, not "
            f"{authorizes!r}")
    if subject_field is not None and doc.get(subject_field) != subject_value:
        raise MintRefusal(
            f"approval record {subject_field}={doc.get(subject_field)!r} != "
            f"{subject_value!r}")
    if not str(doc.get("owner_verbatim", "")).strip():
        raise MintRefusal("approval record carries no owner_verbatim")


# --------------------------------------------------------------------------
# hash-chained ledger loading (Codex round 36 A1/A6 — mirrors holdout_ledger)
# --------------------------------------------------------------------------

def _verify_chain(lines: list[str]) -> list[dict]:
    entries = []
    for index, line in enumerate(lines):
        entry = _loads_strict(line.encode())
        if entry.get("entry") != index + 1:
            raise MintRefusal(f"ledger entry numbering broken at line {index + 1}")
        if index > 0:
            want = hashlib.sha256(lines[index - 1].encode()).hexdigest()
            if entry.get("prev_sha256") != want:
                raise MintRefusal(
                    f"ledger hash chain broken at entry {entry['entry']} — the "
                    "history was rewritten")
        entries.append(entry)
    return entries


def _load_ledger(relpath: str, *, pinned_head_sha256: str) -> list[dict]:
    """Load a hash-chained JSONL admission ledger from git HEAD, verify its
    chain, and require its head sha256 (sha256 of the whole committed file) to
    equal the packet pin — a rolled-back or rewritten ledger refuses."""
    raw = _read_committed(relpath, allowed_prefixes=("platform/evidence/",))
    actual = hashlib.sha256(raw).hexdigest()
    if actual != str(pinned_head_sha256 or ""):
        raise MintRefusal(
            f"{relpath} head sha256 {actual[:16]} != the packet-pinned "
            f"{str(pinned_head_sha256)[:16]} — refusing a rolled-back ledger")
    lines = [l for l in raw.decode().splitlines() if l.strip()]
    return _verify_chain(lines)


def _active_entries(entries: list[dict]) -> list[dict]:
    """ADMITTED entries not later SUPERSEDED (Codex round 36 A3: an append-only
    ledger keeps stale entries; only the current admission is honoured)."""
    superseded: set[int] = set()
    for e in entries:
        if e.get("event") != "SUPERSEDED":
            continue
        # Codex round 36 impl red-team #2: a malformed/dangling SUPERSEDED
        # marker must FAIL CLOSED, not be silently dropped (which would leave
        # the entry the owner meant to retire ACTIVE).
        target = e.get("supersedes_entry")
        cites_admitted = isinstance(target, int) and any(
            t.get("entry") == target and t.get("event") == "ADMITTED"
            and t["entry"] < e["entry"] for t in entries)
        if not cites_admitted:
            raise MintRefusal(
                f"ledger entry {e.get('entry')} is a SUPERSEDED marker whose "
                f"supersedes_entry {target!r} does not cite an earlier ADMITTED "
                "entry — an un-honourable retirement fails closed")
        superseded.add(target)
    return [e for e in entries
            if e.get("event") == "ADMITTED" and e.get("entry") not in superseded]


# --------------------------------------------------------------------------
# exposure-index helpers (pure)
# --------------------------------------------------------------------------

def load_index() -> dict:
    return json.loads(INDEX.read_bytes())


def load_packet() -> dict:
    return json.loads(LIVE_MINT_PACKET.read_bytes())


def _is_pinned(src: dict) -> bool:
    return bool(src.get("key") and src.get("sha256") and src.get("s3_version_id"))


def nomination_pool_keys(index: dict, *, pinned_only: bool = True
                         ) -> dict[str, list[str]]:
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
    return sorted({src["key"] for src in index.get("pinned_sources", [])
                   if src.get("class") in _ELIGIBLE_DEV_CLASSES
                   and src.get("role") == "eval_dev_half"
                   and src.get("language") in NOMINATION_LANGUAGES
                   and src.get("key") and not _is_pinned(src)})


def sealed_pools(index: dict, *, pinned_only: bool = True) -> dict[str, dict]:
    """{key: pinned_source} for every SEALED pool."""
    return {src["key"]: src for src in index.get("pinned_sources", [])
            if src.get("class") == "SEALED" and src.get("key")
            and (not pinned_only or _is_pinned(src))}


def sealed_pool_keys(index: dict, *, pinned_only: bool = True) -> list[str]:
    return sorted(sealed_pools(index, pinned_only=pinned_only))


def candidate_pinned_pool_keys(index: dict, *, pinned_only: bool = True
                               ) -> list[str]:
    return sorted({src["key"] for src in index.get("pinned_sources", [])
                   if src.get("class") == "CANDIDATE_EXPOSED" and src.get("key")
                   and (not pinned_only or _is_pinned(src))})


def read_pool_keys(index: dict) -> set[str]:
    """The COMPLETE, EXACT set of pools the mint reads identities from: the
    nomination eval-dev pools + the pinned CANDIDATE_EXPOSED pools. SEALED
    pools are NEVER in this set — sealed exclusion is identity-authority-based."""
    return (set(sum(nomination_pool_keys(index).values(), []))
            | set(candidate_pinned_pool_keys(index)))


def veto_surface_checksums() -> set[str]:
    veto: set[str] = set()
    ds = json.loads(DEV_SELECTION.read_bytes())
    veto |= {r["audio_checksum_sha256"] for r in ds["rows"]
             if r.get("language") in VETO_LANGUAGES}
    ls = json.loads(LINGALA_SENTINEL.read_bytes())
    veto |= {r["audio_checksum_sha256"] for r in ls["rows"]}
    return veto


def committed_training_source_digests() -> dict[str, str]:
    # Codex round 36 impl red-team #3/#4-raw: read the adoption/provenance
    # records from git HEAD (the stated trust model), not a bare working-tree
    # read. Fail-closed either way, but consistent with every other committed
    # read in this module.
    out: dict[str, str] = {}
    for dataset, rel in TRAINING_SOURCE_RECORDS.items():
        doc = json.loads(_read_committed(
            rel, allowed_prefixes=("platform/evidence/",)).decode())
        digest = (doc.get("complete_raw_sha256")
                  or (doc.get("provenance") or {}).get("complete_raw_sha256"))
        out[dataset] = str(digest)
    return out


# --------------------------------------------------------------------------
# artifact STRUCTURE validation (secondary to the ledger authentication)
# --------------------------------------------------------------------------

def _validate_training_structure(artifact) -> list[str]:
    if not isinstance(artifact, dict):
        raise MintRefusal("training index is not a JSON object")
    if artifact.get("record") != TRAINING_INDEX_RECORD:
        raise MintRefusal(
            f"training index record {artifact.get('record')!r} != "
            f"{TRAINING_INDEX_RECORD!r}")
    if artifact.get("identity_key") != "audio_checksum_sha256":
        raise MintRefusal("training index identity_key must be audio_checksum_sha256")
    sources = artifact.get("source_manifests")
    if not isinstance(sources, list) or not sources:
        raise MintRefusal("training index declares no source_manifests")
    identities = artifact.get("identities")
    if not isinstance(identities, list) or not identities:
        raise MintRefusal("training index has NO identities")
    for value in identities:
        if not _is_identity(value):
            raise MintRefusal(
                f"training index carries a malformed identity {str(value)[:24]!r}")
    if len(set(identities)) != len(identities):
        raise MintRefusal("training index carries duplicate identities")
    return identities


def authenticate_training_index(artifact_bytes: bytes, *, index: dict,
                                packet: dict) -> set[str]:
    """Admit the training identity index ONLY if its canonical digest matches an
    ACTIVE owner-committed ledger entry, every binding field agrees with the
    LIVE source/index digests, the committed identity aggregate reproduces, and
    the entry's owner approval record verifies. A fabricated artifact has no
    active entry (Codex round 36 F2). RAISES MintRefusal otherwise."""
    artifact = _require_canonical(artifact_bytes, "training index")
    identities = _validate_training_structure(artifact)
    digest = hashlib.sha256(bytes(artifact_bytes)).hexdigest()

    head = str(packet.get("training_index_ledger_sha256") or "")
    if not head:
        raise MintRefusal("packet pins no training_index_ledger_sha256")
    entries = _load_ledger(TRAINING_INDEX_LEDGER, pinned_head_sha256=head)
    active = _active_entries(entries)
    # Codex round 36 impl red-team #1: mirror the sealed one-active invariant —
    # more than one active ADMITTED training index means a prior admission was
    # not SUPERSEDED; a stale/under-counting index would be replayable.
    if len(active) > 1:
        raise MintRefusal(
            "training-index ledger has more than one active ADMITTED entry — a "
            "prior admission was not SUPERSEDED; refusing an ambiguous/"
            "replayable training index")
    matched = [e for e in active if e.get("artifact_sha256") == digest]
    if not matched:
        raise MintRefusal(
            f"training index digest {digest[:16]} has NO active admission "
            "entry in the committed ledger — refusing an unadmitted artifact")
    if len(matched) > 1:
        raise MintRefusal("training index digest matches multiple active "
                          "ledger entries — ambiguous, refusing")
    entry = matched[0]

    if entry.get("exposure_index_sha256") != str(
            packet.get("exposure_index_sha256") or ""):
        raise MintRefusal("training ledger entry exposure_index_sha256 != packet")
    # the source digests must agree across artifact, the ledger entry, AND the
    # LIVE committed adoption records (Codex round 36 A3 replay guard)
    live_digests = committed_training_source_digests()
    entry_sources = entry.get("source_records") or {}
    artifact_sources = {s.get("dataset"): s.get("complete_raw_sha256")
                        for s in artifact["source_manifests"]}
    if set(entry_sources) != set(live_digests) \
            or set(artifact_sources) != set(live_digests):
        raise MintRefusal("training source set != the pinned gb9/gb8/gb3 corpora")
    for dataset, live_sha in sorted(live_digests.items()):
        entry_sha = str((entry_sources.get(dataset) or {}).get("complete_raw_sha256"))
        if entry_sha != live_sha or str(artifact_sources.get(dataset)) != live_sha:
            raise MintRefusal(
                f"training source {dataset!r} digest disagrees across "
                "artifact / ledger / live adoption record — refusing")
    unique, aggregate = _agg(identities)
    if entry.get("unique_count") != unique \
            or entry.get("identity_aggregate_sha256") != aggregate:
        raise MintRefusal(
            "training index identities do not reproduce the committed ledger "
            "aggregate/unique_count — refusing a substituted identity set")
    _verify_approval_record(entry.get("approval_record"),
                            authorizes="ADMIT_TRAINING_INDEX",
                            subject_field="artifact_sha256", subject_value=digest)
    return set(identities)


def authenticate_sealed_authorities(authorities, *, index: dict, packet: dict
                                    ) -> tuple[set[str], list[dict]]:
    """Admit sealed exclusion ONLY through the committed sealed ledger. Every
    pinned SEALED pool must have exactly one ACTIVE ledger entry with an
    allow-listed disposition and a verified owner approval record; the
    quarantined pool needs a SEPARATE quarantine clearance; BY_CONSTRUCTION_
    DISJOINT pools need a verified disjointness record and contribute NO
    identities (so an untouched seal is NEVER read); CLEARED_FOR_EXCLUSION
    pools require a supplied identity authority reproducing the committed
    aggregate. Returns (identity union, provenance). RAISES MintRefusal."""
    head = str(packet.get("sealed_exclusion_ledger_sha256") or "")
    if not head:
        raise MintRefusal("packet pins no sealed_exclusion_ledger_sha256")
    entries = _load_ledger(SEALED_EXCLUSION_LEDGER, pinned_head_sha256=head)
    active = _active_entries(entries)
    # Codex round 36 impl red-team #6: an unpinned SEALED pool cannot be
    # adjudicated (no immutable identity), so its disjointness is unprovable —
    # refuse rather than silently exclude it from completeness.
    pools = sealed_pools(index, pinned_only=False)
    unpinned = sorted(k for k, sp in pools.items() if not _is_pinned(sp))
    if unpinned:
        raise MintRefusal(
            f"the exposure index carries unpinned SEALED pools {unpinned} — an "
            "unpinned seal cannot be adjudicated; pin or remove them before a "
            "FROZEN mint")
    by_key: dict[str, dict] = {}
    for e in active:
        key = e.get("key")
        if key in by_key:
            raise MintRefusal(f"multiple active sealed ledger entries for {key!r}")
        by_key[key] = e
    missing = sorted(set(pools) - set(by_key))
    if missing:
        raise MintRefusal(
            f"sealed exclusion ledger is INCOMPLETE — no active entry for "
            f"{missing}; a partial exclusion set cannot prove disjointness")
    extra = sorted(set(by_key) - set(pools))
    if extra:
        raise MintRefusal(
            f"sealed ledger has active entries for non-index pools {extra}")

    supplied = {}
    for auth in (authorities or []):
        if not isinstance(auth, dict) or not auth.get("key"):
            raise MintRefusal("a sealed authority is malformed")
        if auth["key"] in supplied:
            raise MintRefusal(f"duplicate sealed authority for {auth['key']!r}")
        supplied[auth["key"]] = auth

    union: set[str] = set()
    provenance: list[dict] = []
    for key in sorted(pools):
        pin = pools[key]
        entry = by_key[key]
        for field in ("class", "language", "rows", "sha256", "s3_version_id"):
            if entry.get(field) != pin.get(field):
                raise MintRefusal(
                    f"sealed ledger entry for {key!r} {field}={entry.get(field)!r}"
                    f" != the live exposure-index pin {pin.get(field)!r}")
        if entry.get("exposure_index_sha256") != str(
                packet.get("exposure_index_sha256") or ""):
            raise MintRefusal(f"sealed entry {key!r} exposure_index_sha256 != packet")
        disposition = entry.get("disposition")
        if disposition not in SEALED_DISPOSITIONS:
            raise MintRefusal(
                f"sealed entry {key!r} disposition {disposition!r} is not in the "
                f"fail-closed allow-list {SEALED_DISPOSITIONS}")
        _verify_approval_record(entry.get("approval_record"),
                                authorizes="ADMIT_SEALED_EXCLUSION",
                                subject_field="key", subject_value=key)
        # the quarantined pool needs a SEPARATE clearance (distinct record_id)
        if pin.get("role") == _QUARANTINED_ROLE:
            clearance = entry.get("quarantine_clearance")
            if not isinstance(clearance, dict):
                raise MintRefusal(
                    f"quarantined pool {key!r} needs a quarantine_clearance record")
            if clearance.get("record_id") == \
                    (entry.get("approval_record") or {}).get("record_id"):
                raise MintRefusal(
                    f"quarantine clearance for {key!r} must be a SEPARATE record "
                    "from the ordinary admission")
            _verify_approval_record(clearance,
                                    authorizes="CLEAR_SEALED_QUARANTINE",
                                    subject_field="key", subject_value=key)
        if disposition == "BY_CONSTRUCTION_DISJOINT":
            if key in supplied:
                raise MintRefusal(
                    f"an identity authority was supplied for {key!r} which is "
                    "BY_CONSTRUCTION_DISJOINT — such a pool carries NO "
                    "identities; refusing the meaningless authority")
            _verify_approval_record(entry.get("disjointness_record"),
                                    authorizes="ATTEST_SEALED_DISJOINT",
                                    subject_field="key", subject_value=key)
            provenance.append({"key": key, "disposition": disposition,
                               "identities": 0})
            continue
        # CLEARED_FOR_EXCLUSION — needs a supplied identity authority
        auth = supplied.get(key)
        if auth is None:
            raise MintRefusal(
                f"pool {key!r} is CLEARED_FOR_EXCLUSION but no identity "
                "authority was supplied")
        ids = auth.get("identities")
        if not isinstance(ids, list) or not ids:
            raise MintRefusal(f"sealed authority for {key!r} has no identities")
        for value in ids:
            if not _is_identity(value):
                raise MintRefusal(
                    f"sealed authority for {key!r} malformed identity "
                    f"{str(value)[:24]!r}")
        if len(set(ids)) != len(ids):
            raise MintRefusal(f"sealed authority for {key!r} has duplicate ids")
        if pin.get("rows") is not None and len(ids) != int(pin["rows"]):
            raise MintRefusal(
                f"sealed authority for {key!r} holds {len(ids)} ids, pin rows="
                f"{pin['rows']}")
        u, agg = _agg(ids)
        if entry.get("identity_unique") != u \
                or entry.get("identity_aggregate_sha256") != agg:
            raise MintRefusal(
                f"sealed authority for {key!r} does not reproduce the committed "
                "ledger aggregate/unique — refusing a substituted identity set")
        union |= set(ids)
        provenance.append({"key": key, "disposition": disposition,
                           "identities": u})
    unused = sorted(set(supplied) - set(pools))
    if unused:
        raise MintRefusal(f"sealed authorities supplied for non-pool keys {unused}")
    return union, sorted(provenance, key=lambda p: p["key"])


# --------------------------------------------------------------------------
# the split computation (pure) + manifest assembly
# --------------------------------------------------------------------------

def _pool_pins(index: dict, keys) -> list[dict]:
    keyset = set(keys)
    return sorted(
        [{"key": s["key"], "class": s["class"], "language": s.get("language"),
          "role": s.get("role"), "rows": s.get("rows"), "sha256": s.get("sha256"),
          "s3_version_id": s.get("s3_version_id")}
         for s in index.get("pinned_sources", []) if s.get("key") in keyset],
        key=lambda p: p["key"] or "")


def mint_phase_a_split(index: dict, pool_identities: dict[str, list[str]],
                       *, packet: dict | None = None, training_index=None,
                       sealed_authorities=None,
                       status: str = "MINTED_OFFLINE_FIXTURE") -> dict:
    """Mint the Phase-A nomination split. PURE apart from reading the committed
    ledgers/records (through the monkeypatchable ``_read_committed``) when
    authenticating supplied evidence. Refuses on any leak, duplicate,
    incompleteness, empty split, veto collision, or — for FROZEN — missing/
    unauthenticated training index or sealed authorities."""
    if status not in ALLOWED_STATUSES:
        raise MintRefusal(f"unknown mint status {status!r} — fail closed")
    if status == "FROZEN":
        if training_index is None or sealed_authorities is None or packet is None:
            raise MintRefusal(
                "a FROZEN mint requires the authenticated training index, the "
                "sealed authorities, AND the packet")
    if (training_index is not None or sealed_authorities is not None) \
            and packet is None:
        raise MintRefusal("authenticating supplied evidence requires the packet")

    training = (authenticate_training_index(training_index, index=index,
                                            packet=packet)
                if training_index is not None else None)
    if sealed_authorities is not None:
        sealed, sealed_provenance = authenticate_sealed_authorities(
            sealed_authorities, index=index, packet=packet)
    else:
        sealed, sealed_provenance = None, None

    required = read_pool_keys(index)
    supplied = set(pool_identities)
    missing = sorted(required - supplied)
    if missing:
        raise MintRefusal(f"pool identities are INCOMPLETE — missing {missing}")
    extra = sorted(supplied - required)
    if extra:
        raise MintRefusal(
            f"pool identities include {extra} NOT in the reviewed read set")

    candidate = set(used_union_checksums())
    for key in candidate_pinned_pool_keys(index):
        rows = list(pool_identities[key])
        for value in rows:
            if not _is_identity(value):
                raise MintRefusal(
                    f"candidate pool {key!r} malformed identity {str(value)[:24]!r}")
        if len(rows) != len(set(rows)):
            raise MintRefusal(f"candidate pool {key!r} has duplicate identities")
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
                f"nomination language {lang!r} has no fully-PINNED eligible pool")
        raw = [c for key in keys for c in pool_identities[key]]
        for value in raw:
            if not _is_identity(value):
                raise MintRefusal(
                    f"nomination pool for {lang!r} malformed identity "
                    f"{str(value)[:24]!r}")
        if len(raw) != len(set(raw)):
            raise MintRefusal(
                f"nomination pools for {lang!r} contain duplicate identities")
        raw_unique = sorted(raw)
        veto_here = sorted(set(raw_unique) & veto)
        if veto_here:
            raise MintRefusal(
                f"nomination pool for {lang!r} contains {len(veto_here)} veto "
                "identities — nomination and veto surfaces must be DISJOINT")
        eligible = [c for c in raw_unique if c not in excluded_all]
        if not eligible:
            raise MintRefusal(f"nomination language {lang!r} split is EMPTY")
        count, agg = _agg(eligible)
        split[lang] = eligible
        per_language[lang] = {
            "pool_keys": keys, "raw_unique": len(raw_unique),
            "removed_candidate": len(set(raw_unique) & candidate),
            "removed_sealed": (len(set(raw_unique) & sealed)
                               if sealed is not None else "UNVERIFIED"),
            "removed_training": (len(set(raw_unique) & training)
                                 if training is not None else "UNVERIFIED"),
            "eligible": count, "split_aggregate_sha256": agg}

    all_split_list = [c for lang in NOMINATION_LANGUAGES for c in split[lang]]
    all_split = sorted(set(all_split_list))
    if len(all_split_list) != len(all_split):
        dupes = sorted({c for c in all_split_list if all_split_list.count(c) > 1})
        raise MintRefusal(
            f"{len(dupes)} identities appear in MORE THAN ONE language split")

    overlap = {
        "candidate_exposed": len(set(all_split) & candidate),
        "sealed": (len(set(all_split) & sealed) if sealed is not None
                   else "UNVERIFIED"),
        "veto_surface": len(set(all_split) & veto),
        "training_exposed": (len(set(all_split) & training) if training is not None
                             else "UNVERIFIED")}
    numeric = {k: v for k, v in overlap.items() if isinstance(v, int)}
    if any(numeric.values()):
        raise MintRefusal(
            f"the minted split is NOT disjoint from the excluded classes "
            f"{overlap} — refusing to emit a leaking split")

    cand_n, cand_agg = _agg(candidate)
    veto_n, veto_agg = _agg(veto)
    split_n, split_agg = _agg(all_split)
    provenance: dict = {
        "candidate_exposed_unique": cand_n,
        "candidate_exposed_aggregate_sha256": cand_agg,
        "veto_surface_unique": veto_n, "veto_surface_aggregate_sha256": veto_agg}
    if sealed is not None:
        s_n, s_agg = _agg(sealed)
        provenance["sealed_unique"] = s_n
        provenance["sealed_aggregate_sha256"] = s_agg
        provenance["sealed_authorities"] = sealed_provenance
    else:
        provenance["sealed_unique"] = "UNVERIFIED"
    if training is not None:
        t_n, t_agg = _agg(training)
        provenance["training_exposed_unique"] = t_n
        provenance["training_exposed_aggregate_sha256"] = t_agg
    else:
        provenance["training_exposed_unique"] = "UNVERIFIED"

    return {
        "record": SPLIT_RECORD, "status": status,
        "phase": "A_held_out_development_nomination",
        "protocol": "B5-UNIVERSAL-ARM2-KD-COMPARISON-PROTOCOL-2026-001",
        "identity_key": "audio_checksum_sha256",
        "generator": "scripts/mint_arm2_nomination_split.py",
        "eligibility_rule": "audio_checksum_sha256 NOT in CANDIDATE_EXPOSED, "
                            "NOT in TRAINING_EXPOSED, NOT in SEALED",
        "nomination_languages": list(NOMINATION_LANGUAGES),
        "veto_languages": list(VETO_LANGUAGES),
        "split": {lang: split[lang] for lang in NOMINATION_LANGUAGES},
        "per_language": per_language,
        "split_identity": {"unique": split_n, "aggregate_sha256": split_agg},
        "aggregate_overlap_counts": overlap,
        "exclusion_provenance": provenance,
        "pool_pins": _pool_pins(index, read_pool_keys(index)),
        "sealed_pools_never_read": sealed_pool_keys(index, pinned_only=False),
        "unpinned_excluded_pools": unpinned_eligible_pools(index),
        "NEVER_INCLUDES": "sealed rows, transcript text or audio — identities only",
    }


def verify_frozen_manifest(manifest: dict, index: dict,
                           pool_identities: dict[str, list[str]], *,
                           packet: dict | None = None, training_index=None,
                           sealed_authorities=None) -> list[str]:
    """Regenerate the entire canonical manifest from the same inputs and compare
    it COMPLETELY — a forged field anywhere fails verification."""
    try:
        expected = mint_phase_a_split(
            index, pool_identities, packet=packet, training_index=training_index,
            sealed_authorities=sealed_authorities,
            status=str(manifest.get("status") or ""))
    except (MintRefusal, LiveMintForbidden) as exc:
        return [f"the canonical manifest cannot be regenerated: {exc}"]
    if _canon(expected) == _canon(manifest):
        return []
    return [f"{k!r} does not match the regenerated canonical manifest"
            for k in sorted(set(expected) | set(manifest))
            if _canon(expected.get(k)) != _canon(manifest.get(k))] or \
        ["manifest differs from the regenerated canonical manifest"]


# --------------------------------------------------------------------------
# LIVE path — caller identity, per-object verification, ledger self-load
# --------------------------------------------------------------------------

def validate_caller_identity(caller, *, account: str, role_name: str) -> None:
    """Assert the EXACT STS account AND role: the ARN must parse to exactly
    arn:aws:sts::<account>:assumed-role/<role_name>/<session>. A same-named role
    in another account, a substring match, or a user ARN all refuse."""
    if not isinstance(caller, dict):
        raise MintRefusal("no STS caller identity was supplied to the trusted path")
    got_account = str(caller.get("Account") or "")
    arn = str(caller.get("Arn") or "")
    if got_account != account:
        raise MintRefusal(f"caller account {got_account!r} != pinned {account!r}")
    parts = arn.split(":")
    ok = (len(parts) == 6 and parts[0] == "arn" and parts[1] == "aws"
          and parts[2] == "sts" and parts[4] == account
          and parts[5].startswith(f"assumed-role/{role_name}/")
          and len(parts[5].split("/")) == 3 and parts[5].split("/")[2])
    if not ok:
        raise MintRefusal(
            f"caller ARN {arn!r} is not exactly "
            f"arn:aws:sts::{account}:assumed-role/{role_name}/<session>")


_PIN_FIELDS = ("class", "role", "language", "rows", "sha256", "s3_version_id")


def _verify_fetched_object(pin: dict, fetched: dict, *, expected_kms: str,
                           key: str) -> list[str]:
    body = fetched.get("body")
    if not isinstance(body, (bytes, bytearray)):
        raise MintRefusal(f"{key}: reader returned no raw bytes")
    actual_sha = hashlib.sha256(bytes(body)).hexdigest()
    if actual_sha != pin.get("sha256"):
        raise MintRefusal(
            f"{key}: downloaded bytes hash to {actual_sha[:16]}, pin declares "
            f"{str(pin.get('sha256'))[:16]} — refusing unverified bytes")
    if str(fetched.get("version_id") or "") != str(pin.get("s3_version_id")):
        raise MintRefusal(f"{key}: S3 VersionId echo != pin")
    if str(fetched.get("kms_key_arn") or "") != expected_kms:
        raise MintRefusal(f"{key}: object KMS key != the pinned CMK")
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
                f"{key}: malformed audio_checksum_sha256 {str(value)[:24]!r}")
        row_lang = row.get("language")
        if row_lang is not None and pin.get("language") is not None \
                and str(row_lang).strip().lower() != str(pin["language"]).lower():
            raise MintRefusal(
                f"{key}: row language {row_lang!r} != pinned {pin.get('language')!r}")
        identities.append(value)
    expected_rows = pin.get("rows")
    if expected_rows is not None and len(identities) != int(expected_rows):
        raise MintRefusal(
            f"{key}: manifest holds {len(identities)} rows, pin declares "
            f"{expected_rows}")
    return identities


def _running_script_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def live_mint(packet: dict, *, index_bytes: bytes, s3_reader, caller_identity,
              training_index_bytes: bytes, sealed_authorities,
              commit_sha: str = "") -> dict:
    """Read ONLY the 7 nomination + pinned-candidate identity manifests through
    the injected ``s3_reader`` and mint the FROZEN split. Loads the committed
    admission ledgers ITSELF (through the module-level ``_read_committed``) —
    NEVER from a caller argument. Asserts the caller identity, binds the
    exposure index by canonical bytes + full-field pins, verifies every fetched
    object, authenticates the training index and sealed authorities against the
    committed ledgers, and returns a hash-bound result manifest."""
    if s3_reader is None:
        raise LiveMintForbidden(
            "no s3_reader was injected — this harness has NO default AWS client")
    account = str((packet.get("aws") or {}).get("account") or "")
    role_name = str(((packet.get("minimal_read_role") or {})
                     .get("role_name")) or "")
    if not account or not role_name:
        raise MintRefusal("the live-mint packet pins no account/role identity")
    validate_caller_identity(caller_identity, account=account, role_name=role_name)

    expected_index_sha = str(packet.get("exposure_index_sha256") or "")
    if not expected_index_sha:
        raise MintRefusal("the packet pins no exposure_index_sha256")
    actual_index_sha = hashlib.sha256(bytes(index_bytes)).hexdigest()
    if actual_index_sha != expected_index_sha:
        raise MintRefusal(
            f"exposure index bytes hash to {actual_index_sha[:16]}, packet pins "
            f"{expected_index_sha[:16]} — refusing a tampered index")
    index = json.loads(bytes(index_bytes).decode())
    expected_kms = str(((packet.get("aws") or {}).get("kms_key")) or "")
    if not expected_kms:
        raise MintRefusal("the packet pins no KMS key")

    packet_pins = {p.get("key"): p for p in packet.get("pinned_objects", [])}
    wanted = read_pool_keys(index)          # NEVER includes sealed pools
    for key in sorted(sealed_pool_keys(index)):
        if key in packet_pins:
            raise MintRefusal(
                f"the packet pins sealed object {key!r} for fetching — the mint "
                "NEVER reads sealed manifests")
    index_pins = {}
    for src in index.get("pinned_sources", []):
        if src.get("key") in wanted and src["key"] not in index_pins:
            index_pins[src["key"]] = src
    pool_identities: dict[str, list[str]] = {}
    for key in sorted(wanted):
        pin = index_pins.get(key)
        pkt_pin = packet_pins.get(key)
        if pkt_pin is None:
            raise MintRefusal(f"{key}: the packet does not pin this read object")
        for field in _PIN_FIELDS:
            if _canon(pkt_pin.get(field)) != _canon(pin.get(field)):
                raise MintRefusal(
                    f"{key}: packet pin {field}={pkt_pin.get(field)!r} disagrees "
                    f"with the exposure index {pin.get(field)!r}")
        fetched = s3_reader(key=key, s3_version_id=pin.get("s3_version_id"))
        if not isinstance(fetched, dict):
            raise MintRefusal(f"{key}: reader must return body + echoed metadata")
        pool_identities[key] = _verify_fetched_object(
            pin, fetched, expected_kms=expected_kms, key=key)

    manifest = mint_phase_a_split(
        index, pool_identities, packet=packet,
        training_index=training_index_bytes,
        sealed_authorities=sealed_authorities, status="FROZEN")

    # A4/A9: bind the output inside the same governed run
    training_digest = hashlib.sha256(bytes(training_index_bytes)).hexdigest()
    result = {
        "record": "B5-UNIVERSAL-ARM2-NOMINATION-SPLIT-RESULT-2026-001",
        "manifest": manifest,
        "provenance": {
            "exposure_index_sha256": expected_index_sha,
            "training_index_artifact_sha256": training_digest,
            "training_index_ledger_sha256":
                str(packet.get("training_index_ledger_sha256")),
            "sealed_exclusion_ledger_sha256":
                str(packet.get("sealed_exclusion_ledger_sha256")),
            "sealed_dispositions":
                manifest["exclusion_provenance"].get("sealed_authorities"),
            "caller_arn": str(caller_identity.get("Arn")),
            "commit_sha": str(commit_sha),
            "running_script_sha256": _running_script_sha256(),
        },
    }
    result["result_sha256"] = hashlib.sha256(_canon(
        {k: v for k, v in result.items() if k != "result_sha256"})).hexdigest()
    return result


def _dump(obj) -> str:
    return _canon(obj).decode() + "\n"


def main(argv=None) -> int:
    import os
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live", action="store_true",
                    help="perform the LIVE S3-reading mint — only inside the "
                         "owner-approved protected mint workflow, under the "
                         "dedicated mint role")
    ap.add_argument("--training-index", default="training-identity-index.json",
                    help="path to the produced training identity index artifact")
    ap.add_argument("--sealed-authorities", default="",
                    help="path to the CLEARED_FOR_EXCLUSION identity authorities "
                         "JSON (a list); omit when every sealed pool is "
                         "BY_CONSTRUCTION_DISJOINT")
    ap.add_argument("--out", default="nomination-split-result.json")
    args = ap.parse_args(argv)
    if not args.live:
        raise SystemExit(
            "offline invocation mints nothing: the pure core is driven by "
            "committed fixtures in tests/test_arm2_nomination_mint.py; the "
            "FROZEN mint is the owner-authorized `--live` step in the mint "
            "workflow")

    # Codex round 36 impl red-team #3: the trust-root packet is read from git
    # HEAD (the one file previously read from the working tree), consistent with
    # the exposure index and both ledgers.
    packet = _loads_strict(_read_committed(
        "platform/decisions/B5-UNIVERSAL-ARM2-NOMINATION-LIVE-MINT-PACKET-2026-001.json",
        allowed_prefixes=("platform/decisions/",)))
    account = str((packet.get("aws") or {}).get("account") or "")
    role_name = str(((packet.get("minimal_read_role") or {})
                     .get("role_name")) or "")
    bucket = str((packet.get("aws") or {}).get("bucket") or "")
    try:
        import boto3  # lazy: never imported on the offline/test path
    except ImportError as exc:
        raise SystemExit(
            "boto3 is unavailable — the live mint runs only inside the approved "
            f"protected mint workflow with the dedicated role; refusing ({exc})")
    sts = boto3.client("sts")
    ident = sts.get_caller_identity()
    caller = {"Account": ident.get("Account"), "Arn": ident.get("Arn")}
    try:
        validate_caller_identity(caller, account=account, role_name=role_name)
    except MintRefusal as exc:
        raise SystemExit(f"refusing the live mint: {exc}")

    index_bytes = _read_committed(
        "platform/manifests/B5-UNIVERSAL-ARM2-EXPOSURE-INDEX-2026-001.json",
        allowed_prefixes=("platform/manifests/",))
    training_bytes = Path(args.training_index).read_bytes()
    sealed = (json.loads(Path(args.sealed_authorities).read_bytes())
              if args.sealed_authorities else [])
    s3 = boto3.client("s3")

    def reader(key: str, s3_version_id: str) -> dict:
        resp = s3.get_object(Bucket=bucket, Key=key, VersionId=s3_version_id,
                             ExpectedBucketOwner=account)
        return {"body": resp["Body"].read(), "version_id": resp.get("VersionId"),
                "kms_key_arn": resp.get("SSEKMSKeyId")}

    # This is the REAL executable path (Codex round 36 F4). It FAILS CLOSED
    # today because the admission ledgers ship PENDING/empty (no active entry),
    # but through the full live_mint chain, not an unconditional exit.
    try:
        result = live_mint(packet, index_bytes=index_bytes, s3_reader=reader,
                           caller_identity=caller,
                           training_index_bytes=training_bytes,
                           sealed_authorities=sealed,
                           commit_sha=os.environ.get("GITHUB_SHA", ""))
    except MintRefusal as exc:
        raise SystemExit(f"FROZEN mint refused (fail-closed): {exc}")
    Path(args.out).write_bytes(_canon(result) + b"\n")
    print(json.dumps({"status": "NOMINATION_SPLIT_FROZEN",
                      "result_sha256": result["result_sha256"],
                      "out": args.out}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
