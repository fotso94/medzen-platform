from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

from scripts.generate_b6_websocket_qualification_fixtures import products


ROOT = Path(__file__).resolve().parents[1]
RECEIPT = (
    ROOT
    / "platform/evidence/b6-websocket-runtime/"
    / "medzen-orchestrator.full-conversation.json"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_exact_window_conversation_pass_is_bound_to_probe_and_runtime_app():
    receipt = json.loads(RECEIPT.read_bytes())
    conversation = receipt["websocket_conversation"]
    assert receipt["status"] == "PASS"
    assert receipt["image_id"] == (
        "sha256:a3bd7170dbef4541ff6286324974a79d0b0da2287dcdcaf8f77a20654c7befed"
    )
    assert conversation["exact_window_probe"] == (
        "scripts/b6_6_probe.py websocket"
    )
    assert conversation["event_types"][0] == "ready"
    assert conversation["event_types"].count("partial_transcript") == 5
    assert conversation["event_types"][-3:] == [
        "final_transcript",
        "reply_text",
        "completed",
    ]
    assert conversation["final_result_preserved"] is True
    binding = conversation["probe_app_binding"]
    authorization = json.loads((
        ROOT / "platform/decisions/B6-AWS-AUTH-2026-032A-window.json"
    ).read_bytes())
    probe_source = subprocess.run(
        [
            "git",
            "show",
            f"{authorization['independent_review']['reviewed_repository_commit']}:"
            "scripts/b6_6_probe.py",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    assert binding["probe_sha256"] == hashlib.sha256(probe_source).hexdigest()
    runtime_source = subprocess.run(
        [
            "git",
            "show",
            f"{receipt['source_commit']}:services/speech-orchestrator/"
            "medzen_speech_orchestrator/streaming_app.py",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    assert binding["runtime_app_sha256"] == hashlib.sha256(
        runtime_source
    ).hexdigest()
    pair = {
        "probe_sha256": binding["probe_sha256"],
        "runtime_app_sha256": binding["runtime_app_sha256"],
    }
    assert binding["pair_sha256"] == hashlib.sha256(
        json.dumps(pair, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def test_qualification_overlays_are_generated_and_historical_fixtures_unchanged():
    receipt = json.loads(RECEIPT.read_bytes())
    bindings = receipt["websocket_conversation"]["fixture_bindings"]
    for path, expected in products().items():
        assert path.read_bytes() == expected
    assert bindings == {
        "asr_fixture_sha256": sha256(
            ROOT / "platform/testdata/orchestrator/b6-window-asr-fixture.json"
        ),
        "proof_audio_sha256": sha256(
            ROOT / "platform/testdata/b6a-003c-b-synthetic.wav"
        ),
        "registry_fixture_sha256": sha256(
            ROOT / "platform/testdata/registry-ssm/b6-window-websocket-v1.json"
        ),
    }
    assert sha256(
        ROOT / "platform/testdata/orchestrator/synthetic-file-request.wav"
    ) == "97592cb9f83e38439ea9d7ff1841e502bf1ef5b60be096dd91ac80a320e5402b"
    historical = json.loads(
        (
            ROOT / "platform/testdata/registry-ssm/b6-local-v1.json"
        ).read_bytes()
    )
    assert "97592cb9f83e38439ea9d7ff1841e502bf1ef5b60be096dd91ac80a320e5402b" in (
        historical["parameters"][2]["Value"]
    )
