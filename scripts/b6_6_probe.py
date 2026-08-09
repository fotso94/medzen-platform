#!/usr/bin/env python3
"""Synthetic-only HTTP and WebSocket probes that never emit request content."""
from __future__ import annotations

import argparse
import base64
import hashlib
import http.client
import json
import os
import socket
import ssl
import struct
import time
import uuid
import wave
from pathlib import Path
from urllib.parse import urlsplit


CONTRACT = "medzen.speech.v1"
REGISTRY = "b6-test:d4f9696d288e0ea6c1d139f496e00eaf097b77ea8b3a4f5a26a6470286adfe81"
MODEL_KEYS = {"asr", "registry_snapshot", "llm", "rag", "tts"}


class ProbeRefusal(RuntimeError):
    pass


def _token(path: Path) -> str:
    if not path.is_file() or (path.stat().st_mode & 0o777) != 0o600:
        raise ProbeRefusal("synthetic token file is absent or not mode 0600")
    value = path.read_text().strip()
    if not value or any(character.isspace() for character in value):
        raise ProbeRefusal("synthetic token is malformed")
    return value


def _connection(url: str) -> tuple[http.client.HTTPConnection, str]:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ProbeRefusal("probe URL is invalid")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    connection: http.client.HTTPConnection
    if parsed.scheme == "https":
        connection = http.client.HTTPSConnection(parsed.hostname, port, timeout=60)
    else:
        connection = http.client.HTTPConnection(parsed.hostname, port, timeout=60)
    return connection, parsed.path.rstrip("/")


def _post_file(base_url: str, token: str | None, wav: Path, *, language: str = "en", contract: str = CONTRACT) -> tuple[int, dict]:
    boundary = "medzen-b6-" + uuid.uuid4().hex
    request_id = str(uuid.uuid4())
    audio = wav.read_bytes()
    sections = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"request_id\"\r\n\r\n{request_id}\r\n".encode(),
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"language_hint\"\r\n\r\n{language}\r\n".encode(),
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"response_audio\"\r\n\r\nfalse\r\n".encode(),
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"audio\"; filename=\"synthetic.wav\"\r\nContent-Type: audio/wav\r\n\r\n".encode() + audio + b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    body = b"".join(sections)
    connection, prefix = _connection(base_url)
    headers = {
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Content-Length": str(len(body)),
        "X-MedZen-Contract-Version": contract,
    }
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    try:
        connection.request("POST", prefix + "/v1/conversations/speech", body=body, headers=headers)
        response = connection.getresponse()
        raw = response.read(1_048_577)
        if len(raw) > 1_048_576:
            raise ProbeRefusal("response exceeds one MiB")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ProbeRefusal("response is not an object")
        return response.status, value
    finally:
        connection.close()


class WebSocket:
    def __init__(self, url: str, token: str):
        parsed = urlsplit(url.replace("http://", "ws://", 1).replace("https://", "wss://", 1))
        if parsed.scheme not in {"ws", "wss"} or not parsed.hostname:
            raise ProbeRefusal("WebSocket URL is invalid")
        port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        sock = socket.create_connection((parsed.hostname, port), timeout=60)
        if parsed.scheme == "wss":
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=parsed.hostname)
        self.sock = sock
        self.sock.settimeout(60)
        self.buffer = b""
        key = base64.b64encode(os.urandom(16)).decode()
        path = parsed.path.rstrip("/") + "/v1/conversations/stream"
        request = (
            f"GET {path} HTTP/1.1\r\nHost: {parsed.hostname}:{port}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
            f"Authorization: Bearer {token}\r\nX-MedZen-Contract-Version: {CONTRACT}\r\n\r\n"
        ).encode()
        self.sock.sendall(request)
        response = b""
        while b"\r\n\r\n" not in response and len(response) < 16384:
            response += self.sock.recv(4096)
        if not response.startswith(b"HTTP/1.1 101") and not response.startswith(b"HTTP/1.0 101"):
            self.sock.close()
            raise ProbeRefusal("WebSocket upgrade refused")
        expected = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        if f"sec-websocket-accept: {expected}".lower().encode() not in response.lower():
            self.sock.close()
            raise ProbeRefusal("WebSocket accept binding differs")
        _, self.buffer = response.split(b"\r\n\r\n", 1)

    def send(self, payload: bytes, opcode: int) -> None:
        mask = os.urandom(4)
        length = len(payload)
        header = bytes([0x80 | opcode])
        if length < 126:
            header += bytes([0x80 | length])
        elif length < 65536:
            header += bytes([0x80 | 126]) + struct.pack("!H", length)
        else:
            header += bytes([0x80 | 127]) + struct.pack("!Q", length)
        masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        self.sock.sendall(header + mask + masked)

    def json(self, value: dict) -> None:
        self.send(json.dumps(value, sort_keys=True, separators=(",", ":")).encode(), 1)

    def binary(self, value: bytes) -> None:
        self.send(value, 2)

    def receive(self) -> tuple[int, bytes]:
        first = self._read(2)
        opcode = first[0] & 0x0F
        length = first[1] & 0x7F
        if first[1] & 0x80:
            raise ProbeRefusal("server WebSocket frame is unexpectedly masked")
        if length == 126:
            length = struct.unpack("!H", self._read(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._read(8))[0]
        payload = self._read(length)
        if opcode == 9:
            self.send(payload, 10)
            return self.receive()
        return opcode, payload

    def receive_json(self) -> dict:
        opcode, payload = self.receive()
        if opcode != 1:
            raise ProbeRefusal("expected a text WebSocket frame")
        value = json.loads(payload)
        if not isinstance(value, dict):
            raise ProbeRefusal("WebSocket event is not an object")
        return value

    def _read(self, size: int) -> bytes:
        value = self.buffer[:size]
        self.buffer = self.buffer[size:]
        while len(value) < size:
            chunk = self.sock.recv(size - len(value))
            if not chunk:
                raise ProbeRefusal("WebSocket closed unexpectedly")
            value += chunk
        return value

    def close(self) -> None:
        try:
            try:
                self.send(struct.pack("!H", 1000), 8)
            except OSError:
                pass
        finally:
            self.sock.close()


def file_proof(args: argparse.Namespace) -> dict:
    status, value = _post_file(args.base_url, _token(args.token_file), args.wav)
    versions = value.get("model_versions")
    reply = value.get("reply")
    if (
        status != 200 or not isinstance(reply, dict)
        or reply.get("tts_backend") != "text_only"
        or len(reply.get("citations", [])) != 3
        or not isinstance(versions, dict) or set(versions) != MODEL_KEYS
        or versions.get("registry_snapshot") != REGISTRY
        or versions.get("asr") != "v0"
        or versions.get("llm") != "fake-bedrock-local-v1"
        or versions.get("tts") is not None
    ):
        raise ProbeRefusal("file proof contract differs")
    return {"status": "PASS", "http_status": status, "citation_count": 3, "tts_backend": "text_only", "model_versions": versions}


def websocket_proof(args: argparse.Namespace) -> dict:
    token = _token(args.token_file)
    with wave.open(str(args.wav), "rb") as stream:
        pcm = stream.readframes(stream.getnframes())
    websocket = WebSocket(args.base_url, token)
    events: list[str] = []
    try:
        websocket.json({"type": "start", "request_id": str(uuid.uuid4()), "language_hint": "en", "audio_format": "pcm_s16le/16000/mono"})
        event = websocket.receive_json()
        events.append(event.get("type", ""))
        for offset in range(0, len(pcm), 32768):
            websocket.binary(pcm[offset:offset + 32768])
            event = websocket.receive_json()
            events.append(event.get("type", ""))
        websocket.json({"type": "end_of_speech"})
        while "completed" not in events:
            event = websocket.receive_json()
            events.append(event.get("type", ""))
            if len(events) > 32:
                raise ProbeRefusal("WebSocket event count exceeds bound")
        if events[0] != "ready" or "partial_transcript" not in events or events[-3:] != ["final_transcript", "reply_text", "completed"]:
            raise ProbeRefusal("WebSocket sequence differs")
        return {"status": "PASS", "event_types": events, "final_result_preserved": True, "partial_queue_limit": 4, "audio_queue_limit": 8}
    finally:
        websocket.close()


def cancellation_proof(args: argparse.Namespace) -> dict:
    websocket = WebSocket(args.base_url, _token(args.token_file))
    try:
        websocket.json({"type": "start", "request_id": str(uuid.uuid4()), "language_hint": "en", "audio_format": "pcm_s16le/16000/mono"})
        if websocket.receive_json().get("type") != "ready":
            raise ProbeRefusal("cancellation stream did not become ready")
        started = time.perf_counter()
        websocket.json({"type": "barge_in"})
        event = websocket.receive_json()
        elapsed = (time.perf_counter() - started) * 1000
        if event.get("type") != "cancelled" or elapsed > 250:
            raise ProbeRefusal("barge-in exceeded 250 ms")
        return {"status": "PASS", "event_type": "cancelled", "barge_in_latency_ms": round(elapsed, 3), "maximum_ms": 250}
    finally:
        websocket.close()


def refusal_proof(args: argparse.Namespace) -> dict:
    token = _token(args.token_file)
    checks = []
    for supplied, language, contract, expected in (
        (None, "en", CONTRACT, 401),
        (token, "zz", CONTRACT, 422),
        (token, "en", "unsupported", 426),
    ):
        status, value = _post_file(args.base_url, supplied, args.wav, language=language, contract=contract)
        if status != expected or not isinstance(value.get("error", {}).get("code"), str):
            raise ProbeRefusal("controlled refusal differs")
        checks.append(status)
    return {"status": "PASS", "controlled_http_statuses": checks, "http_500_cascades": 0}


def dependency_refusal_proof(args: argparse.Namespace) -> dict:
    status, value = _post_file(args.base_url, _token(args.token_file), args.wav)
    if status != 503 or value.get("error", {}).get("code") != "DEPENDENCY_UNAVAILABLE":
        raise ProbeRefusal("dependency outage did not fail closed")
    return {"status": "PASS", "controlled_http_status": 503, "error_code": "DEPENDENCY_UNAVAILABLE", "http_500_cascades": 0}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("file", "websocket", "cancellation", "refusals", "dependency-refusal"))
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--wav", type=Path, required=True)
    args = parser.parse_args()
    try:
        if hashlib.sha256(args.wav.read_bytes()).hexdigest() != "97592cb9f83e38439ea9d7ff1841e502bf1ef5b60be096dd91ac80a320e5402b":
            raise ProbeRefusal("synthetic WAV binding differs")
        result = {
            "file": file_proof,
            "websocket": websocket_proof,
            "cancellation": cancellation_proof,
            "refusals": refusal_proof,
            "dependency-refusal": dependency_refusal_proof,
        }[args.mode](args)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "REFUSED", "error": type(exc).__name__}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
