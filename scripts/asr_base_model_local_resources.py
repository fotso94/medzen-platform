#!/usr/bin/env python3
"""Fail-closed local execution-resource gate for the offline ASR pilot.

This gate runs before an attempt envelope exists.  Its inputs are deliberately
plain data so the same validation code can consume a live host snapshot or a
recorded snapshot during cold rehearsal.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import shutil
import subprocess
from pathlib import Path
from typing import Any

from scripts.asr_eval_digest_rescan import (
    DigestRescanRefusal,
    detect_scout_authentication,
)


GIB = 1024**3
REQUIRED_COMMANDS = ("aws", "docker", "git", "kubectl")


class LocalResourceRefusal(RuntimeError):
    def __init__(self, reason_code: str, detail: str):
        super().__init__(detail)
        self.reason_code = reason_code
        self.detail = detail


def _positive_integer(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise LocalResourceRefusal(
            "LOCAL_RESOURCE_POLICY_MALFORMED",
            f"local resource field is not one positive integer: {field}",
        )
    return value


def _physical_memory_bytes() -> int:
    if hasattr(os, "sysconf"):
        try:
            pages = int(os.sysconf("SC_PHYS_PAGES"))
            page_size = int(os.sysconf("SC_PAGE_SIZE"))
            if pages > 0 and page_size > 0:
                return pages * page_size
        except (OSError, ValueError):
            pass
    completed = subprocess.run(
        ["sysctl", "-n", "hw.memsize"],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    try:
        measured = int(completed.stdout.strip())
    except ValueError as exc:
        raise LocalResourceRefusal(
            "LOCAL_MEMORY_UNREADABLE",
            "physical memory could not be measured before the attempt envelope",
        ) from exc
    if completed.returncode != 0 or measured <= 0:
        raise LocalResourceRefusal(
            "LOCAL_MEMORY_UNREADABLE",
            "physical memory could not be measured before the attempt envelope",
        )
    return measured


def _limit(name: int) -> dict[str, int]:
    soft, hard = resource.getrlimit(name)
    infinity = resource.RLIM_INFINITY
    return {
        "soft": 2**63 - 1 if soft == infinity else int(soft),
        "hard": 2**63 - 1 if hard == infinity else int(hard),
    }


def collect_local_resource_snapshot(workdir: Path) -> dict[str, Any]:
    """Measure every enumerable host resource the local executor consumes."""
    parent = workdir.parent.resolve()
    if not parent.is_dir():
        raise LocalResourceRefusal(
            "LOCAL_WORKDIR_PARENT_ABSENT",
            "the external execution workdir parent does not exist",
        )
    usage = shutil.disk_usage(parent)
    commands = {name: shutil.which(name) for name in REQUIRED_COMMANDS}
    docker = subprocess.run(
        ["docker", "info", "--format", "{{json .ServerVersion}}"],
        capture_output=True,
        check=False,
        text=True,
        timeout=60,
    ) if commands["docker"] else None
    try:
        scout_authentication = detect_scout_authentication()
    except DigestRescanRefusal as exc:
        scout_authentication = {
            "status": "REFUSED",
            "reason_code": exc.reason_code,
            "credentials_present": False,
            "credentials_persisted": False,
            "credential_values_recorded": False,
        }
    return {
        "schema_version": 1,
        "disk": {
            "measured_path": str(parent),
            "total_bytes": usage.total,
            "available_bytes": usage.free,
        },
        "memory": {"physical_bytes": _physical_memory_bytes()},
        "cpu": {"logical_count": os.cpu_count() or 0},
        "process_limits": {
            "open_files": _limit(resource.RLIMIT_NOFILE),
            "processes": _limit(resource.RLIMIT_NPROC),
        },
        "commands": commands,
        "environment": {
            "home_present": bool(os.environ.get("HOME")),
            "workdir_parent_writable": os.access(parent, os.W_OK | os.X_OK),
            "scout_user_present": bool(os.environ.get("DOCKER_SCOUT_HUB_USER")),
            "scout_password_present": bool(os.environ.get("DOCKER_SCOUT_HUB_PASSWORD")),
            "scout_authentication": scout_authentication,
            "credential_values_recorded": False,
        },
        "docker": {
            "daemon_reachable": bool(docker and docker.returncode == 0),
            "server_version_present": bool(docker and docker.stdout.strip()),
        },
    }


def required_disk_bytes(policy: dict[str, Any]) -> dict[str, Any]:
    disk = policy.get("disk")
    if not isinstance(disk, dict):
        raise LocalResourceRefusal(
            "LOCAL_RESOURCE_POLICY_MALFORMED", "disk resource policy is absent"
        )
    archive = _positive_integer(disk.get("exact_archive_bytes"), field="exact_archive_bytes")
    scanner = _positive_integer(disk.get("scanner_scratch_reserve_bytes"), field="scanner_scratch_reserve_bytes")
    evidence = _positive_integer(disk.get("evidence_reserve_bytes"), field="evidence_reserve_bytes")
    margin = _positive_integer(disk.get("safety_margin_bytes"), field="safety_margin_bytes")
    floor = _positive_integer(disk.get("operating_floor_bytes"), field="operating_floor_bytes")
    calculated = archive + scanner + evidence + margin
    return {
        "exact_archive_bytes": archive,
        "scanner_scratch_reserve_bytes": scanner,
        "evidence_reserve_bytes": evidence,
        "safety_margin_bytes": margin,
        "calculated_peak_requirement_bytes": calculated,
        "operating_floor_bytes": floor,
        "required_available_bytes": max(calculated, floor),
        "simultaneous_full_image_representations": 1,
    }


def validate_local_resource_snapshot(
    policy: dict[str, Any], snapshot: dict[str, Any]
) -> dict[str, Any]:
    """Apply one deterministic fail-closed policy to a measured snapshot."""
    if policy.get("schema_version") != 1 or snapshot.get("schema_version") != 1:
        raise LocalResourceRefusal(
            "LOCAL_RESOURCE_POLICY_MALFORMED",
            "local resource policy or snapshot schema differs",
        )
    disk_requirement = required_disk_bytes(policy)
    try:
        available = _positive_integer(
            snapshot["disk"]["available_bytes"], field="disk.available_bytes"
        )
        physical = _positive_integer(
            snapshot["memory"]["physical_bytes"], field="memory.physical_bytes"
        )
        cpus = _positive_integer(snapshot["cpu"]["logical_count"], field="cpu.logical_count")
        nofile = _positive_integer(
            snapshot["process_limits"]["open_files"]["soft"],
            field="process_limits.open_files.soft",
        )
        nproc = _positive_integer(
            snapshot["process_limits"]["processes"]["soft"],
            field="process_limits.processes.soft",
        )
    except (KeyError, TypeError) as exc:
        raise LocalResourceRefusal(
            "LOCAL_RESOURCE_SNAPSHOT_MALFORMED",
            "local resource snapshot is incomplete",
        ) from exc
    required = disk_requirement["required_available_bytes"]
    if available < required:
        raise LocalResourceRefusal(
            "LOCAL_DISK_CAPACITY_INSUFFICIENT",
            f"local free bytes {available} are below the pre-envelope requirement {required}",
        )
    minimum_memory = _positive_integer(policy.get("minimum_memory_bytes"), field="minimum_memory_bytes")
    minimum_cpus = _positive_integer(policy.get("minimum_logical_cpus"), field="minimum_logical_cpus")
    minimum_nofile = _positive_integer(policy.get("minimum_open_files_soft"), field="minimum_open_files_soft")
    minimum_nproc = _positive_integer(policy.get("minimum_processes_soft"), field="minimum_processes_soft")
    if physical < minimum_memory:
        raise LocalResourceRefusal("LOCAL_MEMORY_INSUFFICIENT", "physical memory is below the pre-envelope requirement")
    if cpus < minimum_cpus:
        raise LocalResourceRefusal("LOCAL_CPU_CAPACITY_INSUFFICIENT", "logical CPU count is below the pre-envelope requirement")
    if nofile < minimum_nofile:
        raise LocalResourceRefusal("LOCAL_OPEN_FILE_LIMIT_INSUFFICIENT", "open-file limit is below the pre-envelope requirement")
    if nproc < minimum_nproc:
        raise LocalResourceRefusal("LOCAL_PROCESS_LIMIT_INSUFFICIENT", "process limit is below the pre-envelope requirement")
    commands = snapshot.get("commands")
    if not isinstance(commands, dict) or set(commands) != set(REQUIRED_COMMANDS) or any(
        not isinstance(commands[name], str) or not commands[name] for name in REQUIRED_COMMANDS
    ):
        raise LocalResourceRefusal("LOCAL_EXECUTABLE_PREREQUISITE_ABSENT", "one or more required local executables are absent")
    environment = snapshot.get("environment")
    if not isinstance(environment, dict) or any(
        environment.get(field) is not True
        for field in ("home_present", "workdir_parent_writable")
    ) or environment.get("credential_values_recorded") is not False:
        raise LocalResourceRefusal("LOCAL_EXECUTION_ENVIRONMENT_INCOMPLETE", "local execution environment is incomplete")
    environment_pair = (
        environment.get("scout_user_present") is True
        and environment.get("scout_password_present") is True
    )
    scout_authentication = environment.get("scout_authentication")
    credential_store = (
        isinstance(scout_authentication, dict)
        and scout_authentication.get("status")
        == "PASS_SCOUT_AUTHENTICATION_HANDOFF"
        and scout_authentication.get("mode") == "DOCKER_CREDENTIAL_STORE"
        and scout_authentication.get("credentials_present") is True
        and scout_authentication.get("credentials_persisted") is False
        and scout_authentication.get("credential_values_recorded") is False
    )
    if not environment_pair and not credential_store:
        raise LocalResourceRefusal(
            "LOCAL_EXECUTION_ENVIRONMENT_INCOMPLETE",
            "Docker Scout authentication handoff is absent",
        )
    docker = snapshot.get("docker")
    if not isinstance(docker, dict) or docker.get("daemon_reachable") is not True or docker.get("server_version_present") is not True:
        raise LocalResourceRefusal("LOCAL_DOCKER_DAEMON_UNAVAILABLE", "Docker daemon is unavailable before the attempt envelope")
    return {
        "status": "PASS_PRE_ENVELOPE_LOCAL_RESOURCES",
        "disk": {
            **disk_requirement,
            "measured_available_bytes": available,
            "headroom_after_requirement_bytes": available - required,
        },
        "memory": {"measured_bytes": physical, "minimum_bytes": minimum_memory},
        "cpu": {"measured_logical_count": cpus, "minimum_logical_count": minimum_cpus},
        "process_limits": {
            "open_files_soft": nofile,
            "minimum_open_files_soft": minimum_nofile,
            "processes_soft": nproc,
            "minimum_processes_soft": minimum_nproc,
        },
        "required_commands": list(REQUIRED_COMMANDS),
        "scout_authentication_mode": (
            "ENVIRONMENT_PAIR"
            if environment_pair
            else "DOCKER_CREDENTIAL_STORE"
        ),
        "docker_daemon_reachable": True,
        "attempt_envelope_created": False,
        "attempt_number_consumed": False,
    }


def qualify(policy: dict[str, Any], workdir: Path) -> dict[str, Any]:
    snapshot = collect_local_resource_snapshot(workdir)
    return {
        "schema_version": 1,
        "status": "PASS_LOCAL_RESOURCE_QUALIFICATION",
        "snapshot": snapshot,
        "validation": validate_local_resource_snapshot(policy, snapshot),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    args = parser.parse_args()
    value = json.loads(args.policy.read_bytes())
    result = qualify(value, args.workdir)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
