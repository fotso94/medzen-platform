"""ARCH-2026-001 governance: the records are machine-consumed (Codex
review #6: 'no tests or scripts consume the new protocol'). These tests
bind the architecture's load-bearing statements so silent drift fails CI."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCH = json.loads((ROOT / "platform/decisions/"
                   "ARCH-2026-001-one-multilingual-production-model.json").read_bytes())
PROTOCOL = json.loads((ROOT / "platform/decisions/"
                       "PROMOTION-PROTOCOL-2026-001.json").read_bytes())
PILOT = (ROOT / "platform/decisions/"
         "B5-UNIVERSAL-PILOT-DESIGN-2026-001.md").read_text()


def test_architecture_record_binds_the_owner_decision():
    assert ARCH["status"] == "OWNER_DECIDED"
    arch = ARCH["architecture"]
    assert "ONE active multilingual" in arch["production_artifact"]
    assert arch["language_routing"].startswith("NO external")
    assert "MANDATORY" in arch["language_hints"]
    assert "RESEARCH TEACHERS ONLY" in arch["per_language_models"]
    assert "no kinyarwanda-specific serving path" in (
        ARCH["kinyarwanda_reframe"]["serving_path"])


def test_the_two_variant_deployment_decision_is_superseded():
    base_model = (ROOT / "platform/decisions/"
                  "B6-ASR-BASE-MODEL-2026-001.md").read_text()
    assert "SUPERSESSION NOTE" in base_model
    assert "ARCH-2026-001" in base_model
    assert "OFFLINE accuracy comparator only" in base_model


def test_pilot_design_declares_gb6_and_two_tier_evaluation():
    assert "gb6" in PILOT, "the pilot must not claim gb5 suffices"
    assert "trainable for kinyarwanda ONLY" in PILOT
    assert "TIER 2 (promotion)" in PILOT
    assert "consumed EXACTLY ONCE" in PILOT
    for language in ("kinyarwanda", "english", "french", "swahili",
                     "lingala", "ewe"):
        assert language in PILOT


def test_promotion_protocol_gates_are_bound():
    assert any("PREDECLARES" in p for p in PROTOCOL["principles"])
    assert "MANDATORY FOR THE FIRST PRODUCTION PROMOTION" in (
        PROTOCOL["gates_phased_blocking_when_assets_exist"]["code_switch"])
    assert "aggregate WER alone can NEVER promote" in PROTOCOL["principles"][0]
    assert "test_arch_2026_001" in PROTOCOL["machine_enforcement"]


def test_trainer_enforces_what_the_pilot_promises():
    """The two machine gates the pilot leans on must exist in code, not
    prose: the multilingual-full ack and the coverage refusal."""
    trainer = (ROOT / "pipeline/omniasr_train.py").read_text()
    assert "MEDZEN_MULTILINGUAL_FULL_ACK" in trainer
    mix = (ROOT / "pipeline/train_asr.py").read_text()
    assert "a partial mixture" in mix


def test_no_per_language_serving_digest_may_enter_the_registry():
    """ARCH-2026-001 one-global-digest tripwire (Codex review #6 rec 6):
    language registry files must never grow per-language serving-artifact
    digest bindings — the production digest is bound once, globally, at
    the serving-contract step. Full enforcement arrives with that step;
    this trips any earlier drift."""
    import yaml
    forbidden = {"serving_artifact", "asr_digest", "model_digest",
                 "artifact_digest", "serving_model"}
    for path in sorted((ROOT / "registry/languages").glob("*.yaml")):
        doc = yaml.safe_load(path.read_text()) or {}
        def walk(node, trail):
            if isinstance(node, dict):
                for key, value in node.items():
                    assert key not in forbidden, (
                        f"{path.name}: {'.'.join(trail + [key])} — "
                        "per-language serving digests violate ARCH-2026-001")
                    walk(value, trail + [key])
            elif isinstance(node, list):
                for item in node:
                    walk(item, trail)
        walk(doc, [])


def test_tier2_holdout_record_binds_every_pilot_language():
    holdouts = json.loads((ROOT / "platform/evidence/"
                           "B5-TIER2-HOLDOUTS-2026-001.json").read_bytes())
    assert set(holdouts["pools"]) == {"english", "ewe", "french", "lingala",
                                       "swahili"}
    for language, pools in holdouts["pools"].items():
        for pool in pools:
            assert pool["sealed_vs_gb6_overlap"]["byte"] == 0
            assert pool["sealed_vs_gb6_overlap"]["session"] == 0
            assert pool["tier2-sealed"]["rows"] > 0
    assert "french_aaf_text_overlap" in holdouts["honest_limitations"]


def test_gb6_covers_every_pilot_language_physically():
    gb6 = json.loads((ROOT / "platform/evidence/"
                      "B5-GB6-COMPLETE-2026-001.json").read_bytes())
    covered = {k.split("/")[0] for k in gb6["manifests"]}
    assert {"kinyarwanda", "english", "french", "swahili", "lingala",
            "ewe"} <= covered
    for entry in gb6["manifests"].values():
        assert "/gb6/manifest.jsonl" in entry["key"], (
            "gb6 entries must be PHYSICAL /gb6/ paths — the trainer "
            "resolves by path")
