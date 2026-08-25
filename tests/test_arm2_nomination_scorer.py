"""Adversarial tests for the committed Arm-2 nomination scorer (Codex stage-1
review 2026-08-25 finding 3: the complete decision pipeline must be committed
and tested BEFORE candidate results exist)."""
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import arm2_nomination_scorer as scorer          # noqa: E402


# ---------------------------------------------------------------- fixtures
def ident(surface: str, i: int) -> str:
    return hashlib.sha256(f"{surface}:{i}".encode()).hexdigest()


def build_surfaces():
    split = {lang: [ident(lang, i) for i in range(6)]
             for lang in scorer.NOMINATION_LANGUAGES}
    vetoes = {s: [ident("veto:" + s, i) for i in range(4)]
              for s in scorer.VETO_SURFACES}
    clusters = {}
    for lang, ids in split.items():
        for i, identity in enumerate(ids):
            clusters[identity] = f"{lang}-spk{i // 2}"     # 3 speakers of 2
    return split, vetoes, clusters


def receipts_for(split, vetoes, wer_by_surface: dict[str, float]):
    """Uniform per-row receipts: 100 ref words, edits = round(wer*100)."""
    rows = {}
    for lang, ids in split.items():
        for identity in ids:
            rows[identity] = (int(round(wer_by_surface[lang] * 100)), 100)
    for surface, ids in vetoes.items():
        for identity in ids:
            rows[identity] = (int(round(wer_by_surface[surface] * 100)), 100)
    return rows


def flat(w_nom: float, w_veto: float) -> dict[str, float]:
    d = {lang: w_nom for lang in scorer.NOMINATION_LANGUAGES}
    d.update({s: w_veto for s in scorer.VETO_SURFACES})
    return d


BASELINE = flat(0.10, 0.10)


def run_stage1(cand_wers: dict[str, dict[str, float]], B=10000):
    split, vetoes, clusters = build_surfaces()
    receipts = {
        "base": receipts_for(split, vetoes, BASELINE),
        "arm1": receipts_for(split, vetoes, BASELINE),
        "KD_CONTROL": receipts_for(split, vetoes, BASELINE),
        "H0": receipts_for(split, vetoes, BASELINE),
    }
    for cand in scorer.CANDIDATES:
        receipts[cand] = receipts_for(split, vetoes, cand_wers[cand])
    return scorer.score_stage(
        stage=1, split=split, veto_surfaces=vetoes, receipts=receipts,
        cluster_map=clusters, B=B, master_seed=7,
        alphas={"H1": 0.25, "H2": 1.0, "H3": 0.5, "H4": 0.5})


# ---------------------------------------------------------------- decisions
def test_clear_winner_qualifies_and_wins_tie_break():
    wers = {c: BASELINE for c in scorer.CANDIDATES}
    wers = dict(wers)
    wers["H1"] = flat(0.05, 0.10)          # clearly better everywhere gated
    d = run_stage1(wers)
    assert d["per_candidate"]["H1"]["qualifies"]
    assert d["outcome"] == "PROVISIONAL_FINALIST"
    assert d["provisional_finalist"] == "H1"


def test_no_candidate_qualifies_is_terminal():
    # every candidate identical to the comparators: beats nothing
    d = run_stage1({c: BASELINE for c in scorer.CANDIDATES})
    assert d["qualifiers"] == []
    assert d["outcome"] == "NO_RECIPE_QUALIFIES"
    assert "provisional_finalist" not in d


def test_veto_disqualifies_even_a_positive_winner():
    # H3 wins the gated languages but regresses lingala by +2pp (> 1pp veto)
    good = flat(0.05, 0.10)
    good["lingala"] = 0.12
    d = run_stage1({"H1": BASELINE, "H2": BASELINE, "H3": good,
                    "H4": BASELINE})
    pc = d["per_candidate"]["H3"]
    assert pc["holm_all_rejected"] is True
    assert pc["vetoes"]["lingala"]["vetoed"] is True
    assert pc["qualifies"] is False
    assert d["outcome"] == "NO_RECIPE_QUALIFIES"


def test_tie_break_lowest_alpha_then_lexicographic():
    # H3 and H4 identical winners; alphas H3=0.5, H4=0.5 -> lexicographic H3
    win = flat(0.05, 0.10)
    d = run_stage1({"H1": BASELINE, "H2": BASELINE, "H3": win, "H4": win})
    assert set(d["qualifiers"]) == {"H3", "H4"}
    assert d["provisional_finalist"] == "H3"


def test_stage2_reversal_is_terminal():
    split, vetoes, clusters = build_surfaces()
    receipts = {a: receipts_for(split, vetoes, BASELINE)
                for a in ("base", "arm1", "KD_CONTROL", "H0")}
    receipts["H1"] = receipts_for(split, vetoes, BASELINE)   # no longer wins
    d = scorer.score_stage(stage=2, split=split, veto_surfaces=vetoes,
                           receipts=receipts, cluster_map=clusters,
                           B=10000, master_seed=7, alphas={}, finalist="H1")
    assert d["outcome"] == "NO_RECIPE_QUALIFIES"


def test_stage2_confirms_a_replicating_finalist():
    split, vetoes, clusters = build_surfaces()
    receipts = {a: receipts_for(split, vetoes, BASELINE)
                for a in ("base", "arm1", "KD_CONTROL", "H0")}
    receipts["H1"] = receipts_for(split, vetoes, flat(0.05, 0.10))
    d = scorer.score_stage(stage=2, split=split, veto_surfaces=vetoes,
                           receipts=receipts, cluster_map=clusters,
                           B=10000, master_seed=7, alphas={}, finalist="H1")
    assert d["outcome"] == "FINAL_NOMINATION"


# ---------------------------------------------------------------- refusals
def test_missing_rows_refuse():
    split, vetoes, clusters = build_surfaces()
    receipts = {a: receipts_for(split, vetoes, BASELINE)
                for a in ("base", "arm1", "KD_CONTROL", "H0")}
    for c in scorer.CANDIDATES:
        receipts[c] = receipts_for(split, vetoes, BASELINE)
    del receipts["H1"][split["english"][0]]
    with pytest.raises(SystemExit, match="missing 1 row"):
        scorer.score_stage(stage=1, split=split, veto_surfaces=vetoes,
                           receipts=receipts, cluster_map=clusters,
                           B=10000, master_seed=7,
                           alphas={c: 0.5 for c in scorer.CANDIDATES})


def test_missing_speaker_cluster_refuses():
    split, vetoes, clusters = build_surfaces()
    clusters.pop(split["french"][0])
    receipts = {a: receipts_for(split, vetoes, BASELINE)
                for a in ("base", "arm1", "KD_CONTROL", "H0",
                          *scorer.CANDIDATES)}
    with pytest.raises(SystemExit, match="no speaker cluster"):
        scorer.score_stage(stage=1, split=split, veto_surfaces=vetoes,
                           receipts=receipts, cluster_map=clusters,
                           B=10000, master_seed=7,
                           alphas={c: 0.5 for c in scorer.CANDIDATES})


def test_below_minimum_B_refuses():
    with pytest.raises(SystemExit, match="protocol minimum"):
        run_stage1({c: BASELINE for c in scorer.CANDIDATES}, B=999)


def test_split_sha_pin_refuses_substitution(tmp_path):
    fake = tmp_path / "split.json"
    fake.write_bytes(b'{"manifest": {"split": {}}}')
    with pytest.raises(SystemExit, match="refusing a substituted split"):
        scorer.load_frozen_split(fake)


def test_frozen_split_constant_matches_committed_artifact():
    committed = Path(__file__).resolve().parents[1] / scorer.FROZEN_SPLIT_PATH
    raw = committed.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == scorer.FROZEN_SPLIT_SHA256
    split = scorer.load_frozen_split(committed)
    assert {k: len(v) for k, v in split.items()} == {
        "english": 451, "french": 652, "pidgin": 1440, "swahili": 372}


# ---------------------------------------------------------------- pairing
def test_determinism_same_seed_identical_decision():
    wers = {c: BASELINE for c in scorer.CANDIDATES}
    wers["H1"] = flat(0.05, 0.10)
    a = run_stage1(wers)
    b = run_stage1(wers)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_bootstrap_is_paired_identical_arms_have_zero_diff():
    # two arms with IDENTICAL rows must produce IDENTICAL bootstrap samples
    split, vetoes, clusters = build_surfaces()
    ids = split["english"]
    rows = [(10, 100)] * len(ids)
    cl = scorer.build_clusters(ids, clusters)
    plan = scorer.resample_plan(len(cl), 500, 42)
    out = scorer.bootstrap_wers({"a": rows, "b": list(rows)}, cl, plan)
    assert out["a"] == out["b"]
