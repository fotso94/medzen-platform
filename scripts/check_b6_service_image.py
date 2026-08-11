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
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.b6_6_proof_audio_binding import (
    PROOF_AUDIO_PATH,
    PROOF_AUDIO_SHA256,
    PROOF_AUDIO_SHA256_ENV,
)
from scripts.generate_b6_websocket_qualification_fixtures import (
    ASR_BINDING_OUTPUT as WEBSOCKET_ASR_FIXTURE,
    REGISTRY_OUTPUT as WEBSOCKET_REGISTRY_FIXTURE,
    products as websocket_fixture_products,
)


MODULES = {
    "rag-index": "medzen_rag_index.app",
    "llm-gateway": "medzen_llm_gateway.app",
    "speech-orchestrator": "medzen_speech_orchestrator.streaming_app",
    "speech-tts-gateway": "medzen_speech_tts_gateway.app",
}

WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
ORCHESTRATOR_WEBSOCKET_PATH = "/v1/conversations/stream"
ORCHESTRATOR_SYNTHETIC_KEY = "medzen-b6-synthetic-client-key"
PARTIAL_SOURCE_PATH_ENV = "MEDZEN_STREAM_PARTIAL_FIXTURE"
PARTIAL_SOURCE_SHA256_ENV = "MEDZEN_STREAM_PARTIAL_FIXTURE_SHA256"
PARTIAL_SOURCE_CONTAINER_PATH = (
    "/opt/medzen/platform/testdata/orchestrator/b6-window-asr-fixture.json"
)
PARTIAL_SOURCE_SHA256 = (
    "f5e6c57c3d8a57d80980ee3741723b36ae810e03aea10d2057fa2c30776a90fc"
)


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


def _exact_streamed_conversation(
    host: str,
    port: int,
    *,
    runtime_app_sha256: str,
) -> dict[str, object]:
    """Run the exact window WebSocket proof against the real local server."""
    if any(
        not path.exists() or path.read_bytes() != expected
        for path, expected in websocket_fixture_products().items()
    ):
        raise RuntimeError("WebSocket qualification fixtures are stale")
    token_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix="medzen-b6-local-ws-token-", delete=False
        ) as stream:
            token_path = Path(stream.name)
            stream.write(ORCHESTRATOR_SYNTHETIC_KEY.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        token_path.chmod(0o600)
        environment = os.environ.copy()
        environment[PROOF_AUDIO_SHA256_ENV] = PROOF_AUDIO_SHA256
        completed = subprocess.run(
            (
                sys.executable,
                str(ROOT / "scripts/b6_6_probe.py"),
                "websocket",
                "--base-url",
                f"http://{host}:{port}",
                "--token-file",
                str(token_path),
                "--wav",
                str(PROOF_AUDIO_PATH),
            ),
            cwd=ROOT,
            env=environment,
            check=False,
            text=True,
            capture_output=True,
            timeout=180,
        )
        try:
            result = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            stderr = " ".join(completed.stderr.strip().split())[-1024:]
            raise RuntimeError(
                "exact WebSocket conversation returned malformed evidence: "
                f"exit={completed.returncode} stderr={stderr or 'absent'}"
            ) from exc
        if completed.returncode != 0:
            assertion = result.get("failed_assertion", "UNCLASSIFIED")
            close_code = result.get("websocket_close_code", "absent")
            safe_body = result.get("sanitized_response_body", "absent")
            raise RuntimeError(
                "exact WebSocket conversation refused: "
                f"assertion={assertion} close_code={close_code} "
                f"sanitized_body={safe_body}"
            )
        if (
            result.get("status") != "PASS"
            or result.get("final_result_preserved") is not True
            or result.get("partial_queue_limit") != 4
            or result.get("audio_queue_limit") != 8
            or result.get("event_types", [None])[0] != "ready"
            or result.get("event_types", [])[-3:]
            != ["final_transcript", "reply_text", "completed"]
        ):
            raise RuntimeError("exact WebSocket conversation result differs")
        probe_sha256 = hashlib.sha256(
            (ROOT / "scripts/b6_6_probe.py").read_bytes()
        ).hexdigest()
        pair = {
            "probe_sha256": probe_sha256,
            "runtime_app_sha256": runtime_app_sha256,
        }
        pair_sha256 = hashlib.sha256(
            json.dumps(pair, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return {
            **result,
            "audio_sha256": PROOF_AUDIO_SHA256,
            "exact_window_probe": "scripts/b6_6_probe.py websocket",
            "probe_app_binding": {**pair, "pair_sha256": pair_sha256},
            "fixture_bindings": {
                "proof_audio_sha256": PROOF_AUDIO_SHA256,
                "asr_fixture_sha256": hashlib.sha256(
                    WEBSOCKET_ASR_FIXTURE.read_bytes()
                ).hexdigest(),
                "registry_fixture_sha256": hashlib.sha256(
                    WEBSOCKET_REGISTRY_FIXTURE.read_bytes()
                ).hexdigest(),
            },
            "server": "containerized_orchestrator_with_fake_dependencies",
            "transport": "real_tcp_rfc6455",
        }
    finally:
        if token_path is not None:
            token_path.unlink(missing_ok=True)


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


def _wait_for_partial_source_refusal(container: str, port: int) -> dict[str, object]:
    deadline = time.monotonic() + 60.0
    last_error = "not attempted"
    while time.monotonic() < deadline:
        try:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2.0)
            connection.request("GET", "/readyz")
            response = connection.getresponse()
            payload = json.loads(response.read())
            connection.close()
            if (
                response.status == 503
                and payload.get("ready") is False
                and payload.get("streaming_partial_source_loaded") is False
                and payload.get("error_code")
                == "STREAMING_PARTIAL_SOURCE_UNAVAILABLE"
            ):
                return {
                    "status": "PASS_FAIL_CLOSED",
                    "http_status": 503,
                    "dependency": "streaming_partial_source",
                    "reason_code": payload["error_code"],
                    "application_started": True,
                }
            last_error = f"HTTP {response.status} {payload.get('error_code')}"
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            last_error = type(exc).__name__
        state = run(
            "docker", "inspect", "--format={{.State.Running}}", container
        ).strip()
        if state != "true":
            raise RuntimeError(
                "orchestrator negative qualification container stopped"
            )
        time.sleep(0.5)
    raise RuntimeError(
        f"partial-source refusal qualification timed out: {last_error}"
    )


def _orchestrator_websocket_smoke(
    image: str,
    *,
    runtime_app_sha256: str,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    container = f"medzen-orchestrator-ws-smoke-{uuid.uuid4().hex[:12]}"
    refused_container = f"medzen-orchestrator-ws-refusal-{uuid.uuid4().hex[:12]}"
    mounts = (
        (
            ROOT / "platform/testdata/registry-ssm",
            "/opt/medzen/platform/testdata/registry-ssm",
        ),
        (
            ROOT / "platform/testdata/orchestrator/client-keys.json",
            "/opt/medzen/platform/testdata/orchestrator/client-keys.json",
        ),
        (
            ROOT / "platform/testdata/rag-index",
            "/opt/medzen/platform/testdata/rag-index",
        ),
        (ROOT / "registry/languages", "/opt/medzen/registry/languages"),
        (ROOT / "registry/llm-policies", "/opt/medzen/registry/llm-policies"),
        (
            ROOT / "services/rag-index/medzen_rag_index",
            "/opt/medzen/services/rag-index/medzen_rag_index",
        ),
        (
            ROOT / "services/llm-gateway/medzen_llm_gateway",
            "/opt/medzen/services/llm-gateway/medzen_llm_gateway",
        ),
        (
            WEBSOCKET_ASR_FIXTURE,
            "/opt/medzen/platform/testdata/orchestrator/asr-fixture.json",
        ),
    )
    def command_for(name: str, *, missing_partial_source: bool) -> list[str]:
        command = [
            "docker", "run", "--detach", "--rm", "--name", name,
            "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m",
            "--publish", "127.0.0.1::8080",
            "--env", "MEDZEN_ORCHESTRATOR_MODE=local_fixture",
            "--env", (
                "MEDZEN_LOCAL_REGISTRY_FIXTURE="
                "/opt/medzen/platform/testdata/registry-ssm/"
                "b6-window-websocket-v1.json"
            ),
            "--env", (
                "PYTHONPATH=/opt/site-packages:"
                "/opt/medzen/services/speech-orchestrator:"
                "/opt/medzen/services/rag-index:"
                "/opt/medzen/services/llm-gateway"
            ),
        ]
        if missing_partial_source:
            command.extend((
                "--env",
                f"{PARTIAL_SOURCE_PATH_ENV}=/opt/medzen/not-ready/absent.json",
            ))
        for source, target in mounts:
            command.extend((
                "--mount", f"type=bind,src={source},dst={target},readonly"
            ))
        command.append(image)
        return command

    try:
        run(*command_for(refused_container, missing_partial_source=True))
        refused_binding = run(
            "docker", "port", refused_container, "8080/tcp"
        ).strip()
        if not refused_binding.startswith("127.0.0.1:"):
            raise RuntimeError("negative smoke port is not loopback-bound")
        dependency_gate = _wait_for_partial_source_refusal(
            refused_container, int(refused_binding.rsplit(":", 1)[1])
        )
        subprocess.run(
            ("docker", "rm", "--force", refused_container),
            check=False,
            text=True,
            capture_output=True,
        )

        run(*command_for(container, missing_partial_source=False))
        binding = run("docker", "port", container, "8080/tcp").strip()
        if not binding.startswith("127.0.0.1:"):
            raise RuntimeError("orchestrator smoke port is not loopback-bound")
        port = int(binding.rsplit(":", 1)[1])
        _wait_for_orchestrator_ready(container, port)
        handshake = _real_websocket_handshake("127.0.0.1", port)
        handshake.update({
            "container_read_only": True,
            "fixture_mounts": "read_only_synthetic_only",
            "network_binding": "loopback_ephemeral",
            "status": "PASS",
        })
        conversations = [
            _exact_streamed_conversation(
                "127.0.0.1",
                port,
                runtime_app_sha256=runtime_app_sha256,
            )
            for _ in range(3)
        ]
        if any(value != conversations[0] for value in conversations[1:]):
            raise RuntimeError("stable WebSocket conversation results differ")
        conversation = conversations[0]
        conversation.update({
            "container_read_only": True,
            "fixture_mounts": "read_only_synthetic_only",
            "network_binding": "loopback_ephemeral",
            "stable_conversation_passes": 3,
        })
        return handshake, conversation, dependency_gate
    finally:
        for name in (container, refused_container):
            subprocess.run(
                ("docker", "rm", "--force", name),
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
import hashlib
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
app_source_sha256 = hashlib.sha256(Path(loaded.__file__).read_bytes()).hexdigest()
websocket_backend = None
streaming_partial_source_sha256 = None
if service == "speech-orchestrator":
    websocket_backend = importlib.metadata.version("websockets")
    if websocket_backend != expected_websocket_version:
        raise SystemExit("WebSocket backend version does not match the exact pin")
    partial_path = Path(os.environ.get("MEDZEN_STREAM_PARTIAL_FIXTURE", ""))
    expected_partial_sha256 = os.environ.get(
        "MEDZEN_STREAM_PARTIAL_FIXTURE_SHA256", ""
    )
    if not partial_path.is_file():
        raise SystemExit("packaged streaming partial source is absent")
    streaming_partial_source_sha256 = hashlib.sha256(
        partial_path.read_bytes()
    ).hexdigest()
    if streaming_partial_source_sha256 != expected_partial_sha256:
        raise SystemExit("packaged streaming partial source hash differs")
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
    "streaming_partial_source_sha256": streaming_partial_source_sha256,
    "app_source_sha256": app_source_sha256,
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
    websocket_conversation = None
    websocket_dependency_gate = None
    if args.service == "speech-orchestrator":
        (
            websocket_handshake,
            websocket_conversation,
            websocket_dependency_gate,
        ) = (
            _orchestrator_websocket_smoke(
                args.image,
                runtime_app_sha256=result["app_source_sha256"],
            )
        )
    print(json.dumps({
        "image": args.image,
        "image_id": inspected["Id"],
        "service": args.service,
        "source_commit": args.source_commit,
        "runtime_smoke": result,
        "websocket_handshake": websocket_handshake,
        "websocket_conversation": websocket_conversation,
        "websocket_dependency_gate": websocket_dependency_gate,
        "status": "PASS",
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
