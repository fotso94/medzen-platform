#!/usr/bin/env python3
"""A3 dataset validator. Runs in CI; a violation fails the build.

Nine checks, in the order they catch problems most cheaply:

  1  schema        every required field, correct type and range
  2  sample rate   16 kHz mono (44.1 kHz makes WER look catastrophic for no reason)
  3  duration      1.0s <= d <= 30.0s
  4  text          non-empty verbatim AND normalized
  5  language      codes exist in the registry (skipped if --registry omitted)
  6  speaker leak  speaker_id sets disjoint across splits
  7  session leak  session_id sets disjoint across splits
  8  near-dup      MinHash/LSH clusters must not straddle splits
  9  provenance    consent_id + license_policy + dataset_release on every row

Checks 6-8 are the ones that silently inflate every number you report. They
cannot be caught by a schema, which is why this script exists.

    python scripts/validate_manifest.py curated/pidgin/v1/manifest.jsonl
    python scripts/validate_manifest.py m.jsonl --registry registry/languages
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "schemas" / "manifest.schema.json"

NEAR_DUP_THRESHOLD = 0.80      # Jaccard over char 5-grams
MINHASH_PERMS = 64
LSH_BANDS = 16                 # 16 bands x 4 rows


# --------------------------------------------------------------------------- #
# minimal MinHash + LSH — no external dependency, deterministic
# --------------------------------------------------------------------------- #
def shingles(text: str, k: int = 5) -> set[str]:
    t = re.sub(r"\s+", " ", text.strip().lower())
    return {t[i:i + k] for i in range(max(1, len(t) - k + 1))}


def _h(data: str, seed: int) -> int:
    return int.from_bytes(
        hashlib.blake2b(data.encode(), digest_size=8,
                        salt=seed.to_bytes(8, "big")).digest(), "big")


def signature(sh: set[str]) -> tuple[int, ...]:
    if not sh:
        return tuple([0] * MINHASH_PERMS)
    return tuple(min(_h(s, i) for s in sh) for i in range(MINHASH_PERMS))


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if (a or b) else 0.0


def near_duplicate_pairs(rows: list[dict]) -> list[tuple[int, int, float]]:
    """LSH candidate generation, then exact Jaccard on candidates only."""
    sh = [shingles(r.get("text_normalized", "")) for r in rows]
    sigs = [signature(s) for s in sh]
    rows_per_band = MINHASH_PERMS // LSH_BANDS

    buckets: dict[tuple, list[int]] = collections.defaultdict(list)
    for idx, sig in enumerate(sigs):
        for b in range(LSH_BANDS):
            band = sig[b * rows_per_band:(b + 1) * rows_per_band]
            buckets[(b, band)].append(idx)

    seen, out = set(), []
    for members in buckets.values():
        if len(members) < 2:
            continue
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                pair = (min(a, b), max(a, b))
                if pair in seen:
                    continue
                seen.add(pair)
                j = jaccard(sh[a], sh[b])
                if j >= NEAR_DUP_THRESHOLD:
                    out.append((pair[0], pair[1], j))
    return out


# --------------------------------------------------------------------------- #
def load_schema_validator():
    try:
        import jsonschema
    except ImportError:
        return None
    return jsonschema.Draft202012Validator(json.loads(SCHEMA.read_text()))


def registry_languages(path: Path | None) -> set[str] | None:
    if not path or not path.exists():
        return None
    import yaml
    codes = {"mixed"}
    for f in path.glob("*.yaml"):
        d = yaml.safe_load(f.read_text()) or {}
        for k in ("alias", "iso_code"):
            if d.get(k):
                codes.add(str(d[k]))
    return codes or None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest", type=Path)
    ap.add_argument("--registry", type=Path, default=None,
                    help="registry/languages dir; language check skipped if absent")
    ap.add_argument("--max-errors", type=int, default=25)
    a = ap.parse_args()

    errors: list[str] = []
    warns: list[str] = []
    rows: list[dict] = []

    # ---- parse ------------------------------------------------------------
    for n, line in enumerate(a.manifest.read_text().splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as e:
            errors.append(f"line {n}: invalid JSON — {e}")
    if not rows:
        print("FAIL  manifest is empty or unparseable")
        return 1

    # ---- 1 schema ---------------------------------------------------------
    v = load_schema_validator()
    if v is None:
        warns.append("jsonschema not installed — check 1 (schema) skipped")
    else:
        for i, r in enumerate(rows, 1):
            for err in sorted(v.iter_errors(r), key=lambda e: e.path):
                loc = "/".join(str(p) for p in err.path) or "(root)"
                errors.append(f"row {i} schema [{loc}]: {err.message}")

    # ---- 2,3,4,9 per-row invariants (explicit, not just schema) -----------
    for i, r in enumerate(rows, 1):
        if r.get("sample_rate") != 16000:
            errors.append(f"row {i}: sample_rate={r.get('sample_rate')} — must be 16000")
        if r.get("channels") != 1:
            errors.append(f"row {i}: channels={r.get('channels')} — must be mono")
        d = r.get("duration_s")
        if not isinstance(d, (int, float)) or not (1.0 <= d <= 30.0):
            errors.append(f"row {i}: duration_s={d} outside [1.0, 30.0]")
        for f in ("text_verbatim", "text_normalized"):
            if not str(r.get(f, "")).strip():
                errors.append(f"row {i}: {f} is empty")
        for f in ("consent_id", "license_policy", "dataset_release", "source_id"):
            if not str(r.get(f, "")).strip():
                errors.append(f"row {i}: provenance field {f} missing")

    # ---- 5 language codes -------------------------------------------------
    langs = registry_languages(a.registry)
    if langs is None:
        warns.append("no registry supplied — check 5 (language codes) skipped")
    else:
        for i, r in enumerate(rows, 1):
            if r.get("primary_language") not in langs:
                errors.append(f"row {i}: primary_language "
                              f"'{r.get('primary_language')}' not in registry {sorted(langs)}")
            for s in r.get("segments") or []:
                if s.get("language") not in langs:
                    errors.append(f"row {i}: segment language '{s.get('language')}' "
                                  f"not in registry")

    # ---- 6,7 leakage ------------------------------------------------------
    # Which identity must be disjoint depends on what the corpus has to
    # generalise to. Near-duplicate TEXT (check 8) is forbidden either way.
    strategies = {r.get("split_strategy") for r in rows if r.get("split_strategy")}
    if len(strategies) > 1:
        errors.append(f"mixed split_strategy in one manifest: {sorted(strategies)} "
                      f"— a corpus must be split one way")
    strategy = next(iter(strategies), "speaker_disjoint")
    # Under text_disjoint the SAME speakers deliberately appear on both sides —
    # that is the design (one voice, unseen sentences). No identity check
    # applies; check 8 (near-duplicate text) carries the whole invariant.
    leak_fields = ("speaker_id", "session_id") if strategy == "speaker_disjoint" else ()
    if strategy == "text_disjoint":
        warns.append("split_strategy=text_disjoint: speaker/session overlap is BY "
                     "DESIGN (parallel TTS corpus). Text-disjointness (check 8) is "
                     "the invariant that matters here.")
    for field in leak_fields:
        by_split: dict[str, set[str]] = collections.defaultdict(set)
        for r in rows:
            if r.get("split") and r.get(field):
                by_split[r["split"]].add(r[field])
        splits = sorted(by_split)
        for i, s1 in enumerate(splits):
            for s2 in splits[i + 1:]:
                overlap = by_split[s1] & by_split[s2]
                if overlap:
                    sample = sorted(overlap)[:5]
                    errors.append(
                        f"LEAK: {len(overlap)} {field}(s) appear in both "
                        f"'{s1}' and '{s2}' — e.g. {sample}. Splits must be "
                        f"disjoint by {field} or every metric is inflated.")

    # ---- 8 near-duplicates straddling splits ------------------------------
    for a_i, b_i, j in near_duplicate_pairs(rows):
        sa, sb = rows[a_i].get("split"), rows[b_i].get("split")
        if sa and sb and sa != sb:
            errors.append(
                f"NEAR-DUP across splits (Jaccard {j:.2f}): row {a_i+1} [{sa}] "
                f"~ row {b_i+1} [{sb}] — '{rows[a_i].get('text_normalized','')[:45]}'")

    # ---- report -----------------------------------------------------------
    print(f"manifest: {a.manifest}  rows: {len(rows)}")
    counts = collections.Counter(r.get("split") for r in rows)
    print(f"splits:   {dict(counts)}")
    langc = collections.Counter(r.get("primary_language") for r in rows)
    print(f"languages:{dict(langc)}")
    print()
    for w in warns:
        print(f"  WARN  {w}")
    for e in errors[:a.max_errors]:
        print(f"  FAIL  {e}")
    if len(errors) > a.max_errors:
        print(f"  ... {len(errors) - a.max_errors} more")

    if errors:
        print(f"\n{len(errors)} violation(s) — manifest REJECTED")
        return 1
    print(f"\nOK — {len(rows)} rows pass all A3 checks"
          f"{f' ({len(warns)} check(s) skipped)' if warns else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
