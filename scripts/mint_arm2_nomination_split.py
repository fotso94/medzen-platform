"""Deterministic Arm-2 Phase-A nomination-split MINTING harness.

Phase-A held-out development nomination (protocol
B5-UNIVERSAL-ARM2-KD-COMPARISON-PROTOCOL-2026-001, phase
``phase_A_held_out_development_nomination_now``): mint, from the eval-dev pools,
a FROZEN per-language nomination split of the four NOMINATION-GATED languages
(english / french / pidgin / swahili). A row is eligible IFF its
``audio_checksum_sha256`` is NOT in CANDIDATE_EXPOSED, NOT in TRAINING_EXPOSED
and NOT in SEALED (BASE_EXPOSED rows REMAIN eligible for Phase A — nomination
compares candidates, so base-blindness is not required). The mint commits the
row-level proof (the frozen manifest of eligible IDENTITIES + per-pool pins) and
the AGGREGATE overlap counts against every excluded class and the directional-
veto surfaces — all of which MUST be zero, or the mint refuses.

TWO MODES, FAIL-CLOSED TO OFFLINE
---------------------------------
* The PURE core (:func:`mint_phase_a_split`) operates ONLY on in-memory pool
  identities the caller passes in. It imports NO AWS SDK and touches NO network,
  so the class rules and the candidate / veto disjointness are proven with
  committed fixtures and AWS is IMPOSSIBLE in the test path.
* The LIVE path (:func:`live_mint`) reads the S3-pinned identity manifests, but
  ONLY through an ``s3_reader`` callable the caller injects AND only when an
  explicit owner-authorization token is supplied; without BOTH it refuses BEFORE
  importing or touching any AWS SDK. It extracts ONLY ``audio_checksum_sha256``
  and emits ONLY the frozen nomination manifest + aggregate overlap counts —
  NEVER sealed rows, text or audio.

The eventual live mint is a SEPARATE, independently-reviewed, owner-authorized
step; its exact S3 keys, VersionIds, hashes, KMS key and minimal read role are
pinned in
platform/decisions/B5-UNIVERSAL-ARM2-NOMINATION-LIVE-MINT-PACKET-2026-001.json.
This module MINTS NOTHING and reads NO S3 on import.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
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

# the token the LIVE path demands (owner supplies it out of band, per the
# live-mint packet); its mere value is not a secret — the gate is the human
# authorization + independent review the packet requires, not this string.
LIVE_AUTHORIZATION_TOKEN = "OWNER-AUTHORIZED-ARM2-NOMINATION-LIVE-MINT"
LIVE_AUTHORIZATION_ENV = "MEDZEN_NOMINATION_LIVE_MINT"


class MintRefusal(RuntimeError):
    """Fail-closed: the nomination split could not be minted with a proof that
    it is disjoint from every excluded class and the veto surfaces."""


class LiveMintForbidden(RuntimeError):
    """The live (S3-reading) mint was invoked without an injected reader AND an
    explicit owner authorization — refused before any AWS SDK is touched."""


# --------------------------------------------------------------------------
# pure helpers (no AWS, no network)
# --------------------------------------------------------------------------

def load_index() -> dict:
    return json.loads(INDEX.read_bytes())


def _agg(checksums) -> tuple[int, str]:
    """(unique count, sha256 over the sorted unique set) — the same identity
    aggregation the exposure-index generator uses."""
    uniq = sorted(set(checksums))
    return len(uniq), hashlib.sha256("\n".join(uniq).encode()).hexdigest()


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


def _excluded_class_sets(index: dict,
                         pool_identities: dict[str, list[str]]) -> dict:
    """Assemble the per-row exclusion sets from the in-repo CANDIDATE_EXPOSED
    union PLUS whatever pinned pool identities the caller materialized:

      * candidate = in-repo used-union  ∪  supplied pinned CANDIDATE_EXPOSED pools
      * sealed    = supplied SEALED pools
      * training  = (no per-row identities exist; excluded structurally — the
                     training corpora are pinned only by content digest)
    """
    candidate = set(used_union_checksums())
    for key in candidate_pinned_pool_keys(index):
        if key in pool_identities:
            candidate |= set(pool_identities[key])
    sealed: set[str] = set()
    for key in sealed_pool_keys(index):
        if key in pool_identities:
            sealed |= set(pool_identities[key])
    return {"candidate": candidate, "sealed": sealed,
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
                       *, status: str = "MINTED_OFFLINE_FIXTURE") -> dict:
    """Mint the FROZEN Phase-A nomination split from the supplied pool
    identities. PURE: no AWS, no network. Refuses (MintRefusal) on any leak,
    any empty language split, or any nomination/veto surface collision.

    ``pool_identities`` maps each S3 manifest key to its list of
    ``audio_checksum_sha256`` values. Tests supply committed fixtures; the live
    path supplies the fetched identities. ``status`` marks the artifact's
    provenance (offline fixture vs a live FROZEN mint) so the two can never be
    confused."""
    excl = _excluded_class_sets(index, pool_identities)
    excluded_all = excl["candidate"] | excl["sealed"]
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
        raw_unique = sorted(set(raw))
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
            "eligible": count,
            "split_aggregate_sha256": agg,
        }

    # FAIL-CLOSED disjointness guard: a frozen split can NEVER leak. Recompute
    # the overlap of the FINAL split against every excluded class + the veto
    # surface; a non-zero count is an internal bug and refuses the mint.
    all_split = sorted({c for rows in split.values() for c in rows})
    overlap = {
        "candidate_exposed": len(set(all_split) & excl["candidate"]),
        "sealed": len(set(all_split) & excl["sealed"]),
        "veto_surface": len(set(all_split) & excl["veto"]),
        "training_exposed": 0,
    }
    if any(overlap.values()):
        raise MintRefusal(
            f"the minted split is NOT disjoint from the excluded classes "
            f"{overlap} — refusing to emit a leaking nomination split")

    cand_n, cand_agg = _agg(excl["candidate"])
    sealed_n, sealed_agg = _agg(excl["sealed"])
    veto_n, veto_agg = _agg(excl["veto"])
    split_n, split_agg = _agg(all_split)
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
            "training_exposed_note": "no per-row identities exist (corpora "
                "pinned by content digest); excluded structurally",
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


def verify_frozen_manifest(manifest: dict, index: dict) -> list[str]:
    """Independently re-check a minted manifest: every aggregate overlap count
    is zero, each per-language aggregate sha reproduces from the listed
    identities, and no nomination identity intersects the veto surface. Returns
    a list of failures (empty == verified)."""
    failures: list[str] = []
    overlaps = manifest.get("aggregate_overlap_counts", {})
    for name, value in overlaps.items():
        if value != 0:
            failures.append(f"aggregate_overlap_counts.{name} == {value} (!= 0)")
    veto = veto_surface_checksums()
    for lang in NOMINATION_LANGUAGES:
        rows = manifest.get("split", {}).get(lang)
        if not rows:
            failures.append(f"split[{lang}] is empty/absent")
            continue
        _, agg = _agg(rows)
        declared = (manifest.get("per_language", {}).get(lang, {})
                    .get("split_aggregate_sha256"))
        if declared != agg:
            failures.append(f"split[{lang}] aggregate sha {agg[:12]} != declared "
                            f"{str(declared)[:12]}")
        if set(rows) & veto:
            failures.append(f"split[{lang}] intersects the veto surface")
    return failures


# --------------------------------------------------------------------------
# LIVE path — refuses before touching any AWS SDK unless explicitly authorized
# --------------------------------------------------------------------------

def live_mint(index: dict, *, s3_reader=None, authorization: str = "") -> dict:
    """Read the S3-pinned identity manifests through the injected ``s3_reader``
    and mint the FROZEN split. Refuses (LiveMintForbidden) BEFORE importing or
    touching any AWS SDK unless BOTH an explicit owner authorization token
    (also mirrored in the MEDZEN_NOMINATION_LIVE_MINT env var) AND a reader are
    supplied. Emits ONLY identities + aggregate counts — never sealed rows,
    text or audio. NOT executed here; the authorized step runs it noninteractively
    per the live-mint packet."""
    if authorization != LIVE_AUTHORIZATION_TOKEN or \
            os.environ.get(LIVE_AUTHORIZATION_ENV) != LIVE_AUTHORIZATION_TOKEN:
        raise LiveMintForbidden(
            "live nomination mint requires an explicit owner authorization "
            "token (argument AND the MEDZEN_NOMINATION_LIVE_MINT env var) — "
            "run only the independently-reviewed, owner-authorized step in the "
            "live-mint packet; refusing")
    if s3_reader is None:
        raise LiveMintForbidden(
            "no s3_reader was injected — this harness has NO default AWS client; "
            "the authorized step must pass a minimal read-only reader")
    # fetch ONLY the pinned identity manifests, keeping ONLY audio checksums
    wanted = (set(sum(nomination_pool_keys(index).values(), []))
              | set(sealed_pool_keys(index))
              | set(candidate_pinned_pool_keys(index)))
    pool_identities: dict[str, list[str]] = {}
    for src in index.get("pinned_sources", []):
        key = src.get("key")
        if key not in wanted or key in pool_identities:
            continue
        raw = s3_reader(key=key, s3_version_id=src.get("s3_version_id"),
                        sha256=src.get("sha256"))
        checksums = []
        for line in raw.decode().splitlines():
            line = line.strip()
            if line:
                # IDENTITIES ONLY — never retain text/audio fields
                checksums.append(json.loads(line)["audio_checksum_sha256"])
        pool_identities[key] = checksums
    return mint_phase_a_split(index, pool_identities, status="FROZEN")


def _dump(obj) -> str:
    return json.dumps(obj, indent=1, sort_keys=True) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live", action="store_true",
                    help="perform the LIVE S3-reading mint (refuses without an "
                         "injected reader + explicit owner authorization)")
    ap.add_argument("--authorization", default="",
                    help="owner authorization token (see the live-mint packet)")
    args = ap.parse_args(argv)
    if not args.live:
        raise SystemExit(
            "offline invocation mints nothing: the pure core "
            "(mint_phase_a_split) is driven by committed fixtures in "
            "tests/test_arm2_nomination_mint.py; the FROZEN mint is the "
            "owner-authorized `--live` step in the live-mint packet")
    # NOTE: no default AWS client is constructed here. The authorized step wires
    # a minimal read-only reader per the packet and passes it to live_mint().
    raise SystemExit(
        "refusing: `--live` has no built-in AWS client by design; run the "
        "authorized noninteractive step that injects the minimal read-only "
        "reader described in the live-mint packet")


if __name__ == "__main__":
    raise SystemExit(main())
