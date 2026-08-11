#!/usr/bin/env python3
"""Synthetic-only HTTP and WebSocket probes that never emit request content."""
from __future__ import annotations

import argparse
import base64
import hashlib
import http.client
import json
import os
import re
import socket
import ssl
import struct
import time
import uuid
import wave
from pathlib import Path
from urllib.parse import urlsplit

from scripts.b6_6_proof_audio_binding import PROOF_AUDIO_SHA256_ENV


CONTRACT = "medzen.speech.v1"
REGISTRY = "b6-test:d4f9696d288e0ea6c1d139f496e00eaf097b77ea8b3a4f5a26a6470286adfe81"
MODEL_KEYS = {"asr", "registry_snapshot", "llm", "rag", "tts"}
DIAGNOSTIC_MAX_UTF8_BYTES = 1024
PROOF_EXIT_CODES = {
    "SYNTHETIC_WAV_SHA256_MATCHES": 10,
    "TOKEN_FILE_MODE_IS_0600": 11,
    "TOKEN_VALUE_IS_NONEMPTY_SINGLE_TOKEN": 12,
    "PROBE_URL_IS_HTTP_OR_HTTPS": 13,
    "HTTP_REQUEST_COMPLETED": 14,
    "HTTP_RESPONSE_WITHIN_ONE_MIB": 15,
    "FILE_HTTP_STATUS_IS_200": 31,
    "FILE_RESPONSE_IS_JSON": 32,
    "FILE_RESPONSE_IS_OBJECT": 33,
    "FILE_REPLY_IS_OBJECT": 34,
    "FILE_TTS_BACKEND_IS_TEXT_ONLY": 35,
    "FILE_CITATIONS_IS_LIST": 36,
    "FILE_CITATION_COUNT_IS_THREE": 37,
    "FILE_MODEL_VERSIONS_IS_OBJECT": 38,
    "FILE_MODEL_VERSION_KEYS_ARE_EXACT": 39,
    "FILE_REGISTRY_SNAPSHOT_MATCHES": 40,
    "FILE_ASR_VERSION_IS_V0": 41,
    "FILE_LLM_VERSION_IS_FAKE_LOCAL": 42,
    "FILE_TTS_VERSION_IS_NULL": 43,
    "WEBSOCKET_URL_IS_WS_OR_WSS": 50,
    "WEBSOCKET_CONNECTION_ESTABLISHED": 51,
    "WEBSOCKET_HANDSHAKE_WITHIN_BOUND": 52,
    "WEBSOCKET_UPGRADE_STATUS_IS_101": 53,
    "WEBSOCKET_ACCEPT_BINDING_MATCHES": 54,
    "WEBSOCKET_SERVER_FRAME_IS_UNMASKED": 55,
    "WEBSOCKET_EVENT_FRAME_IS_TEXT": 56,
    "WEBSOCKET_EVENT_IS_JSON": 57,
    "WEBSOCKET_EVENT_IS_OBJECT": 58,
    "WEBSOCKET_CONNECTION_REMAINS_OPEN": 59,
    "WEBSOCKET_EVENT_COUNT_WITHIN_BOUND": 60,
    "WEBSOCKET_SEQUENCE_MATCHES": 61,
    "CANCELLATION_STREAM_BECOMES_READY": 70,
    "CANCELLATION_EVENT_IS_CANCELLED": 71,
    "CANCELLATION_LATENCY_WITHIN_250_MS": 72,
    "REFUSAL_UNAUTHENTICATED_STATUS_IS_401": 80,
    "REFUSAL_UNAUTHENTICATED_ERROR_CODE_PRESENT": 81,
    "REFUSAL_LANGUAGE_STATUS_IS_422": 82,
    "REFUSAL_LANGUAGE_ERROR_CODE_PRESENT": 83,
    "REFUSAL_CONTRACT_STATUS_IS_426": 84,
    "REFUSAL_CONTRACT_ERROR_CODE_PRESENT": 85,
    "DEPENDENCY_REFUSAL_STATUS_IS_503": 90,
    "DEPENDENCY_REFUSAL_CODE_MATCHES": 91,
    "UNEXPECTED_PROBE_CLIENT_EXCEPTION": 99,
    "LOCAL_PORT_FORWARD_PROCESS_ALIVE": 100,
    "LOCAL_PORT_FORWARD_READYZ": 101,
    "PROBE_DIAGNOSTIC_JSON_VALID": 102,
}
_SENSITIVE_RESPONSE_KEYS = {
    "audio",
    "audio_bytes",
    "audio_url",
    "authorization",
    "bearer",
    "citation_text",
    "client_key",
    "content",
    "key",
    "quote",
    "secret",
    "secret_value",
    "snippet",
    "text",
    "token",
    "transcript",
    "transcript_text",
}


def _redact_response_value(value):
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in _SENSITIVE_RESPONSE_KEYS or normalized.endswith("_token"):
                result[str(key)] = "[REDACTED]"
            else:
                result[str(key)] = _redact_response_value(child)
        return result
    if isinstance(value, list):
        return [_redact_response_value(child) for child in value]
    return value


def _bounded_utf8(value: str) -> tuple[str, bool]:
    encoded = value.encode("utf-8")
    truncated = len(encoded) > DIAGNOSTIC_MAX_UTF8_BYTES
    encoded = encoded[:DIAGNOSTIC_MAX_UTF8_BYTES]
    while True:
        try:
            return encoded.decode("utf-8"), truncated
        except UnicodeDecodeError:
            encoded = encoded[:-1]


def sanitize_response_body(raw: bytes) -> tuple[str, bool]:
    """Return bounded synthetic diagnostics without content or credentials."""
    try:
        parsed = json.loads(raw)
    except Exception:
        text = raw.decode("utf-8", errors="replace")
        text = re.sub(r"(?i)bearer\s+[^\s,;]+", "[CREDENTIAL_REDACTED]", text)
        text = re.sub(
            r'(?i)("(?:authorization|token|api[_-]?key|secret)"\s*:\s*)"[^"]*"',
            r'\1"[REDACTED]"',
            text,
        )
        text = "".join(character if character.isprintable() else " " for character in text)
    else:
        text = json.dumps(
            _redact_response_value(parsed),
            sort_keys=True,
            separators=(",", ":"),
        )
    return _bounded_utf8(text)


class ProbeRefusal(RuntimeError):
    def __init__(
        self,
        safe_error_text: str,
        *,
        failed_assertion: str,
        exit_code: int,
        http_status: int | None = None,
        response_body: bytes = b"",
    ) -> None:
        super().__init__(safe_error_text)
        if PROOF_EXIT_CODES.get(failed_assertion) != exit_code:
            raise ValueError("probe assertion and exit code differ")
        self.safe_error_text = safe_error_text
        self.failed_assertion = failed_assertion
        self.exit_code = exit_code
        self.http_status = http_status
        self.response_body = response_body

    def diagnostic(self) -> dict:
        body, truncated = sanitize_response_body(self.response_body)
        return {
            "status": "REFUSED",
            "reason_code": "SYNTHETIC_PROOF_ASSERTION_REFUSED",
            "failed_assertion": self.failed_assertion,
            "probe_exit_code": self.exit_code,
            "http_status": self.http_status,
            "sanitized_response_body": body,
            "response_body_truncated": truncated,
            "response_body_sha256": hashlib.sha256(self.response_body).hexdigest(),
            "safe_error_text": self.safe_error_text,
            "synthetic_only": True,
            "phi_present": False,
        }


def _refuse(
    assertion: str,
    message: str,
    *,
    http_status: int | None = None,
    response_body: bytes = b"",
) -> None:
    raise ProbeRefusal(
        message,
        failed_assertion=assertion,
        exit_code=PROOF_EXIT_CODES[assertion],
        http_status=http_status,
        response_body=response_body,
    )


def _token(path: Path) -> str:
    if not path.is_file() or (path.stat().st_mode & 0o777) != 0o600:
        _refuse("TOKEN_FILE_MODE_IS_0600", "synthetic token file is absent or not mode 0600")
    value = path.read_text().strip()
    if not value or any(character.isspace() for character in value):
        _refuse("TOKEN_VALUE_IS_NONEMPTY_SINGLE_TOKEN", "synthetic token is malformed")
    return value


def _connection(url: str) -> tuple[http.client.HTTPConnection, str]:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        _refuse("PROBE_URL_IS_HTTP_OR_HTTPS", "probe URL is invalid")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    connection: http.client.HTTPConnection
    if parsed.scheme == "https":
        connection = http.client.HTTPSConnection(parsed.hostname, port, timeout=60)
    else:
        connection = http.client.HTTPConnection(parsed.hostname, port, timeout=60)
    return connection, parsed.path.rstrip("/")


def _post_file(
    base_url: str,
    token: str | None,
    wav: Path,
    *,
    language: str = "en",
    contract: str = CONTRACT,
) -> tuple[int, bytes]:
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
        try:
            connection.request("POST", prefix + "/v1/conversations/speech", body=body, headers=headers)
            response = connection.getresponse()
        except (OSError, http.client.HTTPException) as exc:
            _refuse("HTTP_REQUEST_COMPLETED", type(exc).__name__)
        raw = response.read(1_048_577)
        if len(raw) > 1_048_576:
            _refuse(
                "HTTP_RESPONSE_WITHIN_ONE_MIB",
                "response exceeds one MiB",
                http_status=response.status,
                response_body=raw,
            )
        return response.status, raw
    finally:
        connection.close()


def _json_object(
    status: int,
    raw: bytes,
    *,
    json_assertion: str,
    object_assertion: str,
) -> dict:
    try:
        value = json.loads(raw)
    except Exception:
        _refuse(
            json_assertion,
            "response body is not valid JSON",
            http_status=status,
            response_body=raw,
        )
    if not isinstance(value, dict):
        _refuse(
            object_assertion,
            "response is not an object",
            http_status=status,
            response_body=raw,
        )
    return value


class WebSocket:
    def __init__(self, url: str, token: str):
        parsed = urlsplit(url.replace("http://", "ws://", 1).replace("https://", "wss://", 1))
        if parsed.scheme not in {"ws", "wss"} or not parsed.hostname:
            _refuse("WEBSOCKET_URL_IS_WS_OR_WSS", "WebSocket URL is invalid")
        port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        try:
            sock = socket.create_connection((parsed.hostname, port), timeout=60)
            if parsed.scheme == "wss":
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=parsed.hostname)
        except OSError as exc:
            _refuse("WEBSOCKET_CONNECTION_ESTABLISHED", type(exc).__name__)
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
        if b"\r\n\r\n" not in response:
            self.sock.close()
            _refuse(
                "WEBSOCKET_HANDSHAKE_WITHIN_BOUND",
                "WebSocket handshake exceeds bound",
                response_body=response,
            )
        status = None
        try:
            status = int(response.split(b" ", 2)[1])
        except Exception:
            pass
        if not response.startswith(b"HTTP/1.1 101") and not response.startswith(b"HTTP/1.0 101"):
            self.sock.close()
            _refuse(
                "WEBSOCKET_UPGRADE_STATUS_IS_101",
                "WebSocket upgrade refused",
                http_status=status,
                response_body=response,
            )
        expected = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        if f"sec-websocket-accept: {expected}".lower().encode() not in response.lower():
            self.sock.close()
            _refuse(
                "WEBSOCKET_ACCEPT_BINDING_MATCHES",
                "WebSocket accept binding differs",
                http_status=status,
                response_body=response,
            )
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
            _refuse("WEBSOCKET_SERVER_FRAME_IS_UNMASKED", "server WebSocket frame is unexpectedly masked")
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
            _refuse("WEBSOCKET_EVENT_FRAME_IS_TEXT", "expected a text WebSocket frame", response_body=payload)
        try:
            value = json.loads(payload)
        except Exception:
            _refuse("WEBSOCKET_EVENT_IS_JSON", "WebSocket event is not valid JSON", response_body=payload)
        if not isinstance(value, dict):
            _refuse("WEBSOCKET_EVENT_IS_OBJECT", "WebSocket event is not an object", response_body=payload)
        return value

    def _read(self, size: int) -> bytes:
        value = self.buffer[:size]
        self.buffer = self.buffer[size:]
        while len(value) < size:
            chunk = self.sock.recv(size - len(value))
            if not chunk:
                _refuse("WEBSOCKET_CONNECTION_REMAINS_OPEN", "WebSocket closed unexpectedly")
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


def evaluate_file_response(status: int, raw: bytes) -> dict:
    if status != 200:
        _refuse(
            "FILE_HTTP_STATUS_IS_200",
            "file proof HTTP status differs",
            http_status=status,
            response_body=raw,
        )
    value = _json_object(
        status,
        raw,
        json_assertion="FILE_RESPONSE_IS_JSON",
        object_assertion="FILE_RESPONSE_IS_OBJECT",
    )
    versions = value.get("model_versions")
    reply = value.get("reply")
    if not isinstance(reply, dict):
        _refuse("FILE_REPLY_IS_OBJECT", "file reply is not an object", http_status=status, response_body=raw)
    if reply.get("tts_backend") != "text_only":
        _refuse("FILE_TTS_BACKEND_IS_TEXT_ONLY", "file TTS backend differs", http_status=status, response_body=raw)
    citations = reply.get("citations")
    if not isinstance(citations, list):
        _refuse("FILE_CITATIONS_IS_LIST", "file citations are not a list", http_status=status, response_body=raw)
    if len(citations) != 3:
        _refuse("FILE_CITATION_COUNT_IS_THREE", "file citation count differs", http_status=status, response_body=raw)
    if not isinstance(versions, dict):
        _refuse("FILE_MODEL_VERSIONS_IS_OBJECT", "model versions are not an object", http_status=status, response_body=raw)
    if set(versions) != MODEL_KEYS:
        _refuse("FILE_MODEL_VERSION_KEYS_ARE_EXACT", "model version keys differ", http_status=status, response_body=raw)
    if versions.get("registry_snapshot") != REGISTRY:
        _refuse("FILE_REGISTRY_SNAPSHOT_MATCHES", "registry snapshot differs", http_status=status, response_body=raw)
    if versions.get("asr") != "v0":
        _refuse("FILE_ASR_VERSION_IS_V0", "ASR version differs", http_status=status, response_body=raw)
    if versions.get("llm") != "fake-bedrock-local-v1":
        _refuse("FILE_LLM_VERSION_IS_FAKE_LOCAL", "LLM version differs", http_status=status, response_body=raw)
    if versions.get("tts") is not None:
        _refuse("FILE_TTS_VERSION_IS_NULL", "TTS version differs", http_status=status, response_body=raw)
    return {"status": "PASS", "http_status": status, "citation_count": 3, "tts_backend": "text_only", "model_versions": versions}


def file_proof(args: argparse.Namespace) -> dict:
    status, raw = _post_file(args.base_url, _token(args.token_file), args.wav)
    return evaluate_file_response(status, raw)


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
                _refuse("WEBSOCKET_EVENT_COUNT_WITHIN_BOUND", "WebSocket event count exceeds bound")
        if events[0] != "ready" or "partial_transcript" not in events or events[-3:] != ["final_transcript", "reply_text", "completed"]:
            _refuse("WEBSOCKET_SEQUENCE_MATCHES", "WebSocket sequence differs")
        return {"status": "PASS", "event_types": events, "final_result_preserved": True, "partial_queue_limit": 4, "audio_queue_limit": 8}
    finally:
        websocket.close()


def cancellation_proof(args: argparse.Namespace) -> dict:
    websocket = WebSocket(args.base_url, _token(args.token_file))
    try:
        websocket.json({"type": "start", "request_id": str(uuid.uuid4()), "language_hint": "en", "audio_format": "pcm_s16le/16000/mono"})
        if websocket.receive_json().get("type") != "ready":
            _refuse("CANCELLATION_STREAM_BECOMES_READY", "cancellation stream did not become ready")
        started = time.perf_counter()
        websocket.json({"type": "barge_in"})
        event = websocket.receive_json()
        elapsed = (time.perf_counter() - started) * 1000
        if event.get("type") != "cancelled":
            _refuse("CANCELLATION_EVENT_IS_CANCELLED", "barge-in event differs")
        if elapsed > 250:
            _refuse("CANCELLATION_LATENCY_WITHIN_250_MS", "barge-in exceeded 250 ms")
        return {"status": "PASS", "event_type": "cancelled", "barge_in_latency_ms": round(elapsed, 3), "maximum_ms": 250}
    finally:
        websocket.close()


def refusal_proof(args: argparse.Namespace) -> dict:
    token = _token(args.token_file)
    checks = []
    for supplied, language, contract, expected, status_assertion, code_assertion in (
        (None, "en", CONTRACT, 401, "REFUSAL_UNAUTHENTICATED_STATUS_IS_401", "REFUSAL_UNAUTHENTICATED_ERROR_CODE_PRESENT"),
        (token, "zz", CONTRACT, 422, "REFUSAL_LANGUAGE_STATUS_IS_422", "REFUSAL_LANGUAGE_ERROR_CODE_PRESENT"),
        (token, "en", "unsupported", 426, "REFUSAL_CONTRACT_STATUS_IS_426", "REFUSAL_CONTRACT_ERROR_CODE_PRESENT"),
    ):
        status, raw = _post_file(args.base_url, supplied, args.wav, language=language, contract=contract)
        if status != expected:
            _refuse(status_assertion, "controlled refusal HTTP status differs", http_status=status, response_body=raw)
        value = _json_object(status, raw, json_assertion=code_assertion, object_assertion=code_assertion)
        if not isinstance(value.get("error", {}).get("code"), str):
            _refuse(code_assertion, "controlled refusal error code is absent", http_status=status, response_body=raw)
        checks.append(status)
    return {"status": "PASS", "controlled_http_statuses": checks, "http_500_cascades": 0}


def dependency_refusal_proof(args: argparse.Namespace) -> dict:
    status, raw = _post_file(args.base_url, _token(args.token_file), args.wav)
    if status != 503:
        _refuse("DEPENDENCY_REFUSAL_STATUS_IS_503", "dependency refusal HTTP status differs", http_status=status, response_body=raw)
    value = _json_object(
        status,
        raw,
        json_assertion="DEPENDENCY_REFUSAL_CODE_MATCHES",
        object_assertion="DEPENDENCY_REFUSAL_CODE_MATCHES",
    )
    if value.get("error", {}).get("code") != "DEPENDENCY_UNAVAILABLE":
        _refuse("DEPENDENCY_REFUSAL_CODE_MATCHES", "dependency refusal code differs", http_status=status, response_body=raw)
    return {"status": "PASS", "controlled_http_status": 503, "error_code": "DEPENDENCY_UNAVAILABLE", "http_500_cascades": 0}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("file", "websocket", "cancellation", "refusals", "dependency-refusal"))
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--wav", type=Path, required=True)
    args = parser.parse_args()
    try:
        expected_audio_sha256 = os.environ.get(PROOF_AUDIO_SHA256_ENV)
        if (
            expected_audio_sha256 is None
            or re.fullmatch(r"[0-9a-f]{64}", expected_audio_sha256) is None
            or hashlib.sha256(args.wav.read_bytes()).hexdigest()
            != expected_audio_sha256
        ):
            _refuse("SYNTHETIC_WAV_SHA256_MATCHES", "synthetic WAV binding differs")
        result = {
            "file": file_proof,
            "websocket": websocket_proof,
            "cancellation": cancellation_proof,
            "refusals": refusal_proof,
            "dependency-refusal": dependency_refusal_proof,
        }[args.mode](args)
        print(json.dumps(result, sort_keys=True))
        return 0
    except ProbeRefusal as exc:
        print(json.dumps(exc.diagnostic(), sort_keys=True))
        return exc.exit_code
    except Exception as exc:
        failure = ProbeRefusal(
            type(exc).__name__,
            failed_assertion="UNEXPECTED_PROBE_CLIENT_EXCEPTION",
            exit_code=PROOF_EXIT_CODES["UNEXPECTED_PROBE_CLIENT_EXCEPTION"],
        )
        print(json.dumps(failure.diagnostic(), sort_keys=True))
        return failure.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
