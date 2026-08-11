#!/usr/bin/env python3
"""Verify the B6.6 ALB and classify only exact post-create tag denials."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from botocore.exceptions import ClientError


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.b6_6_lbc_tag_warning import TagWarningRefusal, classify


ACCOUNT = "558069890522"
REGION = "eu-central-1"
PROFILE = "medzen"
ALB_NAME = "medzen-b6-window"
ALB_SECURITY_GROUP = "sg-0f0f6c66852830013"
TARGET_HEALTH_WAIT_SECONDS = 900
TARGET_HEALTH_POLL_SECONDS = 10
TARGET_HEALTH_STABLE_OBSERVATIONS = 3
RUNTIME_SHAPE_STABLE_OBSERVATIONS = 3
RUNTIME_SHAPE_POLL_SECONDS = 5
RUNTIME_SHAPE_WAIT_SECONDS = 120
TAG_CLASSIFICATION_STABLE_OBSERVATIONS = 3
TAG_CLASSIFICATION_POLL_SECONDS = 5
TAG_CLASSIFICATION_WAIT_SECONDS = 120
RETRYABLE_INITIAL_REASONS = {
    "Elb.InitialHealthChecking",
    "Elb.RegistrationInProgress",
}
REQUIRED_TAGS = {
    "elbv2.k8s.aws/cluster": "medzen-speech",
    "Project": "medzen-speech",
    "Environment": "dev",
    "Stage": "B6.6",
    "Workstream": "integration-window",
    "BudgetRegistry": "COST-REGISTRY-2026-003",
}
EXPECTED_ROUTES = {
    "1": ["/v1/conversations/speech", "/v1/conversations/speech/*"],
    "2": ["/v1/conversations/stream", "/v1/conversations/stream/*"],
    "3": ["/readyz", "/readyz/*"],
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


class RuntimeEvidencePending(RuntimeError):
    pass


class TargetReadinessRefusal(RuntimeEvidenceRefusal):
    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


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


def _target_identity(descriptions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = [
        {
            "id": item.get("Target", {}).get("Id"),
            "port": item.get("Target", {}).get("Port"),
            "availability_zone": item.get("Target", {}).get("AvailabilityZone"),
        }
        for item in descriptions
    ]
    if any(
        not isinstance(item["id"], str)
        or not item["id"]
        or not isinstance(item["port"], int)
        for item in result
    ):
        raise TargetReadinessRefusal("ALB_TARGET_IDENTITY_MALFORMED")
    return sorted(result, key=lambda item: (item["id"], item["port"], str(item["availability_zone"])))


def classify_target_health_response(
    descriptions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Classify only readiness-relevant fields from a target-health response."""
    if not descriptions:
        return {"classification": "RETRY", "reason_code": "ALB_TARGETS_ABSENT"}
    states = [
        str(item.get("TargetHealth", {}).get("State", ""))
        for item in descriptions
    ]
    reasons = [
        str(item.get("TargetHealth", {}).get("Reason", ""))
        for item in descriptions
        if item.get("TargetHealth", {}).get("State") == "initial"
    ]
    if all(state == "healthy" for state in states):
        identity = _target_identity(descriptions)
        return {
            "classification": "HEALTHY",
            "reason_code": "ALB_TARGETS_HEALTHY",
            "identity": identity,
        }
    if all(state in {"healthy", "initial"} for state in states) and reasons and all(
        reason in RETRYABLE_INITIAL_REASONS for reason in reasons
    ):
        return {
            "classification": "RETRY",
            "reason_code": "ALB_TARGETS_INITIAL",
        }
    raise TargetReadinessRefusal("ALB_TARGET_TERMINAL_UNHEALTHY")


def wait_for_stable_target_health(
    client: Any,
    wait_seconds: int = TARGET_HEALTH_WAIT_SECONDS,
    *,
    stable_observations: int = TARGET_HEALTH_STABLE_OBSERVATIONS,
    poll_seconds: int = TARGET_HEALTH_POLL_SECONDS,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Wait for one exact target set to remain wholly healthy."""
    if (
        wait_seconds < 1
        or wait_seconds > TARGET_HEALTH_WAIT_SECONDS
        or stable_observations != TARGET_HEALTH_STABLE_OBSERVATIONS
        or poll_seconds != TARGET_HEALTH_POLL_SECONDS
    ):
        raise TargetReadinessRefusal("ALB_TARGET_HEALTH_WAIT_BOUND_DIFFERS")
    deadline = monotonic() + wait_seconds
    consecutive = 0
    stable_identity: list[dict[str, Any]] | None = None
    polls = 0
    while True:
        polls += 1
        try:
            load_balancers = client.describe_load_balancers(Names=[ALB_NAME]).get(
                "LoadBalancers", []
            )
        except ClientError as exc:
            raise TargetReadinessRefusal("ALB_READ_API_REFUSED") from exc
        if len(load_balancers) > 1:
            raise TargetReadinessRefusal("ALB_IDENTITY_AMBIGUOUS")
        if not load_balancers:
            retry = True
        else:
            alb = load_balancers[0]
            if (
                alb.get("LoadBalancerName") != ALB_NAME
                or alb.get("Scheme") != "internal"
                or alb.get("Type") != "application"
                or sorted(alb.get("SecurityGroups", [])) != [ALB_SECURITY_GROUP]
            ):
                raise TargetReadinessRefusal("ALB_BOUNDARY_DIFFERS")
            state = alb.get("State", {}).get("Code")
            if state == "provisioning":
                retry = True
            elif state != "active":
                raise TargetReadinessRefusal("ALB_STATE_TERMINAL")
            else:
                try:
                    target_groups = client.describe_target_groups(
                        LoadBalancerArn=str(alb.get("LoadBalancerArn", ""))
                    ).get("TargetGroups", [])
                except ClientError as exc:
                    raise TargetReadinessRefusal("ALB_READ_API_REFUSED") from exc
                if len(target_groups) > 1:
                    raise TargetReadinessRefusal("ALB_TARGET_GROUP_AMBIGUOUS")
                if not target_groups:
                    retry = True
                else:
                    target_group_arn = str(target_groups[0].get("TargetGroupArn", ""))
                    if not target_group_arn:
                        raise TargetReadinessRefusal("ALB_TARGET_GROUP_ARN_ABSENT")
                    try:
                        descriptions = client.describe_target_health(
                            TargetGroupArn=target_group_arn
                        ).get("TargetHealthDescriptions", [])
                    except ClientError as exc:
                        raise TargetReadinessRefusal("ALB_READ_API_REFUSED") from exc
                    classification = classify_target_health_response(descriptions)
                    if classification["classification"] == "HEALTHY":
                        identity = classification["identity"]
                        if identity == stable_identity:
                            consecutive += 1
                        else:
                            stable_identity = identity
                            consecutive = 1
                        if consecutive == stable_observations:
                            return {
                                "load_balancer_active": True,
                                "target_count": len(identity),
                                "target_set_sha256": canonical_sha256(identity),
                                "stable_healthy_observations": consecutive,
                                "poll_interval_seconds": poll_seconds,
                                "maximum_wait_seconds": wait_seconds,
                                "polls": polls,
                                "retryable_initial_reasons": sorted(
                                    RETRYABLE_INITIAL_REASONS
                                ),
                            }
                        retry = True
                    else:
                        consecutive = 0
                        stable_identity = None
                        retry = True
        if not retry:
            raise TargetReadinessRefusal("ALB_TARGET_HEALTH_INTERNAL_STATE")
        if monotonic() >= deadline:
            raise TargetReadinessRefusal("ALB_TARGET_STABLE_HEALTH_TIMEOUT")
        sleep(poll_seconds)


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
    _exact_one([item for item in rules if item.get("IsDefault")], "default rule")
    non_default = [item for item in rules if not item.get("IsDefault")]
    if len(non_default) != 3:
        raise RuntimeEvidenceRefusal("exactly three non-default rules are required")
    target_group = _exact_one(
        client.describe_target_groups(LoadBalancerArn=alb_arn).get("TargetGroups", []),
        "target group",
    )
    target_group_arn = str(target_group.get("TargetGroupArn", ""))
    route_arns: list[str] = []
    observed_priorities: set[str] = set()
    for rule in non_default:
        priority = str(rule.get("Priority", ""))
        expected_paths = EXPECTED_ROUTES.get(priority)
        conditions = rule.get("Conditions", [])
        actions = rule.get("Actions", [])
        if expected_paths is None or priority in observed_priorities:
            raise RuntimeEvidenceRefusal("B6 route priority differs")
        if len(conditions) != 1 or len(actions) != 1:
            raise RuntimeEvidenceRefusal("B6 route condition or action count differs")
        condition = conditions[0]
        configured_paths = condition.get("PathPatternConfig", {}).get("Values", [])
        if (
            condition.get("Field") != "path-pattern"
            or set(configured_paths) != set(expected_paths)
        ):
            raise RuntimeEvidenceRefusal("B6 route path differs")
        action = actions[0]
        forward_targets = action.get("ForwardConfig", {}).get("TargetGroups", [])
        target_arns = {item.get("TargetGroupArn") for item in forward_targets}
        if (
            action.get("Type") != "forward"
            or len(forward_targets) != 1
            or target_arns != {target_group_arn}
        ):
            raise RuntimeEvidenceRefusal("B6 route target differs")
        rule_arn = str(rule.get("RuleArn", ""))
        if not rule_arn:
            raise RuntimeEvidenceRefusal("B6 route ARN is absent")
        observed_priorities.add(priority)
        route_arns.append(rule_arn)
    if observed_priorities != set(EXPECTED_ROUTES):
        raise RuntimeEvidenceRefusal("B6 route set differs")
    target_health = client.describe_target_health(
        TargetGroupArn=target_group_arn
    ).get("TargetHealthDescriptions", [])
    if not target_health or any(
        item.get("TargetHealth", {}).get("State") != "healthy" for item in target_health
    ):
        raise RuntimeEvidenceRefusal("B6 target is not healthy")
    route_arns.sort()
    tagged_arns = [alb_arn, listener_arn, *route_arns]
    tags = _tag_map(client, tagged_arns)
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
        "route_count": 3,
        "route_priorities": sorted(EXPECTED_ROUTES),
        "tag_mutation_resource_arns": [listener_arn, *route_arns],
        "target_healthy": True,
        "creation_time_exact_tags": True,
        "required_tag_count": len(REQUIRED_TAGS),
        "tagged_resource_count": 5,
        "resource_arn_set_sha256": canonical_sha256(sorted(tagged_arns)),
    }


def wait_for_stable_live_shape(
    client: Any,
    wait_seconds: int = RUNTIME_SHAPE_WAIT_SECONDS,
    *,
    stable_observations: int = RUNTIME_SHAPE_STABLE_OBSERVATIONS,
    poll_seconds: int = RUNTIME_SHAPE_POLL_SECONDS,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    if (
        wait_seconds < 1
        or wait_seconds > RUNTIME_SHAPE_WAIT_SECONDS
        or stable_observations != RUNTIME_SHAPE_STABLE_OBSERVATIONS
        or poll_seconds != RUNTIME_SHAPE_POLL_SECONDS
    ):
        raise RuntimeEvidenceRefusal("ALB live-shape wait boundary differs")
    deadline = monotonic() + wait_seconds
    consecutive = 0
    polls = 0
    stable_hash: str | None = None
    last_error: RuntimeEvidenceRefusal | None = None
    while True:
        polls += 1
        try:
            observed = verify_live(client)
            observed_hash = canonical_sha256(observed)
            if observed_hash == stable_hash:
                consecutive += 1
            else:
                stable_hash = observed_hash
                consecutive = 1
            last_error = None
            if consecutive == stable_observations:
                return {
                    **observed,
                    "stable_runtime_shape_observations": consecutive,
                    "runtime_shape_verification_polls": polls,
                    "runtime_shape_poll_interval_seconds": poll_seconds,
                }
        except RuntimeEvidenceRefusal as exc:
            consecutive = 0
            stable_hash = None
            last_error = exc
        if monotonic() >= deadline:
            if last_error is not None:
                raise last_error
            raise RuntimeEvidenceRefusal("ALB live shape did not remain stable")
        sleep(poll_seconds)


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
    *,
    receipt: dict[str, Any],
    receipt_sha256: str,
    fargate_receipt: dict[str, Any],
    fargate_receipt_sha256: str,
    controller_logs: str,
) -> dict[str, Any]:
    if receipt.get("stage") != "alb_ready" or receipt.get("status") != "PASS":
        raise RuntimeEvidenceRefusal("functional ALB receipt is not PASS")
    proof = dict(receipt.get("payload", {}))
    dependencies = receipt.get("dependencies", {})
    if set(dependencies) != {"endpoints_ready"}:
        raise RuntimeEvidenceRefusal("functional ALB readiness dependency differs")
    if (
        fargate_receipt.get("stage") != "fargate_probe"
        or fargate_receipt.get("status") != "PASS"
        or fargate_receipt.get("dependencies") != {"alb_ready": receipt_sha256}
        or re.fullmatch(r"[0-9a-f]{64}", fargate_receipt_sha256) is None
    ):
        raise RuntimeEvidenceRefusal("functional Fargate receipt binding differs")
    proof["fargate_probe_receipt_sha256"] = fargate_receipt_sha256
    proof["receipt_sha256"] = receipt_sha256
    proof["alb_ready_receipt_sha256"] = receipt_sha256
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
        raise RuntimeEvidencePending("controller logs are unavailable")
    return process.stdout


def wait_for_stable_tag_classification(
    *,
    kubeconfig: Path,
    since_time: str,
    receipt: dict[str, Any],
    receipt_sha256: str,
    fargate_receipt: dict[str, Any],
    fargate_receipt_sha256: str,
    wait_seconds: int = TAG_CLASSIFICATION_WAIT_SECONDS,
    monotonic: Any = time.monotonic,
    sleep: Any = time.sleep,
) -> dict[str, Any]:
    if wait_seconds < 1 or wait_seconds > TAG_CLASSIFICATION_WAIT_SECONDS:
        raise RuntimeEvidenceRefusal("tag classification wait boundary differs")
    deadline = monotonic() + wait_seconds
    previous: str | None = None
    consecutive = 0
    polls = 0
    while True:
        polls += 1
        try:
            result = classify_runtime(
                receipt=receipt,
                receipt_sha256=receipt_sha256,
                fargate_receipt=fargate_receipt,
                fargate_receipt_sha256=fargate_receipt_sha256,
                controller_logs=_controller_logs(kubeconfig, since_time),
            )
            encoded = json.dumps(result, sort_keys=True, separators=(",", ":"))
            if encoded == previous:
                consecutive += 1
            else:
                previous = encoded
                consecutive = 1
            if consecutive == TAG_CLASSIFICATION_STABLE_OBSERVATIONS:
                return {
                    **result,
                    "stable_tag_classification_observations": consecutive,
                    "tag_classification_verification_polls": polls,
                    "tag_classification_poll_interval_seconds": (
                        TAG_CLASSIFICATION_POLL_SECONDS
                    ),
                }
        except RuntimeEvidencePending:
            previous = None
            consecutive = 0
        if monotonic() >= deadline:
            raise RuntimeEvidenceRefusal(
                "tag classification did not remain stable before timeout"
            )
        sleep(TAG_CLASSIFICATION_POLL_SECONDS)


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="mode", required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--profile", default=PROFILE)
    wait_ready = subparsers.add_parser("wait-ready")
    wait_ready.add_argument("--profile", default=PROFILE)
    wait_ready.add_argument(
        "--wait-seconds", type=int, default=TARGET_HEALTH_WAIT_SECONDS
    )
    classify_parser = subparsers.add_parser("classify")
    classify_parser.add_argument("--kubeconfig", type=Path, required=True)
    classify_parser.add_argument("--receipts-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.mode in {"verify", "wait-ready"}:
            client = _session_client(args.profile)
            if args.mode == "wait-ready":
                readiness = wait_for_stable_target_health(
                    client, wait_seconds=args.wait_seconds
                )
                result = {**wait_for_stable_live_shape(client), **readiness}
            else:
                result = verify_live(client)
        else:
            path = args.receipts_dir / "alb_ready.json"
            encoded = path.read_bytes()
            receipt = json.loads(encoded)
            fargate_path = args.receipts_dir / "fargate_probe.json"
            fargate_encoded = fargate_path.read_bytes()
            fargate_receipt = json.loads(fargate_encoded)
            controller = json.loads((args.receipts_dir / "controller_ready.json").read_bytes())
            since_time = controller.get("recorded_utc")
            if not isinstance(since_time, str) or not since_time.endswith("Z"):
                raise RuntimeEvidenceRefusal("controller receipt timestamp is malformed")
            result = wait_for_stable_tag_classification(
                kubeconfig=args.kubeconfig,
                since_time=since_time,
                receipt=receipt,
                receipt_sha256=hashlib.sha256(encoded).hexdigest(),
                fargate_receipt=fargate_receipt,
                fargate_receipt_sha256=hashlib.sha256(fargate_encoded).hexdigest(),
            )
    except (
        ClientError,
        OSError,
        json.JSONDecodeError,
        RuntimeEvidencePending,
        RuntimeEvidenceRefusal,
    ) as exc:
        reason_code = getattr(exc, "reason_code", "ALB_RUNTIME_BOUNDARY_REFUSED")
        print(json.dumps({"status": "REFUSED", "reason_code": reason_code}))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
