#!/usr/bin/env python3
"""Reproduce attempt 16 and qualify the numeric fix in AL2023 userspace."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.asr_base_model_pilot_receipts import canonical_json, write_exclusive
from scripts.asr_base_model_node_staging import (
    NUMERIC_IDENTITY_PREFIX,
    STAGING_GID,
    STAGING_UID,
    audit_staging_commands,
    concatenate_files,
    extract_archive,
    install_directory,
    numeric_identity_command,
    root_command,
    staging_prelude,
    verify_sha256,
    verify_size,
    write_base64,
)
from scripts.asr_external_tool import sanitize_bytes


BASE_IMAGE_INDEX = (
    "public.ecr.aws/amazonlinux/amazonlinux@"
    "sha256:6d8e068b91f351df5bf6acd4bd261316e42747ad4bae76689ff6f4939e2180a2"
)
BASE_IMAGE_LINUX_AMD64 = (
    "public.ecr.aws/amazonlinux/amazonlinux@"
    "sha256:47821fb77b737fb67c93e451c0953e7d3325ee9d41f8d3ecc799fd9b96e6ca9c"
)


def _run(command: list[str], *, timeout: int = 900) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(command, capture_output=True, check=False, timeout=timeout)


def _inside_script() -> str:
    commands, _ = staging_prelude(17)
    base = "/var/lib/medzen-asr-eval/attempt-17"
    source = f"{base}/qualification/source.txt"
    copied = f"{base}/qualification/copied.txt"
    part_a = f"{base}/qualification/a.part"
    part_b = f"{base}/qualification/b.part"
    assembled = f"{base}/qualification/assembled.txt"
    archive = f"{base}/qualification/audio.tar"
    audio = f"{base}/input/audio/sample.txt"
    network = f"{base}/input/network-binding.json"
    payload = b"node-equivalent-numeric-staging\n"
    payload_sha = hashlib.sha256(payload).hexdigest()
    assembled_body = b"part-a\npart-b\n"
    assembled_sha = hashlib.sha256(assembled_body).hexdigest()
    commands.extend(
        [
            install_directory(f"{base}/qualification"),
            root_command(
                "/usr/bin/sudo", "/usr/bin/printf", "%s", payload.decode(),
            )
            + " > "
            + source,
            numeric_identity_command(
                root_command("/usr/bin/cp", source, copied)
            ),
            verify_sha256(copied, payload_sha),
            verify_size(copied, len(payload)),
            root_command("/usr/bin/sudo", "/usr/bin/printf", "part-a\\n")
            + " > "
            + part_a,
            root_command("/usr/bin/sudo", "/usr/bin/printf", "part-b\\n")
            + " > "
            + part_b,
            concatenate_files((part_a, part_b), assembled),
            verify_sha256(assembled, assembled_sha),
            numeric_identity_command(
                root_command(
                    "/usr/bin/tar",
                    "--create",
                    "--file",
                    archive,
                    "--directory",
                    f"{base}/qualification",
                    "source.txt",
                )
            ),
            extract_archive(archive, f"{base}/input/audio"),
            root_command("/usr/bin/sudo", "/usr/bin/mv", f"{base}/input/audio/source.txt", audio),
            verify_sha256(audio, payload_sha),
            write_base64("eyJzdGF0dXMiOiJQQVNTIn0K", network),
            numeric_identity_command(
                "/usr/bin/test \"$(/usr/bin/id -u)\" = 10001; "
                "/usr/bin/test \"$(/usr/bin/id -g)\" = 10001; "
                "/usr/bin/test \"${USER-unset}\" = unset; "
                "/usr/bin/env | /usr/bin/grep -Fx HOME=/tmp >/dev/null"
            ),
            f"/usr/bin/test \"$(/usr/bin/stat -c %u:%g {copied})\" = 10001:10001",
            f"/usr/bin/test \"$(/usr/bin/stat -c %u:%g {assembled})\" = 10001:10001",
            f"/usr/bin/test \"$(/usr/bin/stat -c %u:%g {network})\" = 10001:10001",
            "/usr/bin/printf PASS_NUMERIC_STAGING",
        ]
    )
    audit_staging_commands(commands)
    return "\n".join(commands)


def qualify(output: Path) -> dict[str, Any]:
    inspect = _run(
        [
            "docker",
            "image",
            "inspect",
            BASE_IMAGE_LINUX_AMD64,
            "--format",
            "{{.Architecture}} {{.Id}}",
        ],
        timeout=60,
    )
    if inspect.returncode != 0 or not inspect.stdout.startswith(b"amd64 "):
        raise RuntimeError("pinned Amazon Linux amd64 image is absent or differs")
    package_install = "dnf install -y -q sudo tar findutils util-linux-core >/dev/null"
    old = _run(
        [
            "docker", "run", "--rm", "--platform", "linux/amd64",
            BASE_IMAGE_LINUX_AMD64, "/bin/bash", "-ec",
            package_install
            + "; ! /usr/bin/getent passwd 10001; "
            + "set +e; /usr/bin/sudo -u '#10001' /usr/bin/id; rc=$?; "
            + "set -e; /usr/bin/test \"$rc\" = 1",
        ],
        timeout=900,
    )
    if old.returncode != 0 or "unknown user #10001" not in sanitize_bytes(old.stderr):
        raise RuntimeError("attempt-16 account-name failure did not reproduce")
    corrected = _run(
        [
            "docker", "run", "--rm", "--platform", "linux/amd64",
            BASE_IMAGE_LINUX_AMD64, "/bin/bash", "-ec",
            package_install + "; " + _inside_script(),
        ],
        timeout=900,
    )
    if corrected.returncode != 0 or corrected.stdout.strip() != b"PASS_NUMERIC_STAGING":
        raise RuntimeError(
            "corrected numeric staging refused: " + sanitize_bytes(corrected.stderr)
        )
    commands, _ = staging_prelude(17)
    audit = audit_staging_commands(
        commands + [numeric_identity_command("/usr/bin/printf pass")]
    )
    record = {
        "record": "ASR_BASE_MODEL_NODE_STAGING_LOCAL_QUALIFICATION",
        "id": "ASR-BASE-MODEL-NODE-STAGING-QUALIFICATION-2026-001",
        "schema_version": 1,
        "status": "PASS_FAILURE_AND_FIX_NODE_EQUIVALENT_QUALIFICATION",
        "execution_cost_usd": 0,
        "aws_calls": 0,
        "kubernetes_calls": 0,
        "target_node": {
            "ami": "ami-0a2cdcb403e09fd74",
            "name": "amazon-eks-node-al2023-x86_64-nvidia-1.36-v20260728",
            "architecture": "linux/amd64",
        },
        "local_equivalent": {
            "scope": "Amazon Linux 2023 x86_64 userspace and GNU sudo/coreutils identity semantics; not the EKS kernel, kubelet, driver or AMI filesystem",
            "oci_index": BASE_IMAGE_INDEX,
            "linux_amd64_child": BASE_IMAGE_LINUX_AMD64,
            "inspect": sanitize_bytes(inspect.stdout),
            "packages_installed_only_inside_destroyed_test_containers": [
                "sudo", "tar", "findutils", "util-linux-core"
            ],
            "containers_destroyed_after_each_case": True,
        },
        "observed_failure": {
            "status": "PASS_REPRODUCED_ATTEMPT_16_FAILURE",
            "container_assertion_returncode": old.returncode,
            "observed_inner_command_returncode": 1,
            "stderr_sanitized": sanitize_bytes(old.stderr),
            "passwd_entry_for_uid_10001_present": False,
        },
        "corrected_path": {
            "status": corrected.stdout.decode().strip(),
            "returncode": corrected.returncode,
            "uid": STAGING_UID,
            "gid": STAGING_GID,
            "numeric_wrapper_argv": list(NUMERIC_IDENTITY_PREFIX),
            "numeric_wrapper_argv_sha256": hashlib.sha256(
                b"\0".join(value.encode() for value in NUMERIC_IDENTITY_PREFIX)
            ).hexdigest(),
            "qualified_operations": [
                "numeric uid/gid without passwd entry",
                "empty controlled environment",
                "directory creation",
                "copy and hash/readback",
                "multipart concatenation",
                "archive extraction",
                "base64 receipt write",
                "numeric ownership",
            ],
            "stdout_sha256": hashlib.sha256(corrected.stdout).hexdigest(),
            "stderr_sha256": hashlib.sha256(corrected.stderr).hexdigest(),
        },
        "assumption_audit": audit,
        "limitations": [
            "The local container cannot prove live S3 reachability, EBS mount behavior, SSM transport or GPU workload execution.",
            "Those boundaries remain fail-closed and require first-live receipts in a separately approved attempt.",
        ],
    }
    write_exclusive(output, canonical_json(record))
    return record


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        value = qualify(args.output.resolve())
    except Exception as exc:
        print(json.dumps({"status": "REFUSED", "reason": sanitize_bytes(str(exc))}, sort_keys=True))
        return 2
    print(json.dumps(value, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
