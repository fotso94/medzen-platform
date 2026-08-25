"""Arm-2 KD comparison protocol + exposure inventory — DESIGN-REVIEW artifacts
(owner two-review gate, rev 005). Validated AGAINST SOURCE RECORDS. Rev-005
semantics: exposure CLASSES (base-scored candidate-blind rows stay eligible for
Phase A); ONE authoritative two-stage statistical_procedure; Stage-1 tie-break
seed-1-only; benchmark-adjusted cost over $70 fails closed. Fail if the design is
silently treated as approved, a required element is missing, a claim diverges
from source, or the cost arithmetic exceeds the ceiling."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROTO = ROOT / "platform/decisions/B5-UNIVERSAL-ARM2-KD-COMPARISON-PROTOCOL-2026-001.json"
INV = ROOT / "platform/decisions/B5-UNIVERSAL-ARM2-EXPOSURE-INVENTORY-2026-001.json"
IDX = ROOT / "platform/manifests/B5-UNIVERSAL-ARM2-EXPOSURE-INDEX-2026-001.json"


def _p():
    return json.loads(PROTO.read_bytes())


def _i():
    return json.loads(INV.read_bytes())


# ---- governance -----------------------------------------------------------

def test_both_records_are_pending_review_and_authorize_nothing():
    for d in (_p(), _i()):
        assert d["status"] == "PENDING_DESIGN_REVIEW"
        blob = json.dumps(d)
        assert '"decision": "APPROVED"' not in blob
        assert "APPROVED_EXECUTION" not in blob
    assert "NOT_AN_APPROVAL" in _p()
    assert set(_p()["two_review_gate"]) == {"first_review", "second_review"}


# ---- exposure CLASSES: base-scored rows stay eligible for Phase A ---------

def test_exposure_is_classes_and_base_exposed_is_phase_a_eligible():
    idx = json.loads(IDX.read_bytes())
    assert set(idx["exposure_classes"]) == {
        "CANDIDATE_EXPOSED", "BASE_EXPOSED", "TRAINING_EXPOSED", "SEALED"}
    # every in-repo surface is tagged with a class
    assert all("exposure_class" in s for s in idx["surfaces"])
    pa = idx["phase_eligibility"]["phase_A_nomination"]
    assert "BASE_EXPOSED rows ARE" in pa and "eligible" in pa
    assert "NOT in CANDIDATE_EXPOSED" in pa
    pb = idx["phase_eligibility"]["phase_B_confirmation"]
    assert "excludes BASE_EXPOSED" in pb
    # the protocol nomination rule allows BASE_EXPOSED for Phase A
    src = _p()["nomination_data_rules"]["phaseA_split_source"]
    assert "BASE_EXPOSED rows (zero-shot base-scored) REMAIN ELIGIBLE" in src
    assert "NON-EMPTY" in src
    assert "NOT in CANDIDATE_EXPOSED" in src


def test_disjointness_contract_is_class_based_not_whole_pool():
    idx = json.loads(IDX.read_bytes())
    assert "class-based per-row eligibility" in idx["disjointness_contract"]
    assert "never whole-pool count subtraction" in idx["disjointness_contract"]


# ---- ONE authoritative two-stage procedure --------------------------------

def test_single_statistical_procedure_replaced_the_split_blocks():
    d = _p()
    sp = d["statistical_procedure"]
    assert "single_authority" in sp
    # the ambiguous predecessors are gone
    for gone in ("statistics", "seed_procedure", "selection_rule", "macro_objective"):
        assert gone not in d, f"{gone} must be folded into statistical_procedure"


def test_two_stage_with_stage_specific_multiplicity_and_seed1_only_tiebreak():
    sp = _p()["statistical_procedure"]
    assert sp["stage_1_provisional"]["seed"] == "seed 1 ONLY"
    tb = sp["stage_1_provisional"]["tie_break_seed1_only"]
    assert "SEED-1 RESULTS ONLY" in tb
    assert "NEVER used for ranking" in tb
    assert "DETERMINISTIC" in tb
    assert "SMALLER" in sp["multiplicity"]["stage_2"]
    assert "NOT re-corrected" in sp["multiplicity"]["stage_2"]
    assert "provisional finalist + H0 + KD_CONTROL" in \
        sp["stage_2_replication"]["runs"]
    assert "NO_RECIPE_QUALIFIES" in sp["stage_2_replication"]["confirm"]


def test_h0_is_mechanics_proven_comparator_only():
    d = _p()
    comps = {a["id"]: a for a in d["arm_roles"]["comparators_not_nominable"]}
    cands = {a["id"] for a in d["arm_roles"]["candidates_nominable"]}
    assert "MECHANICS_PROVEN" in comps["H0"]["status"]
    assert cands == {"H1", "H2", "H3", "H4"} and "H0" not in cands
    assert "H0_comparator_only" in d["statistical_procedure"]


def test_preservation_macro_separated_from_pidgin_retention():
    sp = _p()["statistical_procedure"]
    macro = sp["preservation_macro"]
    assert "{english, french, swahili}" in macro
    assert "SEPARATE from pidgin" in macro
    obj = _p()["objectives"]
    assert obj["preservation_objective"]["languages"] == ["english", "french", "swahili"]
    assert obj["pidgin_retention_objective"]["language"] == "pidgin"


def test_resampling_is_speaker_cluster_bootstrap():
    sp = _p()["statistical_procedure"]
    assert "cluster bootstrap" in sp["resampling"]
    assert "10000" in sp["resampling"]
    assert "SPEAKER" in sp["resampling"]
    assert "Holm-Bonferroni" in sp["multiplicity"]["stage_1"]


# ---- directional veto catches the historical Lingala failure -------------

def test_directional_veto_would_catch_the_historical_lingala_failure():
    v = _p()["constraints"]["directional_development_vetoes"]
    assert set(v["languages"]) == {"lingala", "kinyarwanda", "ewe"}
    assert v["veto_margin_abs_wer"] == 0.01
    assert "UPPER CI" in v["veto_rule"] and "0.02" not in v["veto_rule"]
    rcpt = json.loads((ROOT / "platform/evidence/receipts/"
                       "ARM1-LINGALA-SENTINEL-2026-001/receipt.json").read_bytes())
    assert v["veto_margin_abs_wer"] < rcpt["noninferiority"]["upper_ci"]
    assert "0.012685" in v["historical_calibration"]


def test_h0_second_seed_contradiction_resolved():
    ss = _p()["training"]["second_seed"]
    assert set(ss["runs"]) == {"the nominated finalist", "H0", "KD_CONTROL"}


# ---- cost: benchmark budgeted, fail-closed over $70 ----------------------

def test_cost_table_benchmark_budgeted_and_fail_closed_over_seventy():
    t = _p()["cost_runtime_table"]
    assert "REPLACED" in t["scoring_throughput_basis"]
    jobs = t["jobs"]
    assert next(j for j in jobs if "mechanics" in j["job"])["count"] == 6
    assert next(j for j in jobs if "second-seed" in j["job"])["count"] == 3
    assert any("benchmark" in j["job"] for j in jobs)
    worst = 0.0
    for j in jobs:
        assert round(j["count"] * j["worst_case_usd_each"], 2) == j["worst_case_usd"]
        assert j["expected_usd"] <= j["worst_case_usd"] + 1e-9
        assert round(j["max_runtime_s"] / 3600 * 1.60, 2) == j["worst_case_usd_each"]
        worst += j["worst_case_usd"]
    assert round(worst, 2) == t["cumulative_worst_case_usd"] <= 70.0
    b = _p()["budget"]
    assert b["ceiling_usd"] == 70
    assert "fail-closed" in b["fail_closed_over_ceiling"]
    assert "NEW owner approval" in b["fail_closed_over_ceiling"]
    assert "NEVER auto-proceeds above $70" in b["fail_closed_over_ceiling"]


# ---- SOURCE-RECORD validation --------------------------------------------

def test_exposure_inventory_dev_selection_matches_the_real_manifest():
    sel = json.loads((ROOT / "platform/manifests/"
                      "B5-UNIVERSAL-ARM1-DEV-SELECTION-2026-001.json").read_bytes())
    assert len(sel["rows"]) == 420
    assert sel["rows_sha256"] == (
        "54897fff75b8c2c39901ef552f3e58c27340f774887eb09f05f4c7c37b835075")
    ds = next(s for s in _i()["used_surfaces"]
              if s["exposure"] == "USED_DEV_SELECTION")
    assert sel["rows_sha256"] in ds["identity"]


def test_exposure_inventory_sentinel_hashes_match_files_on_disk():
    for name, want in (("lingala",
                        "59b2485c947d6f0556fffaeb83744dd814589a8dca9ccba6d7e52ec0cc4d0f5a"),
                       ("swahili",
                        "613fad796b8629c351fa023944b384f6198d74125733ea89e61af60adaf4c4a8")):
        actual = hashlib.sha256(
            (ROOT / f"platform/manifests/dev-sentinels/{name}.jsonl").read_bytes()
        ).hexdigest()
        assert actual == want


def test_exposure_inventory_cites_only_holdout_records_that_exist():
    for rel in _i()["sealed_holdout"]["governed_by"]:
        assert (ROOT / rel).exists(), f"cited sealed record missing: {rel}"


def test_untouched_verdict_matches_flag_and_gates_only_available_langs():
    v = _i()["per_language_untouched_verdict"]
    for lang in ("kinyarwanda", "lingala", "ewe"):
        assert v[lang]["available"] == "NO"
    assert v["pidgin"]["available"] == "YES_FULLY_BLIND"
    for lang in ("english", "french", "swahili"):
        assert v[lang]["available"] == "YES_CANDIDATE_BLIND"
    ph = _p()["phases"]["phase_A_held_out_development_nomination_now"]
    assert set(ph["nomination_gated_languages"]) == {
        "english", "french", "swahili", "pidgin"}
    assert set(ph["directional_veto_languages"]) == {
        "lingala", "kinyarwanda", "ewe"}


def test_identical_rows_across_all_scored_models():
    req = _p()["evaluation"]["identical_rows_requirement"]
    for who in ("base_teacher", "arm1", "H0", "KD_CONTROL"):
        assert who in req
    assert "paired" in req
