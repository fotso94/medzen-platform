"""A6 conformance matrix (Base v5 §A6, plan task B).

Two enforcement layers:
  1. the resilience configuration must carry the A6 spec numbers EXACTLY —
     a drifted number is a spec violation, not a tuning choice;
  2. every A6 concern must map to at least one existing test — the matrix
     below names them, and this suite fails if a named test disappears,
     so coverage cannot silently rot.
"""

import ast
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
RESILIENCE = yaml.safe_load((ROOT / "platform/resilience.yaml").read_text())


# --------------------------------------------------------------------------
# Layer 1 — the A6 numbers, verbatim from the Base v5 table
# --------------------------------------------------------------------------

def test_backpressure_queues_match_a6():
    queues = RESILIENCE["backpressure"]["queues"]
    assert queues["partial_transcripts"] == {"max": 4, "overflow": "drop_oldest"}
    assert queues["audio_chunks"] == {"max": 8, "overflow": "pause_upstream"}
    assert queues["final_results"]["overflow"] == "block", "finals are NEVER dropped"


def test_cancellation_budget_is_250ms_to_all_three_targets():
    cancellation = RESILIENCE["cancellation"]
    assert cancellation["propagate_within_ms"] == 250
    assert set(cancellation["targets"]) == {"asr", "llm", "tts"}
    assert "barge_in" in cancellation["triggers"]


def test_per_component_timeouts_match_a6_and_are_not_inherited():
    timeouts = RESILIENCE["timeouts"]
    assert timeouts["asr"]["partial_stall_ms"] == 1500
    assert timeouts["llm"]["first_token_ms"] == 5000
    assert timeouts["tts"]["first_byte_ms"] == 2000
    assert timeouts["tts"]["chunk_gap_ms"] == 3000


def test_retries_are_single_attempt_with_jitter_idempotent_only():
    retries = RESILIENCE["retries"]
    assert retries["max_attempts"] == 1
    assert retries["idempotent_only"] is True
    assert retries["backoff"]["jitter_ms"] > 0


def test_breaker_defaults_match_a6():
    defaults = RESILIENCE["circuit_breakers"]["defaults"]
    assert defaults["failure_threshold"] == 5
    assert defaults["timeout_threshold"] == 3
    assert defaults["window_s"] == 30
    assert defaults["half_open_max_calls"] == 1
    observability = RESILIENCE["circuit_breakers"]["observability"]
    assert observability["include_in_readyz"] is True
    assert observability["export_state_as_metric"] is True


def test_tts_fallback_chain_terminates_in_text_only_success():
    chain = RESILIENCE["fallback_chains"]["tts"]
    assert chain["terminal"] == "text_only"
    assert [step["provider"] for step in chain["chain"]] == [
        "fish", "self_hosted", "text_only"]


def test_llm_chain_has_no_textual_fallback():
    assert RESILIENCE["fallback_chains"]["llm"]["terminal"] is None


def test_streaming_limits_hardcode_the_same_numbers():
    """The runtime dataclass must refuse any drift from 4/8/16 and 250ms."""
    body = (ROOT / "services/speech-orchestrator/medzen_speech_orchestrator/"
            "streaming.py").read_text()
    for marker in ("partial_queue_size != 4", "audio_queue_size != 8",
                   "final_queue_size != 16", "<= 0.250"):
        assert marker in body, f"streaming.py lost its A6 guard: {marker}"


# --------------------------------------------------------------------------
# Layer 2 — every A6 concern maps to a living test
#
# Deliberately absent: a behavioral test for "retries: max 1, idempotent
# only". No online service implements a retry loop (verified 2026-08-16),
# so max_attempts=1 is trivially satisfied; the layer-1 config pin above is
# the only meaningful guard. If a retry loop is ever added, it needs a
# behavioral test AND a matrix entry here.
# --------------------------------------------------------------------------

MATRIX = {
    "backpressure: bounded queues, finals preserved": [
        ("tests/test_b6_7_drills.py",
         "test_partial_queue_drops_oldest_and_finals_never_dropped"),
    ],
    "cancellation: 250ms propagation incl. barge-in": [
        ("tests/test_speech_orchestrator_streaming.py",
         "test_cancel_and_barge_in_propagate_and_close_within_250_ms"),
    ],
    "timeouts: per-component, none inherited": [
        ("tests/test_speech_orchestrator_streaming.py",
         "test_processing_control_timeout_cancels_and_reaps_pipeline_task"),
    ],
    "breaker state visible in /readyz": [
        ("tests/test_speech_orchestrator_streaming.py",
         "test_open_streaming_breaker_is_visible_in_readiness"),
    ],
    "tts failure preserves the text answer (text_only success)": [
        ("tests/test_tts_gateway.py",
         "test_fish_failure_is_a_text_preserving_success"),
        ("tests/test_tts_gateway.py",
         "test_three_timeouts_open_breaker_and_fourth_request_skips_provider"),
    ],
    "drill: kill Fish -> text_only": [
        ("tests/test_b6_7_drills.py", "test_drill_kill_fish_degrades_to_text_only"),
    ],
    "drill: kill ASR mid-stream -> clean session error": [
        ("tests/test_b6_7_drills.py", "test_drill_asr_death_mid_stream_errors_cleanly"),
    ],
    "drill: open LLM breaker -> controlled error, no invention": [
        ("tests/test_b6_7_drills.py", "test_drill_open_llm_breaker_controlled_error"),
    ],
}


def _test_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    return {node.name for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test_")}


def test_every_a6_concern_maps_to_a_living_test():
    missing = []
    for concern, refs in MATRIX.items():
        for rel, name in refs:
            path = ROOT / rel
            if not path.is_file() or name not in _test_names(path):
                missing.append(f"{concern} -> {rel}::{name}")
    assert not missing, "A6 concerns without a living test:\n" + "\n".join(missing)
