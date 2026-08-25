"""Deterministic Arm-2 Phase-A nomination-split MINTING harness.

Phase-A held-out development nomination (protocol
B5-UNIVERSAL-ARM2-KD-COMPARISON-PROTOCOL-2026-001, phase
``phase_A_held_out_development_nomination_now``): mint, from the eval-dev pools,
a FROZEN per-language nomination split of the four NOMINATION-GATED languages
(english / french / pidgin / swahili). A row is eligible IFF its
``audio_checksum_sha256`` is NOT in CANDIDATE_EXPOSED, NOT in TRAINING_EXPOSED
and NOT in SEALED (BASE_EXPOSED rows REMAIN eligible for Phase A — nomination
compares candidates, so base-blindness is not required).

CODEX ROUND 34 CORRECTIONS (all five findings reproduced + fixed):

1. TRAINING overlap is never claimed as zero without an actual anti-join.
   ``mint_phase_a_split`` takes ``training_identities``; absent, the manifest
   reports ``training_exposed: "UNVERIFIED"`` and a FROZEN mint REFUSES. The
   per-row training identity index is produced by the owner-approved protected
   job (the training manifests' rows carry ``audio_checksum_sha256``; the job
   verifies them against the adoption records' ``complete_raw_sha256`` and
   emits identities only) — see the live-mint packet.
2. The TRUSTED path verifies every downloaded object: sha256 of the actual
   bytes, the echoed S3 VersionId, the echoed KMS key, the exact pinned row
   count, per-row JSON schema (64-hex lowercase identity), and the row language
   when present. Fabricated bytes can never become a FROZEN split.
3. ``verify_frozen_manifest`` RECOMPUTES everything from the same inputs the
   mint had — candidate / sealed / veto / training overlap, per-language
   eligibility, per-language + split aggregates, within- and cross-language
   duplicates — and never trusts a declared count.
4. Duplicate identities WITHIN a language's pools or ACROSS language splits
   refuse the mint (and fail the verifier).
5. There is NO authorization token. The live mint is authorized by the
   owner-approved protected GitHub environment (``arm2-nomination-mint``, owner
   as required reviewer — the ``verify_protected_environments`` pattern) whose
   OIDC trust is the ONLY principal that can assume the dedicated read-only
   role; the CLI additionally asserts the caller identity IS that role before
   reading anything. Configuration cannot impersonate authorization.

TWO MODES, FAIL-CLOSED TO OFFLINE
---------------------------------
* The PURE core (:func:`mint_phase_a_split`) operates ONLY on in-memory pool
  identities the caller passes in. It imports NO AWS SDK and touches NO
  network, so the class rules and the candidate / veto disjointness are proven
  with committed fixtures and AWS is IMPOSSIBLE in the test path.
* The LIVE path (:func:`live_mint`) reads the S3-pinned identity manifests, but
  ONLY through an ``s3_reader`` callable the caller injects; without one it
  refuses BEFORE importing or touching any AWS SDK. It extracts ONLY
  ``audio_checksum_sha256`` and emits ONLY the frozen nomination manifest +
  aggregate overlap counts — NEVER sealed rows, text or audio. Raw manifest
  bytes exist only in the protected job's memory, never on a workstation.

The eventual live mint is a SEPARATE, independently-reviewed, owner-authorized
step; its exact S3 keys, VersionIds, hashes, KMS key, minimal read role and
protected-environment authorization are pinned in
platform/decisions/B5-UNIVERSAL-ARM2-NOMINATION-LIVE-MINT-PACKET-2026-001.json.
This module MINTS NOTHING and reads NO S3 on import.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

# pure, in-repo identity helpers (NO AWS): the in-repo CANDIDATE_EXPOSED union
# and the source-record readers are reused verbatim so the two artifacts stay
# consistent and machine-derived.
from build_arm2_exposure_index import (DEV_SELECTION, LINGALA_SENTINEL, ROOT,
                                       used_union_checksums)

INDEX = ROOT / "platform/manifests/B5-UNIVERSAL-ARM2-EXPOSURE-INDEX-2026-001.json"
LIVE_MINT_PACKET = (ROOT / "platform/decisions/"
                    "B5-UNIVERSAL-ARM2-NOMINATION-LIVE-MINT-PACKET-2026-001.json")
SPLIT_RECORD = "B5-UNIVERSAL-ARM2-NOMINATION-SPLIT-2026-001"

# the four nomination-gated languages (protocol phase_A nomination_gated_languages)
NOMINATION_LANGUAGES = ("english", "french", "pidgin", "swahili")
# the three directional-veto languages — a DISJOINT surface used ONLY for the
# safety veto; never part of any nomination split (protocol
# nomination_data_rules.directional_veto_surface_exemption)
VETO_LANGUAGES = ("ewe", "kinyarwanda", "lingala")
# eligible Phase-A source halves (candidate-blind; base-blindness NOT required)
_ELIGIBLE_DEV_CLASSES = ("BASE_EXPOSED", "BASE_BLIND_CANDIDATE_ELIGIBLE")

_HEX = frozenset("0123456789abcdef")


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
    sha256 + S3 VersionId. An eligible pool lacking any of these cannot be a
    nomination source in a reproducible frozen mint (it is surfaced by
    :func:`unpinned_eligible_pools`, never silently dropped)."""
    return bool(src.get("key") and src.get("sha256") and src.get("s3_version_id"))


def nomination_pool_keys(index: dict, *, pinned_only: bool = True
                         ) -> dict[str, list[str]]:
    """{language: [S3 manifest keys]} of the Phase-A-eligible eval-dev halves
    for the four nomination-gated languages (class in BASE_EXPOSED /
    BASE_BLIND_CANDIDATE_ELIGIBLE, role eval_dev_half). With ``pinned_only``
    (the default, and what the mint uses) only fully-pinned pools are returned —
    a frozen mint draws exclusively from immutable sources."""
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
    """S3-pinned CANDIDATE_EXPOSED pools (e.g. the kinyarwanda cv17 dev-selection)
    whose per-row identities live off-repo and must be supplied to exclude."""
    return sorted({src["key"] for src in index.get("pinned_sources", [])
                   if src.get("class") == "CANDIDATE_EXPOSED" and src.get("key")
                   and (not pinned_only or _is_pinned(src))})


def veto_surface_checksums() -> set[str]:
    """The directional-veto identities, ALL in-repo: the 386-row lingala
    sentinel PLUS the kinyarwanda + ewe dev-selection rows. Nomination and veto
    surfaces are disjoint; the mint proves nomination identities never intersect
    these."""
    veto: set[str] = set()
    ds = json.loads(DEV_SELECTION.read_bytes())
    veto |= {r["audio_checksum_sha256"] for r in ds["rows"]
             if r.get("language") in VETO_LANGUAGES}
    ls = json.loads(LINGALA_SENTINEL.read_bytes())
    veto |= {r["audio_checksum_sha256"] for r in ls["rows"]}
    return veto


def _excluded_class_sets(index: dict, pool_identities: dict[str, list[str]],
                         training_identities=None) -> dict:
    """Assemble the per-row exclusion sets from the in-repo CANDIDATE_EXPOSED
    union PLUS whatever pinned pool identities the caller materialized:

      * candidate = in-repo used-union  ∪  supplied pinned CANDIDATE_EXPOSED pools
      * sealed    = supplied SEALED pools
      * training  = the supplied per-row training identity index, or None —
                    Codex round 34 finding 1: absent identities mean the
                    training overlap is UNVERIFIED, never zero.
    """
    candidate = set(used_union_checksums())
    for key in candidate_pinned_pool_keys(index):
        if key in pool_identities:
            candidate |= set(pool_identities[key])
    sealed: set[str] = set()
    for key in sealed_pool_keys(index):
        if key in pool_identities:
            sealed |= set(pool_identities[key])
    training = (set(training_identities)
                if training_identities is not None else None)
    return {"candidate": candidate, "sealed": sealed, "training": training,
            "veto": veto_surface_checksums()}


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
                       *, training_identities=None,
                       status: str = "MINTED_OFFLINE_FIXTURE") -> dict:
    """Mint the FROZEN Phase-A nomination split from the supplied pool
    identities. PURE: no AWS, no network. Refuses (MintRefusal) on any leak,
    any duplicate identity within or across languages, any empty language
    split, or any nomination/veto surface collision.

    ``pool_identities`` maps each S3 manifest key to its list of
    ``audio_checksum_sha256`` values. ``training_identities`` is the per-row
    training identity index (gb9/gb8/gb3); WITHOUT it the training overlap is
    reported as the string ``"UNVERIFIED"`` — never zero — and a FROZEN mint
    refuses (Codex round 34 finding 1). ``status`` marks the artifact's
    provenance (offline fixture vs a live FROZEN mint) so the two can never be
    confused."""
    if status == "FROZEN" and training_identities is None:
        raise MintRefusal(
            "a FROZEN mint requires the per-row training identity index — "
            "without it the training overlap is UNVERIFIED and the split "
            "cannot be declared leak-free (Codex round 34 finding 1)")
    excl = _excluded_class_sets(index, pool_identities, training_identities)
    excluded_all = set(excl["candidate"]) | set(excl["sealed"])
    if excl["training"] is not None:
        excluded_all |= excl["training"]
    pools_by_lang = nomination_pool_keys(index)

    split: dict[str, list[str]] = {}
    per_language: dict[str, dict] = {}
    used_pool_keys: set[str] = set()
    for lang in NOMINATION_LANGUAGES:
        keys = pools_by_lang.get(lang, [])
        if not keys:
            raise MintRefusal(
                f"nomination language {lang!r} has no fully-PINNED eligible "
                "eval-dev pool in the exposure index — a frozen mint needs an "
                "immutable (key+sha256+VersionId) source")
        raw: list[str] = []
        for key in keys:
            if key not in pool_identities:
                raise MintRefusal(
                    f"identities for nomination pool {key!r} were not provided "
                    "— cannot mint an unproven split")
            raw += list(pool_identities[key])
            used_pool_keys.add(key)
        for value in raw:
            if not _is_identity(value):
                raise MintRefusal(
                    f"nomination pool for {lang!r} carries a malformed "
                    f"identity {str(value)[:24]!r} — identities must be "
                    "64-hex lowercase sha256")
        # Codex round 34 finding 4: duplicates are refused, never silently
        # deduplicated — a repeated identity inside one language's pools means
        # a corrupted or double-counted manifest.
        if len(raw) != len(set(raw)):
            raise MintRefusal(
                f"nomination pools for {lang!r} contain duplicate identities "
                "within the language — refusing a double-counted split")
        raw_unique = sorted(raw)
        # a nomination pool must never carry a veto-language identity
        veto_here = sorted(set(raw_unique) & excl["veto"])
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
            "removed_candidate": len(set(raw_unique) & excl["candidate"]),
            "removed_sealed": len(set(raw_unique) & excl["sealed"]),
            "removed_training": (len(set(raw_unique) & excl["training"])
                                 if excl["training"] is not None
                                 else "UNVERIFIED"),
            "eligible": count,
            "split_aggregate_sha256": agg,
        }

    # Codex round 34 finding 4: language splits must be MUTUALLY disjoint — the
    # same audio row must never be scored as two languages.
    all_split_list = [c for lang in NOMINATION_LANGUAGES for c in split[lang]]
    all_split = sorted(set(all_split_list))
    if len(all_split_list) != len(all_split):
        dupes = sorted({c for c in all_split_list
                        if all_split_list.count(c) > 1})
        raise MintRefusal(
            f"{len(dupes)} identities appear in MORE THAN ONE language split "
            "— cross-language duplicates are refused")

    # FAIL-CLOSED disjointness guard: a frozen split can NEVER leak. Recompute
    # the overlap of the FINAL split against every excluded class + the veto
    # surface; a non-zero count is an internal bug and refuses the mint.
    # Training overlap is a NUMBER only when the identity index was supplied;
    # otherwise it is the string "UNVERIFIED" (Codex round 34 finding 1).
    overlap = {
        "candidate_exposed": len(set(all_split) & excl["candidate"]),
        "sealed": len(set(all_split) & excl["sealed"]),
        "veto_surface": len(set(all_split) & excl["veto"]),
        "training_exposed": (len(set(all_split) & excl["training"])
                             if excl["training"] is not None
                             else "UNVERIFIED"),
    }
    numeric = {k: v for k, v in overlap.items() if isinstance(v, int)}
    if any(numeric.values()):
        raise MintRefusal(
            f"the minted split is NOT disjoint from the excluded classes "
            f"{overlap} — refusing to emit a leaking nomination split")

    cand_n, cand_agg = _agg(excl["candidate"])
    sealed_n, sealed_agg = _agg(excl["sealed"])
    veto_n, veto_agg = _agg(excl["veto"])
    split_n, split_agg = _agg(all_split)
    if excl["training"] is not None:
        train_n, train_agg = _agg(excl["training"])
        training_provenance = {"training_exposed_unique": train_n,
                               "training_exposed_aggregate_sha256": train_agg}
    else:
        training_provenance = {
            "training_exposed_unique": "UNVERIFIED",
            "training_exposed_note": "NO per-row training identity index was "
                "supplied — the training overlap is UNVERIFIED (not zero). A "
                "FROZEN mint refuses in this state; the identity index is "
                "produced by the owner-approved protected job (see the "
                "live-mint packet)."}
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
        "exclusion_provenance": {
            "candidate_exposed_unique": cand_n,
            "candidate_exposed_aggregate_sha256": cand_agg,
            "sealed_unique": sealed_n,
            "sealed_aggregate_sha256": sealed_agg,
            "veto_surface_unique": veto_n,
            "veto_surface_aggregate_sha256": veto_agg,
            **training_provenance,
        },
        "pool_pins": _pool_pins(index, used_pool_keys | set(sealed_pool_keys(index))
                                | set(candidate_pinned_pool_keys(index))),
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
                           *, training_identities=None) -> list[str]:
    """Independently RE-DERIVE the split from the same inputs the mint had and
    compare against the manifest — never trusting a declared count (Codex round
    34 finding 3). Recomputes candidate / sealed / veto / training overlap,
    per-language eligibility and aggregates, the split identity, and the
    within- and cross-language duplicate rules. Returns a list of failures
    (empty == verified)."""
    failures: list[str] = []
    excl = _excluded_class_sets(index, pool_identities, training_identities)
    excluded_all = set(excl["candidate"]) | set(excl["sealed"])
    if excl["training"] is not None:
        excluded_all |= excl["training"]
    pools_by_lang = nomination_pool_keys(index)

    seen: dict[str, str] = {}
    for lang in NOMINATION_LANGUAGES:
        rows = manifest.get("split", {}).get(lang)
        if not rows:
            failures.append(f"split[{lang}] is empty/absent")
            continue
        for value in rows:
            if not _is_identity(value):
                failures.append(f"split[{lang}] carries a malformed identity "
                                f"{str(value)[:24]!r}")
        if len(rows) != len(set(rows)):
            failures.append(f"split[{lang}] contains duplicate identities")
        for value in rows:
            if value in seen and seen[value] != lang:
                failures.append(
                    f"identity {value[:12]} appears in split[{seen[value]}] "
                    f"AND split[{lang}] — cross-language duplicate")
            seen.setdefault(value, lang)

        # RECOMPUTED overlap of the declared split — never the declared counts
        declared_set = set(rows)
        if declared_set & excl["candidate"]:
            failures.append(
                f"split[{lang}] intersects CANDIDATE_EXPOSED "
                f"({len(declared_set & excl['candidate'])} rows) — recomputed, "
                "regardless of any declared zero")
        if declared_set & excl["sealed"]:
            failures.append(
                f"split[{lang}] intersects SEALED "
                f"({len(declared_set & excl['sealed'])} rows) — recomputed")
        if declared_set & excl["veto"]:
            failures.append(f"split[{lang}] intersects the veto surface")
        if excl["training"] is not None and declared_set & excl["training"]:
            failures.append(
                f"split[{lang}] intersects TRAINING_EXPOSED "
                f"({len(declared_set & excl['training'])} rows) — recomputed")

        # RE-DERIVE the expected eligible set from the pools and require the
        # declared split to BE it (same rule, independent execution)
        keys = pools_by_lang.get(lang, [])
        if all(key in pool_identities for key in keys) and keys:
            raw = [c for key in keys for c in pool_identities[key]]
            expected = sorted(c for c in set(raw) if c not in excluded_all)
            if sorted(rows) != expected:
                failures.append(
                    f"split[{lang}] does not equal the re-derived eligible set "
                    f"({len(rows)} declared vs {len(expected)} re-derived)")
        else:
            failures.append(
                f"cannot re-derive split[{lang}]: pool identities missing for "
                f"{[k for k in keys if k not in pool_identities]}")

        _, agg = _agg(rows)
        declared_agg = (manifest.get("per_language", {}).get(lang, {})
                        .get("split_aggregate_sha256"))
        if declared_agg != agg:
            failures.append(f"split[{lang}] aggregate sha {agg[:12]} != "
                            f"declared {str(declared_agg)[:12]}")

    # split identity recompute
    all_split = sorted({c for lang in NOMINATION_LANGUAGES
                        for c in manifest.get("split", {}).get(lang, [])})
    n, agg = _agg(all_split)
    declared_identity = manifest.get("split_identity", {})
    if declared_identity.get("unique") != n \
            or declared_identity.get("aggregate_sha256") != agg:
        failures.append("split_identity does not reproduce from the split")

    # training claim consistency (Codex round 34 finding 1): a claimed NUMBER
    # is only verifiable with the identity index; a claim of zero without one
    # is exactly the bug this round fixed.
    declared_training = (manifest.get("aggregate_overlap_counts", {})
                         .get("training_exposed"))
    if excl["training"] is None:
        if declared_training != "UNVERIFIED":
            failures.append(
                f"manifest claims training_exposed={declared_training!r} but "
                "NO training identity index is available to verify it — a "
                "numeric training claim without the index is unverifiable")
        if manifest.get("status") == "FROZEN":
            failures.append("a FROZEN manifest cannot have UNVERIFIED "
                            "training overlap")
    else:
        recomputed = len(set(all_split) & excl["training"])
        if declared_training != recomputed:
            failures.append(
                f"training_exposed declared {declared_training!r} != "
                f"recomputed {recomputed}")
    return failures


# --------------------------------------------------------------------------
# LIVE path — refuses before touching any AWS SDK; verifies every byte it uses
# --------------------------------------------------------------------------

def _verify_fetched_object(pin: dict, fetched: dict, *, expected_kms: str,
                           key: str) -> list[str]:
    """Trusted-path integrity checks on ONE fetched identity manifest (Codex
    round 34 finding 2): actual-bytes sha256, echoed VersionId, echoed KMS key,
    exact pinned row count, per-row schema and language. Returns the extracted
    identities via ``fetched['_identities']`` on success."""
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


def live_mint(index: dict, packet: dict, *, s3_reader,
              training_identities=None) -> dict:
    """Read the S3-pinned identity manifests through the injected ``s3_reader``
    and mint the FROZEN split. Refuses (LiveMintForbidden) BEFORE importing or
    touching any AWS SDK when no reader is supplied — there is NO default AWS
    client and NO authorization token (Codex round 34 finding 5: authorization
    is the owner-approved protected environment whose OIDC trust alone can
    assume the read role; the CLI asserts the caller identity IS that role).

    The reader contract is ``s3_reader(key=..., s3_version_id=...) ->
    {"body": bytes, "version_id": str, "kms_key_arn": str}``; every fetched
    object is verified against its pin (sha256 of the ACTUAL bytes, echoed
    VersionId, echoed KMS key, exact row count, per-row schema + language)
    before a single identity is used (finding 2). Emits ONLY identities +
    aggregate counts — never sealed rows, text or audio; raw bytes live only in
    this process's memory."""
    if s3_reader is None:
        raise LiveMintForbidden(
            "no s3_reader was injected — this harness has NO default AWS client; "
            "the authorized protected job must pass a minimal read-only reader")
    if training_identities is None:
        raise MintRefusal(
            "the live FROZEN mint requires the per-row training identity index "
            "(Codex round 34 finding 1) — produce it via the owner-approved "
            "protected job first")
    expected_kms = str(((packet.get("aws") or {}).get("kms_key")) or "")
    if not expected_kms:
        raise MintRefusal("the live-mint packet pins no KMS key")
    packet_pins = {p.get("key"): p for p in packet.get("pinned_objects", [])}

    wanted = (set(sum(nomination_pool_keys(index).values(), []))
              | set(sealed_pool_keys(index))
              | set(candidate_pinned_pool_keys(index)))
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
        for field in ("sha256", "s3_version_id"):
            if str(pkt_pin.get(field)) != str(pin.get(field)):
                raise MintRefusal(
                    f"{key}: packet pin {field} disagrees with the exposure "
                    "index — refusing a torn pin pair")
        fetched = s3_reader(key=key, s3_version_id=pin.get("s3_version_id"))
        if not isinstance(fetched, dict):
            raise MintRefusal(
                f"{key}: reader must return the body WITH its echoed "
                "VersionId + KMS metadata (finding 2) — got bare "
                f"{type(fetched).__name__}")
        pool_identities[key] = _verify_fetched_object(
            pin, fetched, expected_kms=expected_kms, key=key)
    return mint_phase_a_split(index, pool_identities,
                              training_identities=training_identities,
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
    # The live path runs ONLY inside the owner-approved protected workflow
    # (environment arm2-nomination-mint), whose OIDC trust is the sole
    # principal able to assume the dedicated read role. Assert that the
    # CALLER IDENTITY IS that role before reading anything — configuration
    # (env vars, tokens) cannot impersonate this (Codex round 34 finding 5).
    packet = load_packet()
    role_arn = str(((packet.get("minimal_read_role") or {})
                    .get("role_arn")) or "")
    try:
        import boto3  # lazy: never imported on the offline/test path
    except ImportError as exc:
        raise SystemExit(
            "boto3 is unavailable — the live mint runs only inside the "
            "approved protected workflow with the dedicated role; refusing "
            f"({exc})")
    sts = boto3.client("sts")
    caller = str(sts.get_caller_identity().get("Arn") or "")
    role_name = role_arn.rsplit("/", 1)[-1]
    if not role_name or f":assumed-role/{role_name}/" not in caller:
        raise SystemExit(
            f"caller identity {caller!r} is NOT the dedicated mint role "
            f"{role_arn!r} — the live mint refuses under any other principal")
    # Role identity verified. The FROZEN mint still requires the per-row
    # training identity index (finding 1), whose protected production job is
    # specified in the packet but not yet built — fail closed until it exists.
    # (The activation workflow will wire the role-scoped reader — get_object
    # with VersionId + ExpectedBucketOwner, echoing VersionId + SSEKMSKeyId —
    # and pass the index into live_mint.)
    raise SystemExit(
        "caller identity verified as the dedicated mint role, but the FROZEN "
        "mint requires the per-row training identity index (Codex round 34 "
        "finding 1) — produce it via the owner-approved protected job per the "
        "live-mint packet; refusing until then")


if __name__ == "__main__":
    raise SystemExit(main())
