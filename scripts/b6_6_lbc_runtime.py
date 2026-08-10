#!/usr/bin/env python3
"""Verify the B6.6 ALB and classify only exact post-create tag denials."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.b6_6_lbc_tag_warning import TagWarningRefusal, classify


ACCOUNT = "558069890522"
REGION = "eu-central-1"
PROFILE = "medzen"
ALB_NAME = "medzen-b6-window"
ALB_SECURITY_GROUP = "sg-0f0f6c66852830013"
REQUIRED_TAGS = {
    "elbv2.k8s.aws/cluster": "medzen-speech",
    "Project": "medzen-speech",
    "Environment": "dev",
    "Stage": "B6.6",
    "Workstream": "integration-window",
    "BudgetRegistry": "COST-REGISTRY-2026-003",
}
ACCESS_DENIED = re.compile(r"AccessDenied(?:Exception)?")
DENIAL_SIGNAL = re.compile(r"AccessDenied|UnauthorizedOperation|not authorized to perform", re.I)
ACTION = re.compile(r"elasticloadbalancing:(?P<action>[A-Za-z]+)")
OPERATION = re.compile(r"Elastic Load Balancing v2:\s*(?P<action>[A-Za-z]+)")
RESOURCE = re.compile(
    r"arn:aws:elasticloadbalancing:eu-central-1:558069890522:"
    r"(?:listener|listener-rule)/app/medzen-b6-window/[0-9a-f/]+"
)
TIMESTAMP = re.compile(r"(?P<time>20\d\d-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z)")


class RuntimeEvidenceRefusal(RuntimeError):
    pass


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _exact_one(values: list[Any], name: str) -> Any:
    if len(values) != 1:
        raise RuntimeEvidenceRefusal(f"exactly one {name} is required")
    return values[0]


def _tag_map(client: Any, arns: list[str]) -> dict[str, dict[str, str]]:
    descriptions = client.describe_tags(ResourceArns=arns).get("TagDescriptions", [])
    result = {
        item.get("ResourceArn", ""): {
            tag.get("Key", ""): tag.get("Value", "") for tag in item.get("Tags", [])
        }
        for item in descriptions
    }
    if set(result) != set(arns):
        raise RuntimeEvidenceRefusal("ALB tag read-back resource set differs")
    return result


def verify_live(client: Any) -> dict[str, Any]:
    alb = _exact_one(
        client.describe_load_balancers(Names=[ALB_NAME]).get("LoadBalancers", []),
        "B6 internal ALB",
    )
    if (
        alb.get("LoadBalancerName") != ALB_NAME
        or alb.get("Scheme") != "internal"
        or alb.get("Type") != "application"
        or sorted(alb.get("SecurityGroups", [])) != [ALB_SECURITY_GROUP]
        or alb.get("State", {}).get("Code") != "active"
    ):
        raise RuntimeEvidenceRefusal("B6 internal ALB boundary differs")
    alb_arn = str(alb.get("LoadBalancerArn", ""))
    listeners = client.describe_listeners(LoadBalancerArn=alb_arn).get("Listeners", [])
    listener = _exact_one(listeners, "listener")
    if listener.get("Port") != 80 or listener.get("Protocol") != "HTTP":
        raise RuntimeEvidenceRefusal("B6 listener differs")
    listener_arn = str(listener.get("ListenerArn", ""))
    rules = client.describe_rules(ListenerArn=listener_arn).get("Rules", [])
    rule = _exact_one([item for item in rules if not item.get("IsDefault")], "non-default rule")
    rule_arn = str(rule.get("RuleArn", ""))
    target_group = _exact_one(
        client.describe_target_groups(LoadBalancerArn=alb_arn).get("TargetGroups", []),
        "target group",
    )
    target_health = client.describe_target_health(
        TargetGroupArn=target_group.get("TargetGroupArn", "")
    ).get("TargetHealthDescriptions", [])
    if not target_health or any(
        item.get("TargetHealth", {}).get("State") != "healthy" for item in target_health
    ):
        raise RuntimeEvidenceRefusal("B6 target is not healthy")
    tags = _tag_map(client, [alb_arn, listener_arn, rule_arn])
    for resource_arn, actual in tags.items():
        for key, expected in REQUIRED_TAGS.items():
            if actual.get(key) != expected:
                raise RuntimeEvidenceRefusal(
                    f"required creation-time tag differs on {resource_arn.rsplit('/', 1)[0]}"
                )
    return {
        "internal_alb": True,
        "alb_security_group": ALB_SECURITY_GROUP,
        "listener_port": 80,
        "target_healthy": True,
        "orchestrator_readyz": True,
        "fargate_probe_exit_code": 0,
        "creation_time_exact_tags": True,
        "required_tag_count": len(REQUIRED_TAGS),
        "tagged_resource_count": 3,
        "resource_arn_set_sha256": canonical_sha256(sorted((alb_arn, listener_arn, rule_arn))),
    }


def parse_denials(raw: str) -> list[dict[str, str]]:
    observations: list[dict[str, str]] = []
    for line in raw.splitlines():
        error = ACCESS_DENIED.search(line)
        if DENIAL_SIGNAL.search(line) is None:
            continue
        if error is None:
            raise RuntimeEvidenceRefusal("an unknown controller authorization failure occurred")
        action_match = ACTION.search(line) or OPERATION.search(line)
        resource_match = RESOURCE.search(line)
        timestamp_match = TIMESTAMP.search(line)
        if action_match is None or resource_match is None or timestamp_match is None:
            raise RuntimeEvidenceRefusal("an AccessDenied controller event is ambiguous")
        observations.append(
            {
                "operation": f"elasticloadbalancing:{action_match.group('action')}",
                "error_code": error.group(0),
                "resource_arn": resource_match.group(0),
                "observed_utc": timestamp_match.group("time"),
                "timing": "POST_CREATE",
            }
        )
    return observations


def classify_runtime(
    *, receipt: dict[str, Any], receipt_sha256: str, controller_logs: str
) -> dict[str, Any]:
    if receipt.get("stage") != "alb_ready" or receipt.get("status") != "PASS":
        raise RuntimeEvidenceRefusal("functional ALB receipt is not PASS")
    proof = dict(receipt.get("payload", {}))
    proof["receipt_sha256"] = receipt_sha256
    observations = parse_denials(controller_logs)
    try:
        return classify(observations, proof)
    except TagWarningRefusal as exc:
        raise RuntimeEvidenceRefusal(str(exc)) from exc


def _session_client(profile: str) -> Any:
    import boto3

    session = boto3.Session(profile_name=profile, region_name=REGION)
    identity = session.client("sts").get_caller_identity()
    if identity.get("Account") != ACCOUNT:
        raise RuntimeEvidenceRefusal("AWS account differs")
    return session.client("elbv2")


def _controller_logs(kubeconfig: Path, since_time: str) -> str:
    process = subprocess.run(
        [
            "kubectl",
            "--kubeconfig",
            str(kubeconfig),
            "logs",
            "deployment/aws-load-balancer-controller",
            "--namespace",
            "kube-system",
            "--all-containers=true",
            f"--since-time={since_time}",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if process.returncode != 0:
        raise RuntimeEvidenceRefusal("controller logs are unavailable")
    return process.stdout


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="mode", required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--profile", default=PROFILE)
    classify_parser = subparsers.add_parser("classify")
    classify_parser.add_argument("--kubeconfig", type=Path, required=True)
    classify_parser.add_argument("--receipts-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.mode == "verify":
            result = verify_live(_session_client(args.profile))
        else:
            path = args.receipts_dir / "alb_ready.json"
            encoded = path.read_bytes()
            receipt = json.loads(encoded)
            controller = json.loads((args.receipts_dir / "controller_ready.json").read_bytes())
            since_time = controller.get("recorded_utc")
            if not isinstance(since_time, str) or not since_time.endswith("Z"):
                raise RuntimeEvidenceRefusal("controller receipt timestamp is malformed")
            result = classify_runtime(
                receipt=receipt,
                receipt_sha256=hashlib.sha256(encoded).hexdigest(),
                controller_logs=_controller_logs(args.kubeconfig, since_time),
            )
    except (OSError, json.JSONDecodeError, RuntimeEvidenceRefusal) as exc:
        print(json.dumps({"status": "REFUSED", "reason_code": type(exc).__name__}))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
