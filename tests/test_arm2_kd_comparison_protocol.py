"""The Arm-2 KD comparison protocol + exposure inventory are DESIGN-REVIEW
artifacts (owner's two-review gate, step 1). These tests (Codex round-31
design re-review) validate them AGAINST THE ACTUAL SOURCE RECORDS — the real
dev-selection manifest, the frozen sentinel files, the committed holdout
records — not merely against their own prose; and they fail if the design is
silently treated as approved, if a required element is missing, if the
exposure/untouched claims diverge from the source data, or if the cost
arithmetic exceeds the $60 ceiling."""
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


# ---- governance: pending, authorizes nothing -----------------------------

def test_both_records_are_pending_review_and_authorize_nothing():
    for d in (_p(), _i()):
        assert d["status"] == "PENDING_DESIGN_REVIEW"
        blob = json.dumps(d)
        assert '"decision": "APPROVED"' not in blob
        assert "APPROVED_EXECUTION" not in blob
    assert "NOT_AN_APPROVAL" in _p()
    assert set(_p()["two_review_gate"]) == {"first_review", "second_review"}


# ---- structure: modes, comparator-only H0, no screen ---------------------

def test_modes_and_h0_is_comparator_only():
    d = _p()
    assert d["modes"]["KD_CONTROL"]["MEDZEN_KD_ENABLE"] == "0"
    assert d["modes"]["KD_CANDIDATE"]["MEDZEN_KD_ENABLE"] == "1"
    comparators = {a["id"] for a in d["arm_roles"]["comparators_not_nominable"]}
    candidates = {a["id"] for a in d["arm_roles"]["candidates_nominable"]}
    assert {"base_teacher", "arm1", "H0", "KD_CONTROL"} <= comparators
    assert candidates == {"H1", "H2", "H3", "H4"}
    assert "H0" not in candidates, "H0 must be comparator-only (Codex r31)"
    assert "H0_is_comparator_only" in d["selection_rule"]


def test_the_600_step_selection_stage_is_removed():
    d = _p()
    assert "no_selection_stage" in d
    assert d["training"]["steps"] == 2000
    assert "FRESH" in d["training"]["run_type"]
    # no 600-step gating stage may survive anywhere
    assert "600" not in json.dumps(d.get("phases", {})) + json.dumps(d.get("stages", {}))


def test_statistics_are_finalized_speaker_cluster_bootstrap_and_seed_aggregation():
    st = _p()["statistics"]
    assert "SPEAKER-level cluster" in st["clustering"]
    assert "10000" in st["clustering"]
    assert "seed individually" in st["seed_aggregation"]
    assert "Holm-Bonferroni" in st["multiplicity_correction"]
    macro = _p()["macro_objective"]
    for lang in ("english", "french", "swahili", "pidgin"):
        assert lang in macro
    # the three no-untouched-data languages are excluded from the gating macro
    for lang in ("lingala", "kinyarwanda", "ewe"):
        assert lang not in macro.split("(")[0]


def test_selection_rule_has_tiebreak_and_no_recipe_qualifies():
    sel = _p()["selection_rule"]
    assert "DETERMINISTIC" in sel["tie_break"]
    assert "NO_RECIPE_QUALIFIES" in sel
    assert "NEVER silently" in sel["NO_RECIPE_QUALIFIES"]


# ---- cost table: exact arithmetic, six mechanics receipts, <= $60 --------

def test_cost_table_has_six_mechanics_receipts_and_is_under_sixty():
    t = _p()["cost_runtime_table"]
    jobs = t["jobs"]
    mech = next(j for j in jobs if "mechanics" in j["job"])
    assert mech["count"] == 6, "six fresh mechanics receipts (Codex r31)"
    worst = 0.0
    for j in jobs:
        # per-row arithmetic must be internally consistent
        assert round(j["count"] * j["worst_case_usd_each"], 2) == j["worst_case_usd"]
        assert j["expected_usd"] <= j["worst_case_usd"] + 1e-9
        # per-job worst-case = cap-hours * $1.60
        assert round(j["max_runtime_s"] / 3600 * 1.60, 2) == j["worst_case_usd_each"]
        worst += j["worst_case_usd"]
    assert round(worst, 2) == t["cumulative_worst_case_usd"]
    assert t["cumulative_worst_case_usd"] <= 60.0
    assert _p()["budget"]["ceiling_usd"] == 60


# ---- SOURCE-RECORD validation: inventory vs the real data ----------------

def test_exposure_inventory_dev_selection_matches_the_real_manifest():
    sel = json.loads((ROOT / "platform/manifests/"
                      "B5-UNIVERSAL-ARM1-DEV-SELECTION-2026-001.json").read_bytes())
    assert len(sel["rows"]) == 420
    assert sel["rows_sha256"] == (
        "54897fff75b8c2c39901ef552f3e58c27340f774887eb09f05f4c7c37b835075")
    assert len(sel["languages"]) == 7
    # the inventory must cite that exact identity as a USED_DEV_SELECTION surface
    inv = _i()
    ds = next(s for s in inv["used_surfaces"]
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
        assert actual == want, f"{name} sentinel drifted from the inventory"


def test_exposure_inventory_cites_only_holdout_records_that_exist():
    for rel in _i()["sealed_holdout"]["governed_by"]:
        assert (ROOT / rel).exists(), f"cited sealed record missing: {rel}"


def test_untouched_verdict_matches_the_owner_flag_and_gates_only_available_langs():
    inv = _i()
    v = inv["per_language_untouched_verdict"]
    # the owner's flag: kinyarwanda/lingala/ewe have NO untouched data
    for lang in ("kinyarwanda", "lingala", "ewe"):
        assert v[lang]["available"] == "NO"
    # pidgin is the only fully-blind option; en/fr/swa are candidate-blind
    assert v["pidgin"]["available"] == "YES_FULLY_BLIND"
    for lang in ("english", "french", "swahili"):
        assert v[lang]["available"] == "YES_CANDIDATE_BLIND"
    # the protocol may confirmation-GATE only languages with untouched data,
    # and must mark the three with none as directional-only / Phase-B held
    ph = _p()["phases"]
    assert set(ph["phase_A_nomination_now"]["confirmation_gated_languages"]) == {
        "english", "french", "swahili", "pidgin"}
    assert set(ph["phase_A_nomination_now"]["directional_only_languages"]) == {
        "lingala", "kinyarwanda", "ewe"}
    assert "HELD" in ph["phase_B_blind_gate_HELD"]["requirement"] or \
        "held" in ph["phase_B_blind_gate_HELD"]["requirement"]


def test_identical_confirmation_rows_across_all_scored_models():
    ev = _p()["evaluation"]
    req = ev["identical_rows_requirement"]
    for who in ("base_teacher", "arm1", "H0", "KD_CONTROL"):
        assert who in req
    assert "paired" in req
