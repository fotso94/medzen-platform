#!/usr/bin/env python3
"""Live contract probe for a DEPLOYED orchestrator (task F; B6 row 3).

The committed contract suites prove the services against local apps;
this probe proves the same §7-v4 contracts against a live endpoint:

  --smoke   one file-mode transcription round-trip (deploy step 1 exit)
  --full    smoke + WebSocket streaming contract + model-version
            completeness + OTel request-id echo (deploy step 3 exit)

Synthetic non-clinical audio only (the committed PCM fixture); the
probe refuses to run without an explicit target URL and never invents
endpoints. Exit 0 only on PASS.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import struct
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_VERSION_FIELDS = ("asr", "registry_snapshot", "llm", "rag", "tts")


class ProbeFailure(RuntimeError):
    pass


def synthetic_wav(seconds: float = 1.0, rate: int = 16000) -> bytes:
    """The committed synthetic fixture when present (the local pipeline
    is fixture-keyed); otherwise a generated non-speech tone."""
    fixture = ROOT / "platform/testdata/orchestrator/synthetic-file-request.wav"
    if fixture.is_file():
        return fixture.read_bytes()
    import math
    frames = int(seconds * rate)
    samples = b"".join(
        struct.pack("<h", int(3000 * math.sin(2 * math.pi * 440 * i / rate)))
        for i in range(frames))
    header = (b"RIFF" + struct.pack("<I", 36 + len(samples)) + b"WAVEfmt "
              + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
              + b"data" + struct.pack("<I", len(samples)))
    return header + samples


def probe_file_mode(base_url: str, session) -> dict:
    request_id = str(uuid.uuid4())
    response = session.post(
        f"{base_url}/v1/conversations/speech",
        data={"request_id": request_id, "language_hint": "en",
              "response_audio": "false"},
        files={"audio": ("probe.wav", io.BytesIO(synthetic_wav()), "audio/wav")},
        timeout=60)
    if response.status_code != 200:
        raise ProbeFailure(f"file-mode returned {response.status_code}: "
                           f"{response.text[:200]}")
    payload = response.json()
    versions = payload.get("model_versions") or {}
    missing = [f for f in REQUIRED_VERSION_FIELDS if f not in versions]
    if missing:
        raise ProbeFailure(f"response lacks model versions {missing} — "
                           "the contract requires every response to carry all")
    echoed = response.headers.get("x-request-id") or payload.get("request_id")
    if echoed != request_id:
        raise ProbeFailure("request id does not round-trip — OTel tracing "
                           "cannot join this request")
    return {"file_mode": "PASS", "request_id": request_id,
            "model_versions": versions}


def probe_streaming(base_url: str) -> dict:
    from websockets.sync.client import connect
    ws_url = base_url.replace("https://", "wss://").replace("http://", "ws://")
    request_id = str(uuid.uuid4())
    token = os.environ.get("MEDZEN_ORCHESTRATOR_TOKEN", "")
    with connect(f"{ws_url}/v1/conversations/stream",
                 additional_headers={
                     "x-request-id": request_id,
                     "Authorization": f"Bearer {token}",
                     "X-MedZen-Contract-Version": "medzen.speech.v1"},
                 open_timeout=30, close_timeout=30) as ws:
        ws.send(json.dumps({"type": "start", "request_id": request_id,
                            "language_hint": "en", "response_audio": False}))
        ready = json.loads(ws.recv(timeout=30))
        if ready.get("type") != "ready":
            raise ProbeFailure(f"streaming did not open with ready: {ready}")
        # stream contract: RAW pcm_s16le/16000/mono frames <=64KiB each —
        # never a WAV container (the header poisons the pipeline; r-f review)
        pcm = synthetic_wav()[44:]
        for offset in range(0, len(pcm), 32000):
            ws.send(pcm[offset:offset + 32000])
        first = json.loads(ws.recv(timeout=30))
        if first.get("type") not in {"partial_transcript", "final_transcript"}:
            raise ProbeFailure(f"unexpected first stream event: {first}")
        ws.send(json.dumps({"type": "end_of_speech"}))
        # contract sequence: ready, partial_transcript (optional),
        # final_transcript, reply_text, completed
        terminal = None
        saw = []
        for _ in range(20):
            event = json.loads(ws.recv(timeout=30))
            saw.append(event.get("type"))
            if event.get("type") in {"completed", "error", "cancelled"}:
                terminal = event
                break
        if terminal is None or terminal.get("type") != "completed":
            raise ProbeFailure(f"stream did not complete (saw {saw}): {terminal}")
        if "final_transcript" not in saw or "reply_text" not in saw:
            raise ProbeFailure(f"contract sequence incomplete: {saw}")
        versions = terminal.get("model_versions") or {}
        missing = [f for f in REQUIRED_VERSION_FIELDS if f not in versions]
        if missing:
            raise ProbeFailure(f"streaming terminal lacks versions {missing}")
    return {"streaming": "PASS", "request_id": request_id}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args()
    base_url = os.environ.get("MEDZEN_ORCHESTRATOR_URL", "").rstrip("/")
    token = os.environ.get("MEDZEN_ORCHESTRATOR_TOKEN", "")
    if not base_url or not token:
        print(json.dumps({"status": "REFUSED",
                          "detail": "MEDZEN_ORCHESTRATOR_URL and "
                                    "MEDZEN_ORCHESTRATOR_TOKEN are required — "
                                    "this probe never invents endpoints or "
                                    "credentials"}))
        return 2
    import requests
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {token}",
        "X-MedZen-Contract-Version": "medzen.speech.v1",
    })
    results = {"target": base_url}
    try:
        results.update(probe_file_mode(base_url, session))
        if args.full:
            results.update(probe_streaming(base_url))
        results["status"] = "PASS_LIVE_CONTRACTS" if args.full else "PASS_SMOKE"
        print(json.dumps(results, sort_keys=True))
        return 0
    except ProbeFailure as exc:
        results["status"] = "FAIL_LIVE_CONTRACTS"
        results["detail"] = str(exc)
        print(json.dumps(results, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
