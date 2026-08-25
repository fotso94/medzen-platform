"""Regression tests for the Codex stage-1 correction round (2026-08-25):
finding 1 (tier by job class), finding 2 (matched RNG), finding 4a (KD-off
criteria honesty)."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from b5_sagemaker_job import (CALIBRATION_TIER_USD, STAGE1_STEP_BOUNDARY,
                              arm2_acceptance_criteria, is_campaign_arm_job,
                              render_request)                    # noqa: E402


COMPARATIVE = {"MEDZEN_EXECUTION_MODE": "arm2_comparative",
               "MEDZEN_KD_ENABLE": "1"}


# ------------------------------------------------------------- finding 1
def test_full_comparative_arm_is_campaign_tier_below_ten_dollars():
    env = dict(COMPARATIVE, MEDZEN_MAX_STEPS="2000")
    assert is_campaign_arm_job(env, 5.6) is True


def test_calibration_shaped_comparative_stays_below_tier():
    env = dict(COMPARATIVE, MEDZEN_MAX_STEPS=str(STAGE1_STEP_BOUNDARY))
    assert is_campaign_arm_job(env, 0.8) is False


def test_plain_training_keeps_price_tiering():
    assert is_campaign_arm_job({"MEDZEN_MAX_STEPS": "99999"}, 5.0) is False
    assert is_campaign_arm_job({}, CALIBRATION_TIER_USD + 0.01) is True


def test_unparseable_steps_on_comparative_fails_closed_to_arm():
    assert is_campaign_arm_job(dict(COMPARATIVE, MEDZEN_MAX_STEPS="x"),
                               1.0) is True
    assert is_campaign_arm_job(COMPARATIVE, 1.0) is True    # absent steps


def _tier_tag(request: dict) -> str:
    return next(t["Value"] for t in request["Tags"]
                if t["Key"] == "medzen-tier")


def test_committed_stage1_packets_render_arm_tier():
    for name in ("KDCONTROL", "H0", "H1", "H2", "H3", "H4"):
        packet = json.loads((ROOT / "platform/manifests" /
            f"B5-UNIVERSAL-ARM2-{name}-SAGEMAKER-BINDINGS-2026-001.json"
            ).read_bytes())
        assert _tier_tag(render_request(packet)) == "arm", name


def test_committed_thrubench_packet_stays_calibration_tier():
    packet = json.loads((ROOT / "platform/manifests" /
        "B5-UNIVERSAL-ARM2-THRUBENCH-SAGEMAKER-BINDINGS-2026-001.json"
        ).read_bytes())
    assert _tier_tag(render_request(packet)) == "calibration"


# ------------------------------------------------------------- finding 4a
BASE_SPEC = {"expected_steps": 30, "gpu_memory_ceiling_bytes": 23622320128,
             "dev_sentinel_languages": ["lingala", "swahili"],
             "required_preservation_coverage":
                 ["english", "french", "lingala", "swahili"]}


def test_kd_on_criteria_demand_positive_kd():
    lines = "\n".join(arm2_acceptance_criteria(dict(BASE_SPEC)))
    assert "KD positive and finite on every step" in lines
    assert "identically ZERO" not in lines


def test_kd_off_criteria_demand_zero_kd_and_no_coverage_requirement():
    lines = "\n".join(arm2_acceptance_criteria(
        dict(BASE_SPEC, kd_enabled=False)))
    assert "KD identically ZERO on every step" in lines
    assert "KD positive and finite" not in lines
    assert "no per-language KD coverage requirement" in lines


def test_kd_control_committed_packet_criteria_match_generator():
    packet = json.loads((ROOT / "platform/manifests" /
        "B5-UNIVERSAL-ARM2-KDCONTROL-SAGEMAKER-BINDINGS-2026-001.json"
        ).read_bytes())
    assert packet["result_verifier"].get("kd_enabled") is False
    assert packet["acceptance_criteria"] == \
        arm2_acceptance_criteria(packet["result_verifier"])
    joined = "\n".join(packet["acceptance_criteria"])
    assert "KD identically ZERO" in joined
    assert "KD positive and finite" not in joined


# ------------------------------------------------------------- finding 2
def test_reseed_matched_rng_realigns_kd_on_with_kd_off():
    torch = pytest.importorskip("torch")
    sys.path.insert(0, str(ROOT))
    from pipeline.omniasr_train import reseed_matched_rng

    seed = 20260820
    # KD-off arm: seed, no teacher, reseed
    torch.manual_seed(seed)
    reseed_matched_rng(seed)
    state_off = torch.get_rng_state()
    draw_off = torch.rand(4)

    # KD-on arm: seed, TEACHER LOAD CONSUMES RNG, reseed
    torch.manual_seed(seed)
    _ = torch.rand(37)                     # the teacher's RNG consumption
    reseed_matched_rng(seed)
    state_on = torch.get_rng_state()
    draw_on = torch.rand(4)

    assert torch.equal(state_off, state_on)
    assert torch.equal(draw_off, draw_on)


def test_without_reseed_the_trajectories_diverge():
    torch = pytest.importorskip("torch")
    seed = 20260820
    torch.manual_seed(seed)
    a = torch.rand(4)
    torch.manual_seed(seed)
    _ = torch.rand(37)
    b = torch.rand(4)
    assert not torch.equal(a, b)           # the bug the reseed fixes
