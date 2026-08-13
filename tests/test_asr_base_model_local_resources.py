from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.asr_base_model_local_resources import (
    LocalResourceRefusal,
    required_disk_bytes,
    validate_local_resource_snapshot,
)


def policy() -> dict:
    return {
        "schema_version": 1,
        "disk": {
            "exact_archive_bytes": 7_296_860_160,
            "scanner_scratch_reserve_bytes": 2_147_483_648,
            "evidence_reserve_bytes": 536_870_912,
            "safety_margin_bytes": 2_147_483_648,
            "operating_floor_bytes": 42_949_672_960,
        },
        "minimum_memory_bytes": 17_179_869_184,
        "minimum_logical_cpus": 4,
        "minimum_open_files_soft": 1024,
        "minimum_processes_soft": 512,
    }


def snapshot(*, free_bytes: int = 42_949_672_960) -> dict:
    return {
        "schema_version": 1,
        "disk": {"measured_path": "/tmp", "total_bytes": free_bytes * 2, "available_bytes": free_bytes},
        "memory": {"physical_bytes": 51_539_607_552},
        "cpu": {"logical_count": 12},
        "process_limits": {
            "open_files": {"soft": 1_048_575, "hard": 1_048_575},
            "processes": {"soft": 8000, "hard": 8000},
        },
        "commands": {name: f"/bin/{name}" for name in ("aws", "docker", "git", "kubectl")},
        "environment": {
            "home_present": True,
            "workdir_parent_writable": True,
            "scout_user_present": True,
            "scout_password_present": True,
            "credential_values_recorded": False,
        },
        "docker": {"daemon_reachable": True, "server_version_present": True},
    }


def test_disk_requirement_is_actual_archive_plus_reserves_with_owner_floor() -> None:
    value = required_disk_bytes(policy())
    assert value["calculated_peak_requirement_bytes"] == 12_128_698_368
    assert value["required_available_bytes"] == 42_949_672_960
    assert value["simultaneous_full_image_representations"] == 1


def test_sufficient_resources_pass_before_attempt_consumption() -> None:
    result = validate_local_resource_snapshot(policy(), snapshot())
    assert result["status"] == "PASS_PRE_ENVELOPE_LOCAL_RESOURCES"
    assert result["attempt_envelope_created"] is False
    assert result["attempt_number_consumed"] is False


def test_one_byte_below_disk_floor_refuses_fail_closed() -> None:
    with pytest.raises(LocalResourceRefusal) as captured:
        validate_local_resource_snapshot(policy(), snapshot(free_bytes=42_949_672_959))
    assert captured.value.reason_code == "LOCAL_DISK_CAPACITY_INSUFFICIENT"


@pytest.mark.parametrize(
    ("path", "value", "reason"),
    [
        (("memory", "physical_bytes"), 1, "LOCAL_MEMORY_INSUFFICIENT"),
        (("cpu", "logical_count"), 1, "LOCAL_CPU_CAPACITY_INSUFFICIENT"),
        (("process_limits", "open_files", "soft"), 16, "LOCAL_OPEN_FILE_LIMIT_INSUFFICIENT"),
        (("process_limits", "processes", "soft"), 16, "LOCAL_PROCESS_LIMIT_INSUFFICIENT"),
    ],
)
def test_every_enumerated_local_capacity_refuses_when_insufficient(path, value, reason) -> None:
    measured = snapshot()
    target = measured
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(LocalResourceRefusal) as captured:
        validate_local_resource_snapshot(policy(), measured)
    assert captured.value.reason_code == reason
