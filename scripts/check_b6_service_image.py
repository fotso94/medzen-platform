#!/usr/bin/env python3
"""Fail closed unless a B6 service image meets the local runtime boundary."""

from __future__ import annotations

import argparse
import json
import subprocess


MODULES = {
    "rag-index": "medzen_rag_index.app",
    "llm-gateway": "medzen_llm_gateway.app",
    "speech-orchestrator": "medzen_speech_orchestrator.streaming_app",
    "speech-tts-gateway": "medzen_speech_tts_gateway.app",
}


def run(*command: str) -> str:
    return subprocess.run(
        command, check=True, text=True, capture_output=True
    ).stdout


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

    smoke = r'''
import ctypes
import importlib
import importlib.util
import json
import os
from pathlib import Path

module = os.environ["MEDZEN_SMOKE_MODULE"]
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
}, sort_keys=True))
'''
    output = run(
        "docker",
        "run",
        "--rm",
        "--network=none",
        "--read-only",
        "--entrypoint=/usr/local/bin/python",
        "--env",
        f"MEDZEN_SMOKE_MODULE={MODULES[args.service]}",
        args.image,
        "-c",
        smoke,
    )
    result = json.loads(output)
    if result["uid"] != 10001 or result["gid"] != 10001:
        raise SystemExit("runtime smoke did not execute as fixed non-root")
    print(json.dumps({
        "image": args.image,
        "image_id": inspected["Id"],
        "service": args.service,
        "source_commit": args.source_commit,
        "runtime_smoke": result,
        "status": "PASS",
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
