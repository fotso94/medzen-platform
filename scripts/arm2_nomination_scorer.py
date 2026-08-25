"""Arm-2 Phase-A nomination scorer — the COMPLETE executable decision pipeline
(Codex stage-1 review 2026-08-25 finding 3: committed BEFORE any candidate
results exist, so no implementation choice can be made after seeing results).

Single authority implemented: the `statistical_procedure` of
platform/decisions/B5-UNIVERSAL-ARM2-KD-COMPARISON-PROTOCOL-2026-001.json
(rev 007). This module binds together, with every input sha-pinned:

  - the FROZEN nomination split
    (platform/manifests/B5-UNIVERSAL-ARM2-NOMINATION-SPLIT-2026-001.json,
    artifact sha fccadc462e2097e619eac495e047412486b449bdc6add97286b37fdbe6cb968e)
  - per-arm per-row edit-distance receipts for EVERY scored model
    (base teacher, arm1, KD_CONTROL, H0, H1..H4 at Stage 1;
     base, arm1, KD_CONTROL, H0 and the finalist at Stage 2)
  - the three directional-veto surfaces (lingala 386-row sentinel;
    kinyarwanda + ewe 60-row dev-selection slices)
  - a speaker-cluster map for the nomination languages
  - WER + paired cluster bootstrap (B >= 10000, one shared resample plan)
  - the three safety vetoes (RAW upper CI, never Holm-relaxed)
  - the Holm positive gate (scripts/arm2_holm.py) — Stage-1
    candidate_qualifies over the COMPLETE 24-test family; Stage-2 qualifies()
  - the deterministic tie-break and the NO_RECIPE_QUALIFIES terminal states

Determinism: the bootstrap uses `random.Random(derive_seed(master_seed,
surface))` per language surface; the SAME cluster resample plan is applied to
every arm (paired), so re-running with identical inputs reproduces the result
byte-for-byte. No wall-clock, no environment reads.

WER metric: sum(edit_distance) / sum(reference_words) per language, from the
per-row receipts (absolute WER points).

This scorer only COMPUTES the nomination decision; it launches nothing and
holds no credentials. Sealed evaluation, promotion and deployment are outside
its scope entirely.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path

from arm2_holm import candidate_qualifies, holm_reject, qualifies

FROZEN_SPLIT_PATH = (
    "platform/manifests/B5-UNIVERSAL-ARM2-NOMINATION-SPLIT-2026-001.json")
FROZEN_SPLIT_SHA256 = (
    "fccadc462e2097e619eac495e047412486b449bdc6add97286b37fdbe6cb968e")

NOMINATION_LANGUAGES = ("english", "french", "pidgin", "swahili")
PRESERVATION_LANGUAGES = ("english", "french", "swahili")   # macro components
VETO_SURFACES = ("lingala", "kinyarwanda", "ewe")
# reference arm per veto surface (protocol safety_vetoes.statistic)
VETO_REFERENCE = {"lingala": "base", "kinyarwanda": "arm1", "ewe": "arm1"}
CANDIDATES = ("H1", "H2", "H3", "H4")
COMPARATORS = ("base", "arm1", "KD_CONTROL", "H0")
# fixed per-candidate test order inside every Holm family (predeclared)
TEST_ORDER = ("preservation_english", "preservation_french",
              "preservation_swahili", "pidgin_retention",
              "beat_KD_CONTROL", "beat_H0")
NONINF_MARGIN = 0.005          # +0.5pp non-inferiority margin
VETO_MARGIN = 0.01             # +1.0pp raw veto margin
ALPHA = 0.05
MIN_B = 10000


class ScorerRefusal(SystemExit):
    """Any malformed, incomplete or inconsistent input refuses loudly."""


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def derive_seed(master_seed: int, surface: str) -> int:
    """Deterministic per-surface bootstrap seed: sha256(master:surface)."""
    digest = hashlib.sha256(f"{master_seed}:{surface}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


# --------------------------------------------------------------------------
# input loading (everything sha-pinned + coverage-validated)
# --------------------------------------------------------------------------

def load_frozen_split(path: Path, expected_sha: str = FROZEN_SPLIT_SHA256
                      ) -> dict[str, list[str]]:
    raw = path.read_bytes()
    actual = _sha256_bytes(raw)
    if actual != expected_sha:
        raise ScorerRefusal(
            f"frozen split {path} hashes to {actual[:16]}, expected "
            f"{expected_sha[:16]} — refusing a substituted split")
    doc = json.loads(raw)
    split = (doc.get("manifest") or {}).get("split") or {}
    out: dict[str, list[str]] = {}
    for language in NOMINATION_LANGUAGES:
        rows = split.get(language)
        if not isinstance(rows, list) or not rows:
            raise ScorerRefusal(f"frozen split has no rows for {language!r}")
        if len(set(rows)) != len(rows):
            raise ScorerRefusal(f"frozen split rows for {language!r} duplicate")
        out[language] = [str(r) for r in rows]
    return out


def load_veto_surface(path: Path, *, expected_sha: str, language: str
                      ) -> list[str]:
    """A veto surface is a committed JSONL manifest; identity per row =
    audio_checksum_sha256. The caller pins its sha."""
    raw = path.read_bytes()
    actual = _sha256_bytes(raw)
    if actual != expected_sha:
        raise ScorerRefusal(
            f"veto surface {path} ({language}) hashes to {actual[:16]}, "
            f"expected {expected_sha[:16]}")
    rows = []
    for line in raw.decode().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        identity = str(row.get("audio_checksum_sha256") or "")
        if len(identity) != 64:
            raise ScorerRefusal(f"veto row in {path} lacks a 64-hex identity")
        rows.append(identity)
    if not rows or len(set(rows)) != len(rows):
        raise ScorerRefusal(f"veto surface {path} empty or duplicated")
    return rows


def load_receipts(path: Path) -> tuple[dict[str, tuple[int, int]], str]:
    """One arm's per-row receipts: JSON {rows: [{audio_checksum_sha256,
    edit_distance, ref_words}, ...]} (language-agnostic — coverage is checked
    against each surface). Returns ({identity: (edits, ref_words)}, sha256)."""
    raw = path.read_bytes()
    doc = json.loads(raw)
    rows = doc.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ScorerRefusal(f"receipts {path} carry no rows")
    out: dict[str, tuple[int, int]] = {}
    for row in rows:
        identity = str(row.get("audio_checksum_sha256") or "")
        if len(identity) != 64:
            raise ScorerRefusal(f"receipts {path}: malformed identity")
        if identity in out:
            raise ScorerRefusal(f"receipts {path}: duplicate row {identity[:12]}")
        edits = row.get("edit_distance")
        words = row.get("ref_words")
        if not isinstance(edits, int) or isinstance(edits, bool) or edits < 0:
            raise ScorerRefusal(f"receipts {path}: bad edit_distance {edits!r}")
        if not isinstance(words, int) or isinstance(words, bool) or words < 0:
            raise ScorerRefusal(f"receipts {path}: bad ref_words {words!r}")
        out[identity] = (edits, words)
    return out, _sha256_bytes(raw)


def load_cluster_map(path: Path) -> tuple[dict[str, str], str]:
    """{audio_checksum_sha256: cluster_id} for the nomination languages
    (speaker clustering). Veto surfaces NEVER use this — they cluster by row
    (protocol safety_vetoes.statistic)."""
    raw = path.read_bytes()
    doc = json.loads(raw)
    mapping = doc.get("clusters")
    if not isinstance(mapping, dict) or not mapping:
        raise ScorerRefusal(f"cluster map {path} carries no clusters")
    out = {}
    for identity, cluster in mapping.items():
        if len(str(identity)) != 64 or not str(cluster).strip():
            raise ScorerRefusal(f"cluster map {path}: malformed entry")
        out[str(identity)] = str(cluster)
    return out, _sha256_bytes(raw)


def surface_rows(identities: list[str], receipts: dict[str, tuple[int, int]],
                 *, arm: str, surface: str) -> list[tuple[int, int]]:
    """Exact-coverage extraction: every surface identity must be scored by the
    arm exactly once; missing rows refuse (a partial score cannot gate)."""
    missing = [i for i in identities if i not in receipts]
    if missing:
        raise ScorerRefusal(
            f"arm {arm!r} receipts are missing {len(missing)} row(s) of the "
            f"{surface!r} surface (first: {missing[0][:12]}) — refusing a "
            "partial scoring")
    return [receipts[i] for i in identities]


# --------------------------------------------------------------------------
# WER + paired cluster bootstrap
# --------------------------------------------------------------------------

def corpus_wer(rows: list[tuple[int, int]]) -> float:
    edits = sum(r[0] for r in rows)
    words = sum(r[1] for r in rows)
    if words <= 0:
        raise ScorerRefusal("surface has zero reference words")
    return edits / words


def build_clusters(identities: list[str], cluster_map: dict[str, str] | None
                   ) -> list[list[int]]:
    """Group surface row INDICES into clusters. cluster_map None => row-level
    (each row its own cluster: the veto surfaces + placeholder pools)."""
    if cluster_map is None:
        return [[i] for i in range(len(identities))]
    grouped: dict[str, list[int]] = {}
    for index, identity in enumerate(identities):
        cluster = cluster_map.get(identity)
        if cluster is None:
            raise ScorerRefusal(
                f"identity {identity[:12]} has no speaker cluster — the "
                "cluster map must cover every nomination row")
        grouped.setdefault(cluster, []).append(index)
    return [grouped[k] for k in sorted(grouped)]


def resample_plan(n_clusters: int, B: int, seed: int) -> list[list[int]]:
    """The SHARED plan: B resamples of n_clusters cluster-indices drawn with
    replacement. Applying ONE plan to every arm is what makes the bootstrap
    PAIRED (identical resampled clusters across compared arms)."""
    rng = random.Random(seed)
    return [[rng.randrange(n_clusters) for _ in range(n_clusters)]
            for _ in range(B)]


def bootstrap_wers(rows_by_arm: dict[str, list[tuple[int, int]]],
                   clusters: list[list[int]], plan: list[list[int]]
                   ) -> dict[str, list[float]]:
    """WER_b per arm under the shared plan. Precomputes per-cluster (edits,
    words) so each resample is a simple sum."""
    per_cluster: dict[str, list[tuple[int, int]]] = {}
    for arm, rows in rows_by_arm.items():
        per_cluster[arm] = [
            (sum(rows[i][0] for i in cluster),
             sum(rows[i][1] for i in cluster)) for cluster in clusters]
    out: dict[str, list[float]] = {arm: [] for arm in rows_by_arm}
    for draw in plan:
        for arm, cl in per_cluster.items():
            edits = sum(cl[j][0] for j in draw)
            words = sum(cl[j][1] for j in draw)
            out[arm].append(edits / words if words > 0 else float("inf"))
    return out


def one_sided_p_and_ci(samples: list[float], *, direction: str,
                       threshold: float) -> dict:
    """direction 'ge': p = mean[ x >= threshold ], CI = 95th pct (upper);
    direction 'le': p = mean[ x <= threshold ], CI = 5th pct (lower)."""
    n = len(samples)
    ordered = sorted(samples)
    if direction == "ge":
        p = sum(1 for x in samples if x >= threshold) / n
        ci = ordered[min(n - 1, int(0.95 * n))]
        return {"p": p, "upper_ci95": ci}
    if direction == "le":
        p = sum(1 for x in samples if x <= threshold) / n
        ci = ordered[max(0, int(0.05 * n) - 1)] if n >= 20 else ordered[0]
        return {"p": p, "lower_ci95": ci}
    raise ScorerRefusal(f"unknown direction {direction!r}")


# --------------------------------------------------------------------------
# the decision pipeline
# --------------------------------------------------------------------------

def candidate_tests(candidate: str, wers: dict[str, dict[str, list[float]]]
                    ) -> dict[str, dict]:
    """The six positive tests for one candidate, in TEST_ORDER, from the
    per-surface paired bootstrap samples `wers[surface][arm][b]`."""
    tests: dict[str, dict] = {}
    for language in PRESERVATION_LANGUAGES:
        cand = wers[language][candidate]
        base = wers[language]["base"]
        diffs = [c - b for c, b in zip(cand, base)]
        tests[f"preservation_{language}"] = one_sided_p_and_ci(
            diffs, direction="ge", threshold=NONINF_MARGIN)
    cand = wers["pidgin"][candidate]
    arm1 = wers["pidgin"]["arm1"]
    diffs = [c - a for c, a in zip(cand, arm1)]
    tests["pidgin_retention"] = one_sided_p_and_ci(
        diffs, direction="ge", threshold=NONINF_MARGIN)
    for comparator, name in (("KD_CONTROL", "beat_KD_CONTROL"),
                             ("H0", "beat_H0")):
        reds = []
        for b in range(len(cand)):
            macro_cand = sum(wers[lang][candidate][b]
                             for lang in PRESERVATION_LANGUAGES) / 3.0
            macro_comp = sum(wers[lang][comparator][b]
                             for lang in PRESERVATION_LANGUAGES) / 3.0
            reds.append(macro_comp - macro_cand)
        tests[name] = one_sided_p_and_ci(reds, direction="le", threshold=0.0)
    return tests


def candidate_vetoes(candidate: str,
                     veto_wers: dict[str, dict[str, list[float]]]
                     ) -> dict[str, dict]:
    """The three RAW safety vetoes (never Holm-relaxed): upper_CI95 of
    (cand - reference) > 0.01 => VETO."""
    out: dict[str, dict] = {}
    for surface in VETO_SURFACES:
        reference = VETO_REFERENCE[surface]
        cand = veto_wers[surface][candidate]
        ref = veto_wers[surface][reference]
        diffs = [c - r for c, r in zip(cand, ref)]
        summary = one_sided_p_and_ci(diffs, direction="ge",
                                     threshold=VETO_MARGIN)
        summary["vetoed"] = summary["upper_ci95"] > VETO_MARGIN
        summary["reference"] = reference
        out[surface] = summary
    return out


def stage1_decision(wers, veto_wers, *, alphas: dict[str, float]) -> dict:
    """The complete Stage-1 decision: 24-test Holm family + raw vetoes +
    deterministic tie-break, or NO_RECIPE_QUALIFIES."""
    family: list[float] = []
    per_candidate: dict[str, dict] = {}
    for candidate in CANDIDATES:            # fixed, predeclared family order
        tests = candidate_tests(candidate, wers)
        vetoes = candidate_vetoes(candidate, veto_wers)
        per_candidate[candidate] = {"tests": tests, "vetoes": vetoes}
        family.extend(tests[name]["p"] for name in TEST_ORDER)
    mask = holm_reject(family, ALPHA)
    qualifiers = []
    for slot, candidate in enumerate(CANDIDATES):
        indices = list(range(slot * len(TEST_ORDER),
                             (slot + 1) * len(TEST_ORDER)))
        holm_ok = candidate_qualifies(family, indices, ALPHA)
        vetoed = any(v["vetoed"]
                     for v in per_candidate[candidate]["vetoes"].values())
        per_candidate[candidate]["holm_all_rejected"] = holm_ok
        per_candidate[candidate]["vetoed"] = vetoed
        per_candidate[candidate]["qualifies"] = holm_ok and not vetoed
        if holm_ok and not vetoed:
            qualifiers.append(candidate)
    decision: dict = {
        "family_pvalues": family,
        "family_order": [f"{c}:{t}" for c in CANDIDATES for t in TEST_ORDER],
        "holm_mask": mask,
        "per_candidate": per_candidate,
        "qualifiers": qualifiers,
    }
    if not qualifiers:
        decision["outcome"] = "NO_RECIPE_QUALIFIES"
        return decision
    # tie-break (protocol stage_1_provisional.tie_break_seed1_only):
    # (1) lowest seed-1 preservation macro point WER; (2) lowest KD alpha;
    # (3) lexicographically smallest recipe id. Seed-2 is NEVER consulted.
    def macro_point(candidate: str) -> float:
        return sum(wers[lang][candidate + "__point"][0]
                   for lang in PRESERVATION_LANGUAGES) / 3.0
    ranked = sorted(qualifiers,
                    key=lambda c: (macro_point(c), alphas[c], c))
    decision["tie_break"] = [
        {"candidate": c, "macro_point_wer": macro_point(c),
         "kd_alpha": alphas[c]} for c in ranked]
    decision["outcome"] = "PROVISIONAL_FINALIST"
    decision["provisional_finalist"] = ranked[0]
    return decision


def stage2_decision(finalist: str, wers, veto_wers) -> dict:
    """Stage-2 replication: the finalist's 6 tests ARE the whole family
    (fresh Holm, NOT re-corrected against Stage 1) + raw vetoes. ANY failure
    => NO_RECIPE_QUALIFIES (no fallback to a runner-up)."""
    tests = candidate_tests(finalist, wers)
    vetoes = candidate_vetoes(finalist, veto_wers)
    family = [tests[name]["p"] for name in TEST_ORDER]
    holm_ok = qualifies(family, ALPHA)
    vetoed = any(v["vetoed"] for v in vetoes.values())
    return {
        "finalist": finalist,
        "family_pvalues": family,
        "family_order": list(TEST_ORDER),
        "tests": tests, "vetoes": vetoes,
        "holm_all_rejected": holm_ok, "vetoed": vetoed,
        "outcome": ("FINAL_NOMINATION" if holm_ok and not vetoed
                     else "NO_RECIPE_QUALIFIES"),
    }


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------

def score_stage(*, stage: int, split: dict[str, list[str]],
                veto_surfaces: dict[str, list[str]],
                receipts: dict[str, dict[str, tuple[int, int]]],
                cluster_map: dict[str, str], B: int, master_seed: int,
                alphas: dict[str, float], finalist: str | None = None) -> dict:
    """Assemble every surface's paired bootstrap and run the stage decision.
    `receipts` maps arm name -> {identity: (edits, ref_words)}."""
    if B < MIN_B:
        raise ScorerRefusal(f"B={B} < the protocol minimum {MIN_B}")
    arms = (["base", "arm1", "KD_CONTROL", "H0"]
            + (list(CANDIDATES) if stage == 1 else [str(finalist)]))
    missing_arms = [a for a in arms if a not in receipts]
    if missing_arms:
        raise ScorerRefusal(f"receipts missing for arms {missing_arms}")
    wers: dict[str, dict[str, list[float]]] = {}
    for language in NOMINATION_LANGUAGES:
        identities = split[language]
        clusters = build_clusters(identities, cluster_map)
        plan = resample_plan(len(clusters), B,
                             derive_seed(master_seed, f"nom:{language}"))
        rows_by_arm = {arm: surface_rows(identities, receipts[arm],
                                         arm=arm, surface=language)
                       for arm in arms}
        wers[language] = bootstrap_wers(rows_by_arm, clusters, plan)
        for arm in arms:            # point estimates for the tie-break
            wers[language][arm + "__point"] = [corpus_wer(rows_by_arm[arm])]
    veto_wers: dict[str, dict[str, list[float]]] = {}
    for surface in VETO_SURFACES:
        identities = veto_surfaces[surface]
        clusters = build_clusters(identities, None)      # ROW-level, always
        plan = resample_plan(len(clusters), B,
                             derive_seed(master_seed, f"veto:{surface}"))
        needed = ([VETO_REFERENCE[surface]]
                  + (list(CANDIDATES) if stage == 1 else [str(finalist)]))
        rows_by_arm = {arm: surface_rows(identities, receipts[arm],
                                         arm=arm, surface=surface)
                       for arm in needed}
        veto_wers[surface] = bootstrap_wers(rows_by_arm, clusters, plan)
    if stage == 1:
        return stage1_decision(wers, veto_wers, alphas=alphas)
    return stage2_decision(str(finalist), wers, veto_wers)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("stage1", "stage2"))
    parser.add_argument("--split", type=Path, default=Path(FROZEN_SPLIT_PATH))
    parser.add_argument("--split-sha256", default=FROZEN_SPLIT_SHA256)
    parser.add_argument("--clusters", type=Path, required=True,
                        help="speaker cluster map for the nomination rows")
    parser.add_argument("--veto-surface", action="append", default=[],
                        metavar="LANG=PATH:SHA256", required=True,
                        help="one per veto language (lingala/kinyarwanda/ewe)")
    parser.add_argument("--receipts", action="append", default=[],
                        metavar="ARM=PATH", required=True)
    parser.add_argument("--kd-alpha", action="append", default=[],
                        metavar="CANDIDATE=ALPHA",
                        help="tie-break alphas (stage1; from the packets)")
    parser.add_argument("--finalist", default=None)
    parser.add_argument("--B", type=int, default=MIN_B)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    split = load_frozen_split(args.split, args.split_sha256)
    vetoes: dict[str, list[str]] = {}
    for spec in args.veto_surface:
        lang, _, rest = spec.partition("=")
        path, _, sha = rest.rpartition(":")
        if lang not in VETO_SURFACES:
            raise ScorerRefusal(f"unknown veto surface {lang!r}")
        vetoes[lang] = load_veto_surface(Path(path), expected_sha=sha,
                                         language=lang)
    if set(vetoes) != set(VETO_SURFACES):
        raise ScorerRefusal(f"veto surfaces incomplete: have {sorted(vetoes)}")
    receipts: dict[str, dict[str, tuple[int, int]]] = {}
    receipt_shas: dict[str, str] = {}
    for spec in args.receipts:
        arm, _, path = spec.partition("=")
        if arm in receipts:
            raise ScorerRefusal(f"duplicate receipts for arm {arm!r}")
        receipts[arm], receipt_shas[arm] = load_receipts(Path(path))
    cluster_map, cluster_sha = load_cluster_map(args.clusters)
    alphas: dict[str, float] = {}
    for spec in args.kd_alpha:
        cand, _, value = spec.partition("=")
        alphas[cand] = float(value)
    if args.stage == "stage1" and set(alphas) != set(CANDIDATES):
        raise ScorerRefusal(
            f"stage1 tie-break needs --kd-alpha for all of {CANDIDATES}")
    if args.stage == "stage2" and not args.finalist:
        raise ScorerRefusal("stage2 requires --finalist")

    decision = score_stage(
        stage=1 if args.stage == "stage1" else 2, split=split,
        veto_surfaces=vetoes, receipts=receipts, cluster_map=cluster_map,
        B=args.B, master_seed=args.seed, alphas=alphas,
        finalist=args.finalist)
    result = {
        "record": f"B5-UNIVERSAL-ARM2-NOMINATION-{args.stage.upper()}-DECISION",
        "protocol": "B5-UNIVERSAL-ARM2-KD-COMPARISON-PROTOCOL-2026-001",
        "inputs": {
            "split": str(args.split), "split_sha256": args.split_sha256,
            "clusters_sha256": cluster_sha,
            "receipts_sha256": receipt_shas,
            "veto_surfaces": args.veto_surface,
            "B": args.B, "master_seed": args.seed, "alpha": ALPHA,
            "noninferiority_margin": NONINF_MARGIN,
            "veto_margin": VETO_MARGIN,
        },
        "decision": decision,
    }
    payload = json.dumps(result, indent=1, sort_keys=True).encode() + b"\n"
    args.out.write_bytes(payload)
    print(json.dumps({"outcome": decision["outcome"],
                      "out": str(args.out),
                      "result_sha256": _sha256_bytes(payload)},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
