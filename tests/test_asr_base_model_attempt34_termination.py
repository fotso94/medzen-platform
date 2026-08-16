"""Attempt-34 hardening: flagged terminations are scored, bounded, not fatal.

Attempt 33 refused at row 1,203 because a single amharic clip drove whisper
into a capped decode and the pilot-era gate aborted the entire run. The gate
now records the flags the aggregate was always designed to count (cap_hits,
eos_failures), scores the truncated output, and fails closed only when a
(candidate, mode) pass exceeds the 20% misconfiguration bound.
"""

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services/asr-eval-runtime"))

from medzen_asr_eval.backends import Transcript
from medzen_asr_eval.harness import EvaluationRefusal
from medzen_asr_eval.pilot import run_pilot


class _StubSampler:
    samples: list[float]
    errors: list[str]

    def __init__(self):
        self.samples = [100.0, 120.0]
        self.errors = []

    def start(self):
        return None

    def stop(self):
        return None


class _FakeBackend:
    """Caps decoding for audio files whose content starts with b'CAP'."""

    def __init__(self, candidate):
        self.candidate = candidate

    def transcribe(self, audio, language_id):
        capped = audio.read_bytes().startswith(b"CAP")
        if capped and self.candidate == "omniASR_LLM_1B_v2":
            return Transcript(
                text="truncated runaway output",
                eos_observed=False,
                cap_hit=True,
                termination_evidence="max_new_tokens reached without EOS",
            )
        return Transcript(
            text="hello world",
            eos_observed=True,
            cap_hit=False,
            termination_evidence="eos token observed",
        )


def _write_inputs(tmp_path, capped_flags):
    rows = []
    for index, capped in enumerate(capped_flags):
        audio = tmp_path / f"clip-{index}.wav"
        audio.write_bytes((b"CAP" if capped else b"AUD") + str(index).encode())
        reference = "hello world"
        rows.append({
            "manifest": "testlang/asr/manifest.json",
            "language": "testlang",
            "source_id": f"src-{index}",
            "audio_local_path": str(audio),
            "audio_checksum_sha256": hashlib.sha256(audio.read_bytes()).hexdigest(),
            "duration_s": 2.5,
            "reference": reference,
            "reference_sha256": hashlib.sha256(reference.encode()).hexdigest(),
            "selection_ordinal": index,
        })
    rows_path = tmp_path / "runtime-rows.json"
    rows_path.write_text(json.dumps({
        "schema_version": 1,
        "classification": "PUBLIC_RESEARCH_NO_PHI",
        "rows": rows,
    }))
    conditioning_path = tmp_path / "conditioning.json"
    conditioning_path.write_text(json.dumps({
        "schema_version": 1,
        "policy": "proxy language identifiers are prohibited",
        "languages": {
            "testlang": {"dataset_iso": "tst", "meta_llm": None, "whisper": None},
        },
    }))
    return rows_path, conditioning_path


def _run(tmp_path, capped_flags):
    rows_path, conditioning_path = _write_inputs(tmp_path, capped_flags)
    return run_pilot(
        rows_path=rows_path,
        model_root=tmp_path,
        model_binding_path=tmp_path / "unused-bindings.json",
        receipt_root=tmp_path / "rows",
        aggregate_path=tmp_path / "aggregate.json",
        conditioning_path=conditioning_path,
        backend_loader=lambda candidate, mode, language, root: _FakeBackend(candidate),
        model_verifier=lambda root, binding: {"stub": True},
        sampler=_StubSampler(),
    )


def test_single_cap_hit_is_scored_and_counted_not_fatal(tmp_path):
    result = _run(tmp_path, [True, False, False, False, False, False])
    groups = result["aggregate"]["groups"]
    whisper = groups["omniASR_LLM_1B_v2|unconditioned"]
    assert whisper["rows"] == 6
    assert whisper["cap_hits"] == 1
    assert whisper["eos_failures"] == 1
    assert groups["omniASR_CTC_1B_v2|unconditioned"]["cap_hits"] == 0
    rows_written = [
        json.loads(path.read_bytes())
        for path in (tmp_path / "rows").iterdir()
        if not path.name.startswith("backend-load-")
    ]
    scored_flagged = [
        row for row in rows_written
        if row.get("cap_hit") and row.get("status") == "PASS_ROW_INFERENCE"
    ]
    assert scored_flagged and all(
        row["reason_code"] == "TERMINATION_FLAGGED_ROW_SCORED" for row in scored_flagged
    )


def test_flagged_fraction_beyond_the_bound_fails_closed(tmp_path):
    with pytest.raises(EvaluationRefusal, match="misconfiguration bound"):
        _run(tmp_path, [True, True, False, False, False])


def test_small_passes_never_trip_the_bound(tmp_path):
    result = _run(tmp_path, [True, False, False, False])
    assert result["aggregate"]["groups"]["omniASR_LLM_1B_v2|unconditioned"]["cap_hits"] == 1
