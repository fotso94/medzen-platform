#!/usr/bin/env python3
"""Fail closed unless a B6 service image meets the local runtime boundary."""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.client
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import time
import uuid


MODULES = {
    "rag-index": "medzen_rag_index.app",
    "llm-gateway": "medzen_llm_gateway.app",
    "speech-orchestrator": "medzen_speech_orchestrator.streaming_app",
    "speech-tts-gateway": "medzen_speech_tts_gateway.app",
}

ROOT = Path(__file__).resolve().parents[1]
WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
ORCHESTRATOR_WEBSOCKET_PATH = "/v1/conversations/stream"
ORCHESTRATOR_SYNTHETIC_KEY = "medzen-b6-synthetic-client-key"


def run(*command: str) -> str:
    return subprocess.run(
        command, check=True, text=True, capture_output=True
    ).stdout


def _exact_requirement(path: Path, package: str) -> str:
    prefix = f"{package}=="
    matches = [
        line[len(prefix):].strip()
        for line in path.read_text().splitlines()
        if line.strip().startswith(prefix)
    ]
    if len(matches) != 1 or not matches[0]:
        raise SystemExit(f"{package} must have one exact runtime pin")
    return matches[0]


def _validate_websocket_upgrade(response: bytes, key: str) -> dict[str, object]:
    try:
        header_block = response.split(b"\r\n\r\n", 1)[0].decode("ascii")
    except UnicodeDecodeError as exc:
        raise RuntimeError("WebSocket upgrade response is not ASCII") from exc
    lines = header_block.split("\r\n")
    if not lines or lines[0] != "HTTP/1.1 101 Switching Protocols":
        status = lines[0] if lines else "absent"
        raise RuntimeError(f"WebSocket upgrade refused: {status}")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" not in line:
            raise RuntimeError("WebSocket upgrade response has malformed headers")
        name, value = line.split(":", 1)
        headers[name.strip().lower()] = value.strip()
    expected_accept = base64.b64encode(
        hashlib.sha1(f"{key}{WEBSOCKET_GUID}".encode("ascii")).digest()
    ).decode("ascii")
    if headers.get("upgrade", "").lower() != "websocket":
        raise RuntimeError("WebSocket upgrade header is absent")
    if "upgrade" not in {
        item.strip().lower()
        for item in headers.get("connection", "").split(",")
    }:
        raise RuntimeError("WebSocket connection upgrade token is absent")
    if headers.get("sec-websocket-accept") != expected_accept:
        raise RuntimeError("WebSocket accept proof is invalid")
    return {
        "http_status": 101,
        "path": ORCHESTRATOR_WEBSOCKET_PATH,
        "protocol": "RFC6455",
        "transport": "real_tcp",
    }


def _masked_close_frame() -> bytes:
    payload = struct.pack("!H", 1000)
    mask = os.urandom(4)
    masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
    return b"\x88" + bytes([0x80 | len(payload)]) + mask + masked


def _real_websocket_handshake(host: str, port: int) -> dict[str, object]:
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = "\r\n".join((
        f"GET {ORCHESTRATOR_WEBSOCKET_PATH} HTTP/1.1",
        f"Host: {host}:{port}",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Key: {key}",
        "Sec-WebSocket-Version: 13",
        f"Authorization: Bearer {ORCHESTRATOR_SYNTHETIC_KEY}",
        "X-MedZen-Contract-Version: medzen.speech.v1",
        "",
        "",
    )).encode("ascii")
    with socket.create_connection((host, port), timeout=10.0) as connection:
        connection.sendall(request)
        response = b""
        while b"\r\n\r\n" not in response and len(response) < 16_384:
            chunk = connection.recv(4_096)
            if not chunk:
                break
            response += chunk
        result = _validate_websocket_upgrade(response, key)
        connection.sendall(_masked_close_frame())
    return result


def _wait_for_orchestrator_ready(container: str, port: int) -> None:
    deadline = time.monotonic() + 60.0
    last_error = "not attempted"
    while time.monotonic() < deadline:
        try:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2.0)
            connection.request("GET", "/readyz")
            response = connection.getresponse()
            payload = json.loads(response.read())
            connection.close()
            if response.status == 200 and payload.get("ready") is True:
                return
            last_error = f"HTTP {response.status}"
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            last_error = type(exc).__name__
        state = run(
            "docker", "inspect", "--format={{.State.Running}}", container
        ).strip()
        if state != "true":
            raise RuntimeError("orchestrator smoke container stopped before readiness")
        time.sleep(0.5)
    raise RuntimeError(f"orchestrator readiness timed out: {last_error}")


def _orchestrator_websocket_smoke(image: str) -> dict[str, object]:
    container = f"medzen-orchestrator-ws-smoke-{uuid.uuid4().hex[:12]}"
    mounts = (
        (ROOT / "platform/testdata", "/opt/medzen/platform/testdata"),
        (ROOT / "registry/languages", "/opt/medzen/registry/languages"),
        (ROOT / "registry/llm-policies", "/opt/medzen/registry/llm-policies"),
    )
    command = [
        "docker", "run", "--detach", "--rm", "--name", container,
        "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m",
        "--publish", "127.0.0.1::8080",
        "--env", "MEDZEN_ORCHESTRATOR_MODE=local_fixture",
    ]
    for source, target in mounts:
        command.extend((
            "--mount", f"type=bind,src={source},dst={target},readonly"
        ))
    command.append(image)
    try:
        run(*command)
        binding = run("docker", "port", container, "8080/tcp").strip()
        if not binding.startswith("127.0.0.1:"):
            raise RuntimeError("orchestrator smoke port is not loopback-bound")
        port = int(binding.rsplit(":", 1)[1])
        _wait_for_orchestrator_ready(container, port)
        result = _real_websocket_handshake("127.0.0.1", port)
        result.update({
            "container_read_only": True,
            "network_binding": "loopback_ephemeral",
            "status": "PASS",
        })
        return result
    finally:
        subprocess.run(
            ("docker", "rm", "--force", container),
            check=False,
            text=True,
            capture_output=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("image")
    parser.add_argument("service", choices=sorted(MODULES))
    parser.add_argument("source_commit")
    args = parser.parse_args()

    inspected = json.loads(run("docker", "image", "inspect", args.image))[0]
    config = inspected["Config"]
    if config.get("User") != "10001:10001":
        raise SystemExit("runtime image does not use the fixed non-root identity")
    labels = config.get("Labels") or {}
    if labels.get("org.opencontainers.image.revision") != args.source_commit:
        raise SystemExit("runtime image source revision label mismatch")
    if inspected.get("Architecture") != "amd64" or inspected.get("Os") != "linux":
        raise SystemExit("runtime image is not the deployable linux/amd64 child")

    websocket_version = None
    if args.service == "speech-orchestrator":
        websocket_version = _exact_requirement(
            ROOT / "services/speech-orchestrator/requirements.txt", "websockets"
        )

    smoke = r'''
import ctypes
import importlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path

module = os.environ["MEDZEN_SMOKE_MODULE"]
service = os.environ["MEDZEN_SMOKE_SERVICE"]
expected_websocket_version = os.environ.get("MEDZEN_SMOKE_WEBSOCKETS_VERSION")
forbidden_paths = [
    "/lib/apk",
    "/sbin/apk",
    "/var/lib/dpkg",
    "/usr/bin/apt",
    "/usr/bin/apt-get",
    "/usr/bin/dpkg",
    "/usr/local/lib/python3.12/ensurepip",
]
present = [path for path in forbidden_paths if Path(path).exists()]
pip_executables = list(Path("/usr/local/bin").glob("pip*"))
if present or pip_executables:
    raise SystemExit("package manager or installer residue is present")
for name in ("pip", "setuptools", "wheel"):
    if importlib.util.find_spec(name) is not None:
        raise SystemExit(f"build-only Python package is importable: {name}")
ctypes.CDLL(None)
loaded = importlib.import_module(module)
if getattr(loaded, "app", None) is None:
    raise SystemExit("service app import smoke did not produce an app")
websocket_backend = None
if service == "speech-orchestrator":
    websocket_backend = importlib.metadata.version("websockets")
    if websocket_backend != expected_websocket_version:
        raise SystemExit("WebSocket backend version does not match the exact pin")
packages = sorted(
    path.name for path in Path("/opt/site-packages").glob("*.dist-info")
)
print(json.dumps({
    "module": module,
    "uid": os.getuid(),
    "gid": os.getgid(),
    "package_manager_records": 0,
    "language_installers": 0,
    "runtime_packages": packages,
    "websocket_backend": websocket_backend,
}, sort_keys=True))
'''
    command = [
        "docker", "run", "--rm", "--network=none", "--read-only",
        "--entrypoint=/usr/local/bin/python", "--env",
        f"MEDZEN_SMOKE_MODULE={MODULES[args.service]}", "--env",
        f"MEDZEN_SMOKE_SERVICE={args.service}",
    ]
    if websocket_version is not None:
        command.extend((
            "--env", f"MEDZEN_SMOKE_WEBSOCKETS_VERSION={websocket_version}"
        ))
    command.extend((args.image, "-c", smoke))
    output = run(*command)
    result = json.loads(output)
    if result["uid"] != 10001 or result["gid"] != 10001:
        raise SystemExit("runtime smoke did not execute as fixed non-root")
    websocket_handshake = None
    if args.service == "speech-orchestrator":
        websocket_handshake = _orchestrator_websocket_smoke(args.image)
    print(json.dumps({
        "image": args.image,
        "image_id": inspected["Id"],
        "service": args.service,
        "source_commit": args.source_commit,
        "runtime_smoke": result,
        "websocket_handshake": websocket_handshake,
        "status": "PASS",
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
