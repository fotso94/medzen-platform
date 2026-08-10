from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module():
    spec = importlib.util.spec_from_file_location(
        "b6_lbc_tag_warning", ROOT / "scripts/b6_6_lbc_tag_warning.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


module = load_module()


def proof() -> dict:
    return {
        "internal_alb": True,
        "alb_security_group": "sg-0f0f6c66852830013",
        "listener_port": 80,
        "target_healthy": True,
        "orchestrator_readyz": True,
        "fargate_probe_exit_code": 0,
        "creation_time_exact_tags": True,
        "receipt_sha256": "a" * 64,
    }


def observation(kind: str, operation: str = "elasticloadbalancing:AddTags") -> dict:
    suffix = "0123456789abcdef/0123456789abcdef"
    if kind == "listener-rule":
        suffix += "/0123456789abcdef"
    return {
        "operation": operation,
        "error_code": "AccessDenied",
        "resource_arn": (
            "arn:aws:elasticloadbalancing:eu-central-1:558069890522:"
            f"{kind}/app/medzen-b6-window/{suffix}"
        ),
        "observed_utc": "2026-08-10T01:00:00Z",
        "timing": "POST_CREATE",
    }


def test_all_four_exact_tag_mutation_pairs_are_non_fatal_after_proof() -> None:
    observations = [
        observation("listener", "elasticloadbalancing:AddTags"),
        observation("listener", "elasticloadbalancing:RemoveTags"),
        observation("listener-rule", "elasticloadbalancing:AddTags"),
        observation("listener-rule", "elasticloadbalancing:RemoveTags"),
    ]
    result = module.classify(observations, proof())
    assert result["status"] == "WARNING_NON_FATAL"
    assert result["warning_count"] == 4


def test_no_denial_is_a_pass_not_a_warning() -> None:
    result = module.classify([], proof())
    assert result["status"] == "PASS_NO_TAG_MUTATION_DENIAL"


def test_create_or_cleanup_denials_remain_fatal() -> None:
    for operation in (
        "elasticloadbalancing:CreateListener",
        "elasticloadbalancing:CreateRule",
        "elasticloadbalancing:DeleteListener",
        "elasticloadbalancing:DeleteRule",
    ):
        try:
            module.classify([observation("listener", operation)], proof())
        except module.TagWarningRefusal:
            pass
        else:
            raise AssertionError(f"{operation} was accepted as a warning")


def test_wrong_account_name_resource_or_timing_remains_fatal() -> None:
    bad = [
        {**observation("listener"), "resource_arn": observation("listener")["resource_arn"].replace("558069890522", "111111111111")},
        {**observation("listener"), "resource_arn": observation("listener")["resource_arn"].replace("medzen-b6-window", "other")},
        {**observation("listener"), "resource_arn": observation("listener")["resource_arn"].replace("listener/", "loadbalancer/")},
        {**observation("listener"), "timing": "CREATE"},
    ]
    for item in bad:
        try:
            module.classify([item], proof())
        except module.TagWarningRefusal:
            pass
        else:
            raise AssertionError("out-of-bound tag denial was accepted")


def test_missing_functional_proof_remains_fatal() -> None:
    incomplete = proof()
    incomplete["target_healthy"] = False
    try:
        module.classify([observation("listener")], incomplete)
    except module.TagWarningRefusal:
        pass
    else:
        raise AssertionError("tag warning was accepted before ALB function was proven")
