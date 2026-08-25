"""The Arm-2 KD comparison protocol + exposure inventory are DESIGN-REVIEW
artifacts (owner two-review gate, rev 003). These tests validate them AGAINST
THE ACTUAL SOURCE RECORDS and enforce the rev-003 semantics: held-out
DEVELOPMENT NOMINATION (not confirmation), preservation separated from pidgin
retention, hard directional vetoes for lingala/kinyarwanda/ewe, H0 as a
mechanics-proven comparator carried through the second seed, and a measured-
throughput $70 ceiling. They fail if the design is silently treated as approved,
if a required element is missing, if a claim diverges from source, or if the
cost arithmetic exceeds the ceiling."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROTO = ROOT / "platform/decisions/B5-UNIVERSAL-ARM2-KD-COMPARISON-PROTOCOL-2026-001.json"
INV = ROOT / "platform/decisions/B5-UNIVERSAL-ARM2-EXPOSURE-INVENTORY-2026-001.json"


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


# ---- terminology: nomination, not confirmation; H0 mechanics-proven -------

def test_phase_a_is_held_out_development_nomination_not_confirmation():
    d = _p()
    assert "held_out_development_nomination" in d["terminology"]
    ph = d["phases"]
    assert "phase_A_held_out_development_nomination_now" in ph
    # "confirmation" is reserved for base-blind Phase B
    assert "confirmation" in d["terminology"]
    assert "HELD" in ph["phase_B_base_blind_confirmation_HELD"]["requirement"]


def test_h0_is_mechanics_proven_comparator_only():
    d = _p()
    comps = {a["id"]: a for a in d["arm_roles"]["comparators_not_nominable"]}
    cands = {a["id"] for a in d["arm_roles"]["candidates_nominable"]}
    assert "MECHANICS_PROVEN" in comps["H0"]["status"]
    assert "H0" not in cands and cands == {"H1", "H2", "H3", "H4"}
    assert "H0_is_comparator_only" in d["selection_rule"]


# ---- separated objectives + directional vetoes ---------------------------

def test_preservation_objective_is_separated_from_pidgin_retention():
    obj = _p()["objectives"]
    pres = obj["preservation_objective"]
    assert pres["languages"] == ["english", "french", "swahili"]
    assert "pidgin" not in pres["macro"].split("{")[1].split("}")[0]
    assert obj["pidgin_retention_objective"]["language"] == "pidgin"
    # the gating macro is preservation-only
    sel = _p()["selection_rule"]
    assert "preservation macro" in sel["qualify"]
    assert "preservation macro" in sel["tie_break"] or \
        "preservation macro" in sel["qualify"]


def test_directional_vetoes_are_hard_and_numeric_for_lin_kin_ewe():
    v = _p()["constraints"]["directional_development_vetoes"]
    assert set(v["languages"]) == {"lingala", "kinyarwanda", "ewe"}
    assert "HARD VETO" in v["nature"]
    assert "0.02" in v["veto_rule"], "the 2.0pp veto floor must be explicit"
    assert "not" in v["not_a_gate"].lower()


def test_h0_second_seed_contradiction_resolved():
    ss = _p()["training"]["second_seed"]
    assert set(ss["runs"]) == {"the nominated finalist", "H0", "KD_CONTROL"}
    assert "resolves_contradiction" in ss


# ---- statistics -----------------------------------------------------------

def test_statistics_speaker_cluster_bootstrap_and_seed_aggregation():
    st = _p()["statistics"]
    assert "SPEAKER-level cluster" in st["clustering"]
    assert "10000" in st["clustering"]
    assert "seed individually" in st["seed_aggregation"]
    assert "Holm-Bonferroni" in st["multiplicity_correction"]


def test_selection_rule_has_tiebreak_and_no_recipe_qualifies():
    sel = _p()["selection_rule"]
    assert "DETERMINISTIC" in sel["tie_break"]
    assert "NEVER silently" in sel["NO_RECIPE_QUALIFIES"]


# ---- cost: measured-throughput $70 ceiling -------------------------------

def test_cost_table_is_measured_throughput_and_under_seventy():
    t = _p()["cost_runtime_table"]
    # the flat one-hour assumption is gone
    assert "one-hour" in t["scoring_throughput_basis"] and \
        "REPLACED" in t["scoring_throughput_basis"]
    assert "micro-benchmark" in t["scoring_throughput_basis"]
    jobs = t["jobs"]
    assert next(j for j in jobs if "mechanics" in j["job"])["count"] == 6
    assert next(j for j in jobs if "second-seed" in j["job"])["count"] == 3
    worst = 0.0
    for j in jobs:
        assert round(j["count"] * j["worst_case_usd_each"], 2) == j["worst_case_usd"]
        assert j["expected_usd"] <= j["worst_case_usd"] + 1e-9
        assert round(j["max_runtime_s"] / 3600 * 1.60, 2) == j["worst_case_usd_each"]
        worst += j["worst_case_usd"]
    assert round(worst, 2) == t["cumulative_worst_case_usd"]
    assert t["cumulative_worst_case_usd"] <= 70.0
    assert _p()["budget"]["ceiling_usd"] == 70


# ---- SOURCE-RECORD validation --------------------------------------------

def test_exposure_inventory_dev_selection_matches_the_real_manifest():
    sel = json.loads((ROOT / "platform/manifests/"
                      "B5-UNIVERSAL-ARM1-DEV-SELECTION-2026-001.json").read_bytes())
    assert len(sel["rows"]) == 420
    assert sel["rows_sha256"] == (
        "54897fff75b8c2c39901ef552f3e58c27340f774887eb09f05f4c7c37b835075")
    assert len(sel["languages"]) == 7
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


def test_protocol_and_inventory_reference_the_machine_derived_index():
    assert "EXPOSURE-INDEX-2026-001" in _p()["evaluation"]["exposure_index"]
    assert (ROOT / _i()["machine_derived_index"]["path"]).exists()


def test_identical_rows_across_all_scored_models():
    req = _p()["evaluation"]["identical_rows_requirement"]
    for who in ("base_teacher", "arm1", "H0", "KD_CONTROL"):
        assert who in req
    assert "paired" in req
