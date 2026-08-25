"""The Arm-2 KD comparison protocol + exposure inventory are DESIGN-REVIEW
artifacts (owner's two-review gate, step 1): they must be PENDING_DESIGN_REVIEW,
carry NO approval / execution / launch, and completely specify the experiment
(modes, four preservation + three retention constraints, all-seven evaluation,
fresh 2000-step runs, matched seeds/data-order, multiplicity correction,
deterministic tie-break, NO_RECIPE_QUALIFIES, confirmation-data rules, $60
no-retry ceiling). This test fails if the design is silently treated as
approved or if a required element is missing."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROTO = ROOT / "platform/decisions/B5-UNIVERSAL-ARM2-KD-COMPARISON-PROTOCOL-2026-001.json"
INV = ROOT / "platform/decisions/B5-UNIVERSAL-ARM2-EXPOSURE-INVENTORY-2026-001.json"


def _p():
    return json.loads(PROTO.read_bytes())


def test_protocol_is_pending_review_and_authorizes_nothing():
    d = _p()
    assert d["status"] == "PENDING_DESIGN_REVIEW"
    blob = json.dumps(d)
    # no approval / execution / launch may hide anywhere in the record
    assert '"decision": "APPROVED"' not in blob
    assert "APPROVED_EXECUTION" not in blob
    assert "NOT_AN_APPROVAL" in d
    # the two-review gate is explicit
    assert set(d["two_review_gate"]) == {"first_review", "second_review"}


def test_protocol_specifies_control_and_candidate_modes():
    d = _p()
    assert set(d["modes"]) == {"KD_CONTROL", "KD_CANDIDATE"}
    assert d["modes"]["KD_CONTROL"]["MEDZEN_KD_ENABLE"] == "0"
    assert d["modes"]["KD_CANDIDATE"]["MEDZEN_KD_ENABLE"] == "1"
    ids = {a["id"] for a in d["arms"]}
    assert "KD_CONTROL" in ids and {"H0", "H1", "H2", "H3", "H4"} <= ids


def test_four_preservation_and_three_retention_constraints():
    d = _p()
    langs = d["languages"]
    assert langs["base_preservation_gated"] == ["english", "french", "lingala", "swahili"]
    assert langs["arm1_retention_gated"] == ["pidgin", "kinyarwanda", "ewe"]
    assert len(langs["all_seven_evaluated"]) == 7
    c = d["constraints"]
    assert len(c["base_preservation_non_inferiority"]["languages"]) == 4
    assert len(c["arm1_retention"]["languages"]) == 3


def test_stage2_is_fresh_2000_steps_with_matched_seeds_and_second_seed_control():
    d = _p()
    s2 = d["stages"]["stage2_confirm"]
    assert s2["steps"] == 2000
    assert "FRESH" in s2["run_type"]
    assert "seed" in s2["matched"].lower() and "data order" in s2["matched"].lower()
    # the control, not just the finalist, gets a matched second seed
    assert "KD_CONTROL" in d["stages"]["second_seed_replicate"]


def test_statistics_and_selection_rule_are_complete():
    d = _p()
    assert "Holm-Bonferroni" in d["statistics"]["multiplicity_correction"]
    sel = d["selection_rule"]
    assert "tie_break" in sel and "DETERMINISTIC" in sel["tie_break"]
    assert "NO_RECIPE_QUALIFIES" in sel
    assert "NEVER silently" in sel["NO_RECIPE_QUALIFIES"]


def test_confirmation_split_is_untouched_and_not_yet_minted():
    d = _p()
    cs = d["confirmation_data_rules"]["confirmation_split"]
    assert "NOT YET MINTED" in cs["status"]
    rules = " ".join(cs["rules"])
    assert "audio_checksum_sha256" in rules
    assert "NEVER surfaced" in rules
    assert "sealed promotion holdout" in rules


def test_budget_is_sixty_dollar_no_retry_serial():
    d = _p()
    b = d["budget"]
    assert b["ceiling_usd"] == 60
    assert b["reservation"] == "serial (one job reserved at a time)"
    assert "NONE" in b["automatic_retries"]


def test_exposure_inventory_lists_used_surfaces_and_protects_the_holdout():
    inv = json.loads(INV.read_bytes())
    assert inv["status"] == "PENDING_DESIGN_REVIEW"
    used = {u["surface"] for u in inv["used_surfaces"]}
    assert any("dev-selection" in u for u in used)
    assert any("sentinel" in u for u in used)
    assert inv["sealed_never_touch"][0]["rule"].startswith("NEVER")
    assert inv["confirmation_split_requirements"]["status"] == "NOT YET MINTED"
