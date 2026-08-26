"""Arm-2 Phase-A nomination scorer v2 — the COMPLETE executable decision
pipeline, driven by ONE frozen, committed SCORING PACKET (Codex second-review
2026-08-25 finding 5: v1 accepted caller-chosen splits/vetoes/clusters/alphas
and caller-supplied edit counts; v2 authenticates every input).

Authority: statistical_procedure of
platform/decisions/B5-UNIVERSAL-ARM2-KD-COMPARISON-PROTOCOL-2026-001.json.

What the frozen scoring packet
(platform/decisions/B5-UNIVERSAL-ARM2-NOMINATION-SCORING-PACKET-2026-001.json)
pins, and this scorer enforces:

  - the FROZEN nomination split (path + artifact sha256)
  - per-surface REFERENCE files (identity -> text_normalized [+ speaker_id]),
    sha-pinned, committed, built from the exposure-index-pinned pool
    manifests and the committed veto records BEFORE any candidate results
  - the frozen CLUSTER map (placeholder-speaker rule applied pre-results)
  - candidate KD alphas READ FROM the six committed stage-1 packets
    (path + canonical sha pinned; never CLI-supplied)
  - bootstrap settings (B >= 10000, master_seed) and both margins
  - the receipt schema: per arm a JSON {job_name, model_sha256,
    packet_canonical_sha256, rows: [{audio_checksum_sha256, hyp_normalized}]}.
    Receipts carry HYPOTHESES ONLY — this scorer RECOMPUTES edit_distance and
    ref_words from the hypothesis vs the PINNED reference (word-level
    Levenshtein on whitespace tokens); caller-supplied edit counts REFUSE.
  - the arm->model identity rule: at scoring time an --arm-models JSON maps
    every arm to its REQUIRED export model_sha256 (from the arms' completion
    receipts); every receipts file's model_sha256 must equal its arm's entry.
  - the Stage-1 -> Stage-2 chain: stage2 takes the committed stage-1 RESULT
    (path + sha256) and DERIVES the finalist from it; a caller-chosen
    finalist is not an input.

Determinism: random.Random(derive_seed(master_seed, surface)) per surface;
ONE shared resample plan applied to every arm (paired). No wall-clock, no
environment reads. This scorer launches nothing and holds no credentials.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path

from arm2_holm import candidate_qualifies, holm_reject, qualifies

SCORING_PACKET_PATH = (
    "platform/decisions/B5-UNIVERSAL-ARM2-NOMINATION-SCORING-PACKET-2026-001.json")

NOMINATION_LANGUAGES = ("english", "french", "pidgin", "swahili")
PRESERVATION_LANGUAGES = ("english", "french", "swahili")
VETO_SURFACES = ("lingala", "kinyarwanda", "ewe")
VETO_REFERENCE = {"lingala": "base", "kinyarwanda": "arm1", "ewe": "arm1"}
CANDIDATES = ("H1", "H2", "H3", "H4")
COMPARATORS = ("base", "arm1", "KD_CONTROL", "H0")
TEST_ORDER = ("preservation_english", "preservation_french",
              "preservation_swahili", "pidgin_retention",
              "beat_KD_CONTROL", "beat_H0")
NONINF_MARGIN = 0.005
VETO_MARGIN = 0.01
ALPHA = 0.05
MIN_B = 10000


class ScorerRefusal(SystemExit):
    """Any malformed, incomplete, unauthenticated or inconsistent input
    refuses loudly."""


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def derive_seed(master_seed: int, surface: str) -> int:
    digest = hashlib.sha256(f"{master_seed}:{surface}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def word_edits(reference: str, hypothesis: str) -> tuple[int, int]:
    """Word-level Levenshtein distance on whitespace tokens + the reference
    word count — the packet-declared metric (recomputed here, never trusted
    from a caller)."""
    ref = reference.split()
    hyp = hypothesis.split()
    m, n = len(ref), len(hyp)
    prev = list(range(n + 1))
    for i in range(1, m + 1):
        cur = [i] + [0] * n
        for j in range(1, n + 1):
            cost = 0 if ref[i - 1] == hyp[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[n], m


# --------------------------------------------------------------------------
# scoring-packet loading (every pin verified)
# --------------------------------------------------------------------------

def _pinned_bytes(root: Path, pin: dict, what: str) -> bytes:
    rel = str(pin.get("path") or "")
    want = str(pin.get("sha256") or "")
    if not rel or not want:
        raise ScorerRefusal(f"scoring packet pins no {{path,sha256}} for {what}")
    raw = (root / rel).read_bytes()
    actual = _sha256_bytes(raw)
    if actual != want:
        raise ScorerRefusal(
            f"{what} {rel} hashes to {actual[:16]}, the scoring packet pins "
            f"{want[:16]} — refusing a substituted input")
    return raw


def load_scoring_packet(root: Path, path: Path) -> dict:
    raw = path.read_bytes()
    packet = json.loads(raw)
    if packet.get("record") != \
            "B5-UNIVERSAL-ARM2-NOMINATION-SCORING-PACKET-2026-001":
        raise ScorerRefusal("not the Arm-2 nomination scoring packet")
    cfg: dict = {"packet_sha256": _sha256_bytes(raw), "packet": packet}

    # frozen split
    split_doc = json.loads(_pinned_bytes(root, packet["split"], "frozen split"))
    split = (split_doc.get("manifest") or {}).get("split") or {}
    cfg["split"] = {}
    for language in NOMINATION_LANGUAGES:
        rows = split.get(language)
        if not isinstance(rows, list) or not rows or len(set(rows)) != len(rows):
            raise ScorerRefusal(f"frozen split rows for {language!r} invalid")
        cfg["split"][language] = [str(r) for r in rows]

    # per-surface references (nomination + veto)
    cfg["references"] = {}
    for surface in (*NOMINATION_LANGUAGES, *VETO_SURFACES):
        raw_refs = _pinned_bytes(root, packet["references"][surface],
                                 f"references[{surface}]")
        refs: dict[str, str] = {}
        for line in raw_refs.decode().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            identity = str(row.get("audio_checksum_sha256") or "")
            if len(identity) != 64 or identity in refs:
                raise ScorerRefusal(
                    f"references[{surface}]: malformed/duplicate identity")
            refs[identity] = str(row.get("text_normalized") or "")
        if not refs:
            raise ScorerRefusal(f"references[{surface}] empty")
        cfg["references"][surface] = refs
    # nomination reference coverage must equal the split exactly
    for language in NOMINATION_LANGUAGES:
        if set(cfg["references"][language]) != set(cfg["split"][language]):
            raise ScorerRefusal(
                f"references[{language}] do not cover the frozen split exactly")

    # frozen cluster map
    cl_doc = json.loads(_pinned_bytes(root, packet["clusters"], "cluster map"))
    cfg["clusters"] = {str(k): str(v)
                       for k, v in (cl_doc.get("clusters") or {}).items()}
    for language in NOMINATION_LANGUAGES:
        missing = [i for i in cfg["split"][language]
                   if i not in cfg["clusters"]]
        if missing:
            raise ScorerRefusal(
                f"cluster map misses {len(missing)} {language} rows")

    # candidate alphas from the six committed stage-1 packets
    cfg["alphas"] = {}
    for candidate, pin in sorted((packet.get("candidate_packets") or {}).items()):
        body = _pinned_bytes(root, pin, f"stage-1 packet[{candidate}]")
        doc = json.loads(body)
        canonical = hashlib.sha256(json.dumps(
            doc, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if canonical != str(pin.get("canonical_sha256") or ""):
            raise ScorerRefusal(
                f"stage-1 packet[{candidate}] canonical sha mismatch")
        cfg["alphas"][candidate] = float(doc["distillation"]["kd_alpha"])
    if set(cfg["alphas"]) != set(CANDIDATES):
        raise ScorerRefusal(
            f"scoring packet must pin exactly the candidate packets "
            f"{CANDIDATES}; got {sorted(cfg['alphas'])}")

    boot = packet.get("bootstrap") or {}
    cfg["B"] = int(boot.get("B") or 0)
    cfg["master_seed"] = int(boot.get("master_seed") or 0)
    if cfg["B"] < MIN_B:
        raise ScorerRefusal(f"scoring packet B={cfg['B']} < {MIN_B}")
    if not cfg["master_seed"]:
        raise ScorerRefusal("scoring packet lacks a master_seed")
    return cfg


# --------------------------------------------------------------------------
# authenticated receipts
# --------------------------------------------------------------------------

def load_receipts(path: Path, *, arm: str,
                  expected_model_sha: str) -> tuple[dict[str, str], str]:
    """One arm's hypothesis receipts. Returns ({identity: hyp_normalized},
    file sha). REFUSES caller-supplied edit counts and identity mismatches."""
    raw = path.read_bytes()
    doc = json.loads(raw)
    for key in ("job_name", "model_sha256", "packet_canonical_sha256", "rows"):
        if not doc.get(key):
            raise ScorerRefusal(f"receipts[{arm}] lack {key!r}")
    if str(doc["model_sha256"]) != expected_model_sha:
        raise ScorerRefusal(
            f"receipts[{arm}] declare model {str(doc['model_sha256'])[:16]} "
            f"but the arm-model binding requires {expected_model_sha[:16]} — "
            "refusing hypotheses from an unproven model")
    out: dict[str, str] = {}
    for row in doc["rows"]:
        if "edit_distance" in row or "ref_words" in row:
            raise ScorerRefusal(
                f"receipts[{arm}] carry caller-supplied edit counts — the "
                "scorer RECOMPUTES from hypotheses; precomputed numbers refuse")
        identity = str(row.get("audio_checksum_sha256") or "")
        if len(identity) != 64 or identity in out:
            raise ScorerRefusal(f"receipts[{arm}]: malformed/duplicate row")
        if "hyp_normalized" not in row:
            raise ScorerRefusal(f"receipts[{arm}]: row lacks hyp_normalized")
        out[identity] = str(row["hyp_normalized"])
    return out, _sha256_bytes(raw)


def surface_rows(identities: list[str], hyps: dict[str, str],
                 refs: dict[str, str], *, arm: str, surface: str
                 ) -> list[tuple[int, int]]:
    """Exact coverage + RECOMPUTED (edits, ref_words) per row."""
    missing = [i for i in identities if i not in hyps]
    if missing:
        raise ScorerRefusal(
            f"arm {arm!r} receipts are missing {len(missing)} row(s) of the "
            f"{surface!r} surface (first: {missing[0][:12]}) — refusing a "
            "partial scoring")
    return [word_edits(refs[i], hyps[i]) for i in identities]


# --------------------------------------------------------------------------
# WER + paired cluster bootstrap (unchanged core)
# --------------------------------------------------------------------------

def corpus_wer(rows: list[tuple[int, int]]) -> float:
    edits = sum(r[0] for r in rows)
    words = sum(r[1] for r in rows)
    if words <= 0:
        raise ScorerRefusal("surface has zero reference words")
    return edits / words


def build_clusters(identities: list[str], cluster_map: dict[str, str] | None
                   ) -> list[list[int]]:
    if cluster_map is None:
        return [[i] for i in range(len(identities))]
    grouped: dict[str, list[int]] = {}
    for index, identity in enumerate(identities):
        cluster = cluster_map.get(identity)
        if cluster is None:
            raise ScorerRefusal(
                f"identity {identity[:12]} has no cluster assignment")
        grouped.setdefault(cluster, []).append(index)
    return [grouped[k] for k in sorted(grouped)]


def resample_plan(n_clusters: int, B: int, seed: int) -> list[list[int]]:
    rng = random.Random(seed)
    return [[rng.randrange(n_clusters) for _ in range(n_clusters)]
            for _ in range(B)]


def bootstrap_wers(rows_by_arm: dict[str, list[tuple[int, int]]],
                   clusters: list[list[int]], plan: list[list[int]]
                   ) -> dict[str, list[float]]:
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
    n = len(samples)
    ordered = sorted(samples)
    if direction == "ge":
        p = sum(1 for x in samples if x >= threshold) / n
        return {"p": p, "upper_ci95": ordered[min(n - 1, int(0.95 * n))]}
    if direction == "le":
        p = sum(1 for x in samples if x <= threshold) / n
        ci = ordered[max(0, int(0.05 * n) - 1)] if n >= 20 else ordered[0]
        return {"p": p, "lower_ci95": ci}
    raise ScorerRefusal(f"unknown direction {direction!r}")


# --------------------------------------------------------------------------
# the decision pipeline (identical statistics to v1)
# --------------------------------------------------------------------------

def candidate_tests(candidate: str, wers) -> dict[str, dict]:
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


def candidate_vetoes(candidate: str, veto_wers) -> dict[str, dict]:
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
    family: list[float] = []
    per_candidate: dict[str, dict] = {}
    for candidate in CANDIDATES:
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

def score_stage(*, stage: int, cfg: dict,
                hyps_by_arm: dict[str, dict[str, str]],
                finalist: str | None = None) -> dict:
    arms = (list(COMPARATORS)
            + (list(CANDIDATES) if stage == 1 else [str(finalist)]))
    missing_arms = [a for a in arms if a not in hyps_by_arm]
    if missing_arms:
        raise ScorerRefusal(f"receipts missing for arms {missing_arms}")
    B, seed = cfg["B"], cfg["master_seed"]
    wers: dict[str, dict[str, list[float]]] = {}
    for language in NOMINATION_LANGUAGES:
        identities = cfg["split"][language]
        refs = cfg["references"][language]
        clusters = build_clusters(identities, cfg["clusters"])
        plan = resample_plan(len(clusters), B,
                             derive_seed(seed, f"nom:{language}"))
        rows_by_arm = {arm: surface_rows(identities, hyps_by_arm[arm], refs,
                                         arm=arm, surface=language)
                       for arm in arms}
        wers[language] = bootstrap_wers(rows_by_arm, clusters, plan)
        for arm in arms:
            wers[language][arm + "__point"] = [corpus_wer(rows_by_arm[arm])]
    veto_wers: dict[str, dict[str, list[float]]] = {}
    for surface in VETO_SURFACES:
        refs = cfg["references"][surface]
        identities = sorted(refs)
        clusters = build_clusters(identities, None)      # ROW-level, always
        plan = resample_plan(len(clusters), B,
                             derive_seed(seed, f"veto:{surface}"))
        needed = ([VETO_REFERENCE[surface]]
                  + (list(CANDIDATES) if stage == 1 else [str(finalist)]))
        rows_by_arm = {arm: surface_rows(identities, hyps_by_arm[arm], refs,
                                         arm=arm, surface=surface)
                       for arm in needed}
        veto_wers[surface] = bootstrap_wers(rows_by_arm, clusters, plan)
    if stage == 1:
        return stage1_decision(wers, veto_wers, alphas=cfg["alphas"])
    return stage2_decision(str(finalist), wers, veto_wers)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("stage1", "stage2"))
    parser.add_argument("--scoring-packet", type=Path,
                        default=Path(SCORING_PACKET_PATH))
    parser.add_argument("--arm-models", type=Path, required=True,
                        help="JSON {arm: export model_sha256} from the arms' "
                             "completion receipts; every receipts file's "
                             "model_sha256 must match its arm's entry")
    parser.add_argument("--receipts", action="append", default=[],
                        metavar="ARM=PATH", required=True)
    parser.add_argument("--stage1-result", type=Path, default=None,
                        help="stage2: the committed stage-1 decision JSON")
    parser.add_argument("--stage1-result-sha256", default=None)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.stage == "stage2" and (args.stage1_result is None
                                   or not args.stage1_result_sha256):
        raise ScorerRefusal(
            "stage2 requires --stage1-result + --stage1-result-sha256 — "
            "the finalist is DERIVED from the Stage-1 decision, never "
            "caller-chosen")

    root = Path(__file__).resolve().parents[1]
    cfg = load_scoring_packet(root, args.scoring_packet)

    models_raw = args.arm_models.read_bytes()
    arm_models = {str(k): str(v)
                  for k, v in json.loads(models_raw).items()}

    finalist = None
    if args.stage == "stage2":
        if args.stage1_result is None or not args.stage1_result_sha256:
            raise ScorerRefusal(
                "stage2 requires --stage1-result + --stage1-result-sha256 — "
                "the finalist is DERIVED from the Stage-1 decision, never "
                "caller-chosen")
        s1_raw = args.stage1_result.read_bytes()
        if _sha256_bytes(s1_raw) != args.stage1_result_sha256:
            raise ScorerRefusal("stage-1 result sha mismatch")
        s1 = json.loads(s1_raw)
        decision = (s1.get("decision") or {})
        if decision.get("outcome") != "PROVISIONAL_FINALIST":
            raise ScorerRefusal(
                f"stage-1 outcome is {decision.get('outcome')!r} — stage2 "
                "runs only after a PROVISIONAL_FINALIST")
        finalist = str(decision.get("provisional_finalist"))
        if finalist not in CANDIDATES:
            raise ScorerRefusal(f"stage-1 finalist {finalist!r} invalid")

    hyps_by_arm: dict[str, dict[str, str]] = {}
    receipt_shas: dict[str, str] = {}
    for spec in args.receipts:
        arm, _, path = spec.partition("=")
        if arm in hyps_by_arm:
            raise ScorerRefusal(f"duplicate receipts for arm {arm!r}")
        if arm not in arm_models:
            raise ScorerRefusal(f"--arm-models lacks an entry for {arm!r}")
        hyps_by_arm[arm], receipt_shas[arm] = load_receipts(
            Path(path), arm=arm, expected_model_sha=arm_models[arm])

    decision = score_stage(stage=1 if args.stage == "stage1" else 2,
                           cfg=cfg, hyps_by_arm=hyps_by_arm,
                           finalist=finalist)
    result = {
        "record": f"B5-UNIVERSAL-ARM2-NOMINATION-{args.stage.upper()}-DECISION",
        "protocol": "B5-UNIVERSAL-ARM2-KD-COMPARISON-PROTOCOL-2026-001",
        "inputs": {
            "scoring_packet": str(args.scoring_packet),
            "scoring_packet_sha256": cfg["packet_sha256"],
            "arm_models_sha256": _sha256_bytes(models_raw),
            "receipts_sha256": receipt_shas,
            "stage1_result_sha256": args.stage1_result_sha256,
            "B": cfg["B"], "master_seed": cfg["master_seed"],
            "alpha": ALPHA, "noninferiority_margin": NONINF_MARGIN,
            "veto_margin": VETO_MARGIN,
        },
        "decision": decision,
    }
    payload = json.dumps(result, indent=1, sort_keys=True).encode() + b"\n"
    args.out.write_bytes(payload)
    print(json.dumps({"outcome": decision["outcome"], "out": str(args.out),
                      "result_sha256": _sha256_bytes(payload)},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
