from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from check_b6a_ecr_scanning_plan import validate_plan  # noqa: E402


def _plan():
    return {
        "resource_changes": [{
            "address": "aws_ecr_registry_scanning_configuration.b6a_runtime",
            "change": {
                "actions": ["create"],
                "after": {
                    "scan_type": "BASIC",
                    "rule": [{
                        "scan_frequency": "SCAN_ON_PUSH",
                        "repository_filter": [
                            {"filter": "medzen-model-loader", "filter_type": "WILDCARD"},
                            {"filter": "medzen-asr-runtime", "filter_type": "WILDCARD"},
                            {"filter": "medzen-nvidia-dra", "filter_type": "WILDCARD"},
                        ],
                    }],
                },
            },
        }],
        "output_changes": {},
    }


def test_exact_three_repository_scan_configuration_passes():
    result = validate_plan(_plan())
    assert result["status"] == "PASS_EXACT_B6A_PACKET_2026_005"
    assert result["destroy"] == 0


@pytest.mark.parametrize("filter_name", ["medzen-*", "medzen-tts-gateway"])
def test_wildcard_or_separately_owned_repository_refuses(filter_name):
    plan = _plan()
    plan["resource_changes"][0]["change"]["after"]["rule"][0][
        "repository_filter"
    ][0]["filter"] = filter_name
    with pytest.raises(ValueError, match="repository filters differ"):
        validate_plan(plan)


def test_unrelated_or_destructive_change_refuses():
    plan = _plan()
    plan["resource_changes"].append({
        "address": "aws_ecr_repository.unrelated",
        "change": {"actions": ["delete"], "after": None},
    })
    with pytest.raises(ValueError, match="resource changes differ"):
        validate_plan(plan)

    plan = copy.deepcopy(_plan())
    plan["resource_changes"][0]["change"]["actions"] = ["delete"]
    with pytest.raises(ValueError, match="resource changes differ"):
        validate_plan(plan)
