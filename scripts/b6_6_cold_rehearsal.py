#!/usr/bin/env python3
"""Run the entire consolidated B6.6 runner against a faked platform layer."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.b6_integration_receipts import (
    STAGE_A_EXECUTION_STAGES,
    STAGE_A_STAGES,
    WINDOW_STAGES,
    ReceiptStore,
    canonical_json,
    sha256_file,
)
from scripts.b6_6_credential import (
    KMS_KEY,
    SECRET_ARN,
    SECRET_NAME,
    rotate_and_verify,
)
from scripts.b6_6_fargate_probe import (
    PROBE_BAD_STATUS_EXIT_CODE,
    PROBE_CONNECT_EXIT_CODE,
    PROBE_DNS_EXIT_CODE,
    _safe_task_result,
)
from scripts.b6_6_deadline import DeadlineControl, GROUPS
from scripts.b6_6_lbc_runtime import (
    ALB_NAME,
    ALB_SECURITY_GROUP,
    TargetReadinessRefusal,
    classify_target_health_response,
    wait_for_stable_target_health,
)
from scripts.b6_6_bindings import COLD_PATH, REQUIRED_SOURCES
from scripts.b6_6_aws_read_fixtures import audit as audit_aws_read_fixtures
from scripts.b6_6_post_mutation_audit import audit as audit_post_mutation
from scripts.b6_6_runner import RunContext, Runner, StageFailure, StageResult
from scripts.b6_6_stage_a import (
    MAXIMUM_COST_USD,
    MAXIMUM_SECONDS,
    STABLE_PROBE_PASSES,
    StageAContext,
    StageARefusal,
    StageARunner,
)
from scripts.b6_6_probe import (
    DIAGNOSTIC_MAX_UTF8_BYTES,
    PROOF_EXIT_CODES,
    REGISTRY,
    ProbeRefusal as ConversationProbeRefusal,
    evaluate_file_response,
    sanitize_response_body,
)
from scripts.check_b6_6_window_plan import lint_rendered_plan_description_charset


RUNNER_SOURCES = tuple(sorted(REQUIRED_SOURCES - {COLD_PATH}))
GUARDS = {
    "stage0": ["persistent_secret", "operator_deny", "token_shape", "exact_fresh_version_three_stable_observations", "exact_safe_refusal_reason"],
    "deadline": ["deadline_first_4500_seconds_three_stable_observations"],
    "workers_ready": ["bounded_worker_registration_1200_seconds_three_stable_observations"],
    "dra_ready": ["digest_pinned_dra_before_endpoints_three_stable_observations"],
    "rag_ready": ["digest_pinned_rag_before_endpoints_three_stable_observations"],
    "asr_ready": ["digest_pinned_loader_and_asr_before_endpoints_three_stable_observations"],
    "tts_ready": ["digest_pinned_tts_before_endpoints_three_stable_observations"],
    "llm_ready": ["digest_pinned_llm_before_endpoints_three_stable_observations"],
    "orchestrator_ready": ["digest_pinned_orchestrator_before_endpoints_three_stable_observations"],
    "controller_window": ["controller_plan_1_0_0_with_named_resource_receipt"],
    "controller_ready": ["digest_pinned_controller_before_endpoints_three_stable_observations"],
    "pre_endpoint_images": ["seven_pods_eight_resident_child_digests_three_stable_observations"],
    "terraform_window": ["endpoint_plan_13_0_0_with_named_resources_controller_noop"],
    "endpoints_ready": ["probe_exclusive_endpoints_available_900_seconds_three_stable_observations"],
    "alb_ready": ["hostname_active_three_stable_healthy_target_and_runtime_shape_observations"],
    "fargate_probe": ["private_probe_24_attempt_layer_specific_retry_two_stable_terminal_observations"],
    "alb_tag_mutation_warning": ["bounded_nonfatal_tag_rule_always_fatal_list_three_stable_observations"],
    "file_proof": ["synthetic_file_contract", "assertion_specific_sanitized_diagnostic"],
    "websocket_proof": ["synthetic_websocket_contract"],
    "cancellation_proof": ["cancel_within_250ms"],
    "failure_drills": ["dependency_refusal_without_pod_recreation_three_stable_endpoint_observations"],
    "isolation_proof": ["orchestrator_only_ingress_dependencies_clusterip_three_stable_observations"],
    "cleanup": ["three_stable_zero_observations_before_status_keyed_deadline_reconciliation_persistent_secret_retained"],
}


def _aws_read_fixture_fidelity() -> dict[str, Any]:
    return audit_aws_read_fixtures(ROOT)


def _terraform_description_charset_lint() -> dict[str, Any]:
    path = ROOT / "platform/evidence/B6-RENDERED-TERRAFORM-DESCRIPTIONS-2026-001.json"
    evidence = json.loads(path.read_bytes())
    projection = evidence.get("description_projection", {})
    items = projection.get("items")
    if (
        evidence.get("status") != "PASS_READ_ONLY_RENDERED_PLAN_PROJECTION"
        or evidence.get("aws_mutations") != 0
        or evidence.get("kubernetes_mutations") != 0
        or not isinstance(items, list)
        or hashlib.sha256(canonical_json(items)).hexdigest()
        != projection.get("canonical_sha256")
    ):
        raise AssertionError("rendered Terraform description projection differs")
    projected_plan = {
        "rendered_description_projection": [
            {"description": item.get("value")} for item in items
        ]
    }
    result = lint_rendered_plan_description_charset(projected_plan)
    expected = {
        "description_fields": projection.get("description_fields"),
        "string_descriptions": projection.get("string_descriptions"),
        "null_descriptions": projection.get("null_descriptions"),
        "invalid_descriptions": 0,
        "allowed_character_class": projection.get("allowed_character_class"),
    }
    if any(result.get(key) != value for key, value in expected.items()):
        raise AssertionError("rendered Terraform description lint result differs")
    injected = json.loads(json.dumps(projected_plan))
    injected["rendered_description_projection"][0]["description"] = "ECR's S3"
    try:
        lint_rendered_plan_description_charset(injected)
    except ValueError as exc:
        if "U+0027" not in str(exc):
            raise AssertionError("apostrophe refusal did not identify U+0027") from exc
    else:
        raise AssertionError("apostrophe did not refuse description lint")
    return {
        **result,
        "projection_path": str(path.relative_to(ROOT)),
        "projection_sha256": sha256_file(path),
        "projection_inventory_sha256": projection["canonical_sha256"],
        "invalid_description_refusal_cases": 1,
        "real_aws_calls": 0,
    }


class _GateClock:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


class _RecordedTargetHealthClient:
    def __init__(self, descriptions: list[dict[str, Any]]) -> None:
        self.descriptions = descriptions

    def describe_load_balancers(self, **_: Any) -> dict[str, Any]:
        return {
            "LoadBalancers": [
                {
                    "LoadBalancerName": ALB_NAME,
                    "LoadBalancerArn": "arn:aws:elasticloadbalancing:eu-central-1:558069890522:loadbalancer/app/medzen-b6-window/cold",
                    "Scheme": "internal",
                    "Type": "application",
                    "SecurityGroups": [ALB_SECURITY_GROUP],
                    "State": {"Code": "active"},
                }
            ]
        }

    def describe_target_groups(self, **_: Any) -> dict[str, Any]:
        return {
            "TargetGroups": [
                {
                    "TargetGroupArn": "arn:aws:elasticloadbalancing:eu-central-1:558069890522:targetgroup/k8s-medzen-speechor/cold"
                }
            ]
        }

    def describe_target_health(self, **_: Any) -> dict[str, Any]:
        return {"TargetHealthDescriptions": self.descriptions}


def _new_gate_rehearsal() -> dict[str, Any]:
    healthy_path = (
        ROOT
        / "tests/fixtures/aws/elbv2-describe-target-health-medzen-ehrbase-healthy.json"
    )
    empty_path = (
        ROOT / "tests/fixtures/aws/elbv2-describe-target-health-cache-proxy-test.json"
    )
    healthy = json.loads(healthy_path.read_bytes())["TargetHealthDescriptions"]
    empty = json.loads(empty_path.read_bytes())["TargetHealthDescriptions"]
    health_clock = _GateClock()
    target_pass = wait_for_stable_target_health(
        _RecordedTargetHealthClient(healthy),
        wait_seconds=30,
        monotonic=health_clock.monotonic,
        sleep=health_clock.sleep,
    )
    timeout_clock = _GateClock()
    try:
        wait_for_stable_target_health(
            _RecordedTargetHealthClient(empty),
            wait_seconds=20,
            monotonic=timeout_clock.monotonic,
            sleep=timeout_clock.sleep,
        )
    except TargetReadinessRefusal as exc:
        target_timeout = str(exc)
    else:
        raise AssertionError("recorded empty target-health response did not refuse")
    initial = json.loads(json.dumps(healthy))
    for item in initial:
        item["TargetHealth"] = {
            "State": "initial",
            "Reason": "Elb.RegistrationInProgress",
        }
    if classify_target_health_response(initial) != {
        "classification": "RETRY",
        "reason_code": "ALB_TARGETS_INITIAL",
    }:
        raise AssertionError("registration-in-progress is not a bounded retry")

    task = {
        "taskArn": "arn:aws:ecs:eu-central-1:558069890522:task/cold",
        "lastStatus": "STOPPED",
        "stopCode": "EssentialContainerExited",
        "containers": [{"lastStatus": "STOPPED", "exitCode": 0}],
    }
    probe_pass = _safe_task_result(task)
    expected_probe_failures = {
        PROBE_DNS_EXIT_CODE: "PROBE_DNS_RETRIES_EXHAUSTED",
        PROBE_CONNECT_EXIT_CODE: "PROBE_CONNECT_RETRIES_EXHAUSTED",
        PROBE_BAD_STATUS_EXIT_CODE: "PROBE_BAD_STATUS_OR_BODY_RETRIES_EXHAUSTED",
    }
    probe_failures: list[dict[str, Any]] = []
    for exit_code, reason_code in expected_probe_failures.items():
        failed = json.loads(json.dumps(task))
        failed["containers"][0]["exitCode"] = exit_code
        result = _safe_task_result(failed)
        if result.get("reason_code") != reason_code:
            raise AssertionError("probe exit code did not identify its failing layer")
        probe_failures.append(
            {
                "gate": "in_container_retry",
                "injected_exit_code": exit_code,
                "outcome": "REFUSED",
                "reason_code": reason_code,
            }
        )
    return {
        "status": "PASS",
        "full_passes": [
            {
                "gate": "alb_target_health",
                "stable_healthy_observations": target_pass[
                    "stable_healthy_observations"
                ],
                "target_count": target_pass["target_count"],
            },
            {
                "gate": "in_container_retry",
                "outcome": probe_pass["status"],
                "container_exit_code": 0,
            },
        ],
        "injected_failures": [
            {
                "gate": "alb_target_health",
                "injection": "RECORDED_EMPTY_RESPONSE_REPEATED_TO_TIMEOUT",
                "outcome": "REFUSED",
                "reason_code": target_timeout,
            },
            *probe_failures,
        ],
        "registration_in_progress_classification": "BOUNDED_RETRY",
        "recorded_fixture_hashes": {
            str(healthy_path.relative_to(ROOT)): sha256_file(healthy_path),
            str(empty_path.relative_to(ROOT)): sha256_file(empty_path),
        },
        "real_aws_calls": 0,
        "real_kubectl_calls": 0,
    }


def _passing_file_response() -> dict[str, Any]:
    return {
        "reply": {
            "tts_backend": "text_only",
            "text": "synthetic reply",
            "citations": [
                {"id": "one", "snippet": "synthetic one"},
                {"id": "two", "snippet": "synthetic two"},
                {"id": "three", "snippet": "synthetic three"},
            ],
        },
        "model_versions": {
            "asr": "v0",
            "registry_snapshot": REGISTRY,
            "llm": "fake-bedrock-local-v1",
            "rag": "embedded-synthetic-v1",
            "tts": None,
        },
    }


def _file_assertion_injections() -> list[tuple[int, bytes, str]]:
    good = _passing_file_response()
    encoded = lambda value: json.dumps(value, sort_keys=True).encode()
    cases: list[tuple[int, bytes, str]] = [
        (503, encoded(good), "FILE_HTTP_STATUS_IS_200"),
        (200, b"{not-json", "FILE_RESPONSE_IS_JSON"),
        (200, b"[]", "FILE_RESPONSE_IS_OBJECT"),
        (200, encoded({**good, "reply": None}), "FILE_REPLY_IS_OBJECT"),
        (
            200,
            encoded({**good, "reply": {**good["reply"], "tts_backend": "fish"}}),
            "FILE_TTS_BACKEND_IS_TEXT_ONLY",
        ),
        (
            200,
            encoded({**good, "reply": {**good["reply"], "citations": "three"}}),
            "FILE_CITATIONS_IS_LIST",
        ),
        (
            200,
            encoded({**good, "reply": {**good["reply"], "citations": [{}, {}]}}),
            "FILE_CITATION_COUNT_IS_THREE",
        ),
        (200, encoded({**good, "model_versions": []}), "FILE_MODEL_VERSIONS_IS_OBJECT"),
    ]
    wrong_keys = copy.deepcopy(good)
    wrong_keys["model_versions"]["extra"] = "wrong"
    cases.append((200, encoded(wrong_keys), "FILE_MODEL_VERSION_KEYS_ARE_EXACT"))
    for assertion, key, value in (
        ("FILE_REGISTRY_SNAPSHOT_MATCHES", "registry_snapshot", "wrong"),
        ("FILE_ASR_VERSION_IS_V0", "asr", "v1"),
        ("FILE_LLM_VERSION_IS_FAKE_LOCAL", "llm", "real-provider"),
        ("FILE_TTS_VERSION_IS_NULL", "tts", "fish"),
    ):
        changed = copy.deepcopy(good)
        changed["model_versions"][key] = value
        cases.append((200, encoded(changed), assertion))
    return cases


def _proof_diagnostic_rehearsal() -> dict[str, Any]:
    passing_raw = json.dumps(_passing_file_response(), sort_keys=True).encode()
    if evaluate_file_response(200, passing_raw).get("status") != "PASS":
        raise AssertionError("passing file proof did not pass")
    failures: list[dict[str, Any]] = []
    exit_codes: set[int] = set()
    for status, raw, expected_assertion in _file_assertion_injections():
        try:
            evaluate_file_response(status, raw)
        except ConversationProbeRefusal as exc:
            diagnostic = exc.diagnostic()
        else:
            raise AssertionError(f"file assertion injection passed: {expected_assertion}")
        if (
            diagnostic.get("failed_assertion") != expected_assertion
            or diagnostic.get("probe_exit_code")
            != PROOF_EXIT_CODES[expected_assertion]
            or diagnostic.get("http_status") != status
            or len(diagnostic.get("sanitized_response_body", "").encode("utf-8"))
            > DIAGNOSTIC_MAX_UTF8_BYTES
            or diagnostic.get("synthetic_only") is not True
            or diagnostic.get("phi_present") is not False
        ):
            raise AssertionError("file assertion diagnostic boundary differs")
        exit_codes.add(diagnostic["probe_exit_code"])
        failures.append(
            {
                "failed_assertion": diagnostic["failed_assertion"],
                "probe_exit_code": diagnostic["probe_exit_code"],
                "http_status": diagnostic["http_status"],
                "sanitized_response_body_sha256": hashlib.sha256(
                    diagnostic["sanitized_response_body"].encode()
                ).hexdigest(),
                "outcome": "REFUSED",
            }
        )
    if len(exit_codes) != len(failures):
        raise AssertionError("file assertion exit codes are not distinct")
    sanitized, truncated = sanitize_response_body(
        b'Bearer forbidden "token":"forbidden" ' + b"x" * 3000
    )
    if (
        not truncated
        or "forbidden" in sanitized
        or len(sanitized.encode()) > DIAGNOSTIC_MAX_UTF8_BYTES
    ):
        raise AssertionError("synthetic response sanitizer boundary differs")
    return {
        "status": "PASS",
        "passing_file_proofs": 1,
        "injected_assertion_failures": len(failures),
        "distinct_exit_codes": len(exit_codes),
        "failures": failures,
        "diagnostic_max_utf8_bytes": DIAGNOSTIC_MAX_UTF8_BYTES,
        "sanitizer_redaction_and_truncation_cases": 1,
        "real_aws_calls": 0,
        "real_kubectl_calls": 0,
    }


class _RecordedDeadlineAutoscaling:
    def __init__(self, actions: dict[str, list[dict[str, Any]]]) -> None:
        self.actions = actions
        self.group_fixtures = {
            name: json.loads(
                (
                    ROOT
                    / f"tests/fixtures/aws/autoscaling-describe-auto-scaling-groups-medzen-{name}.json"
                ).read_bytes()
            )
            for name in GROUPS
        }

    def describe_auto_scaling_groups(self, AutoScalingGroupNames: list[str]) -> dict[str, Any]:
        name = next(
            key for key, binding in GROUPS.items() if binding["asg"] == AutoScalingGroupNames[0]
        )
        return copy.deepcopy(self.group_fixtures[name])

    def describe_scheduled_actions(self, AutoScalingGroupName: str) -> dict[str, Any]:
        name = next(
            key for key, binding in GROUPS.items() if binding["asg"] == AutoScalingGroupName
        )
        return {"ScheduledUpdateGroupActions": copy.deepcopy(self.actions[name])}

    def delete_scheduled_action(
        self, AutoScalingGroupName: str, ScheduledActionName: str
    ) -> None:
        name = next(
            key for key, binding in GROUPS.items() if binding["asg"] == AutoScalingGroupName
        )
        if ScheduledActionName != GROUPS[name]["action"]:
            raise AssertionError("cold cleanup attempted an unexpected deadline")
        self.actions[name] = []


class _RecordedDeadlineEks:
    def __init__(self) -> None:
        self.fixtures = {
            name: json.loads(
                (
                    ROOT
                    / f"tests/fixtures/aws/eks-describe-nodegroup-medzen-speech-{name}.json"
                ).read_bytes()
            )
            for name in GROUPS
        }

    def describe_nodegroup(self, clusterName: str, nodegroupName: str) -> dict[str, Any]:
        if clusterName != "medzen-speech":
            raise AssertionError("cold cleanup cluster differs")
        return copy.deepcopy(self.fixtures[nodegroupName])


def _pre_deadline_cleanup_rehearsal() -> dict[str, Any]:
    empty_fixture_paths = {
        name: ROOT
        / f"tests/fixtures/aws/autoscaling-describe-scheduled-actions-medzen-{name}.json"
        for name in GROUPS
    }
    empty_actions = {
        name: json.loads(path.read_bytes())["ScheduledUpdateGroupActions"]
        for name, path in empty_fixture_paths.items()
    }
    absent = DeadlineControl(
        _RecordedDeadlineAutoscaling(copy.deepcopy(empty_actions)),
        _RecordedDeadlineEks(),
    ).cleanup_after_zero("ABSENT", sleep=lambda _: None)
    partial_actions = copy.deepcopy(empty_actions)
    partial_actions["cpu"] = [
        {"ScheduledActionName": GROUPS["cpu"]["action"]}
    ]
    refused = DeadlineControl(
        _RecordedDeadlineAutoscaling(partial_actions),
        _RecordedDeadlineEks(),
    ).cleanup_after_zero("REFUSED", sleep=lambda _: None)
    if (
        absent.get("deadline_actions_before") != 0
        or absent.get("deadline_actions_after") != 0
        or refused.get("deadline_actions_before") != 1
        or refused.get("deadline_actions_removed") != 1
        or refused.get("deadline_actions_after") != 0
    ):
        raise AssertionError("pre-deadline cleanup reconciliation differs")
    return {
        "status": "PASS",
        "injected_paths": [
            "NO_DEADLINE_RECEIPT_NO_ACTIONS",
            "REFUSED_DEADLINE_RECEIPT_ONE_EXACT_PARTIAL_ACTION",
        ],
        "absent_receipt_result": absent,
        "refused_receipt_result": refused,
        "recorded_fixture_hashes": {
            str(path.relative_to(ROOT)): sha256_file(path)
            for path in empty_fixture_paths.values()
        },
        "real_aws_calls": 0,
        "real_kubectl_calls": 0,
        "aws_mutations": 0,
    }


class FakeSecretClient:
    def __init__(self, historical_versions: int = 7):
        self.versions = {
            hashlib.sha256(f"history-{index}".encode()).hexdigest(): []
            for index in range(historical_versions)
        }
        if self.versions:
            current = next(reversed(self.versions))
            self.versions[current] = ["AWSCURRENT"]

    def describe_secret(self, **_: Any) -> dict[str, Any]:
        return {"Name": SECRET_NAME, "ARN": SECRET_ARN, "KmsKeyId": KMS_KEY}

    def list_secret_version_ids(self, **_: Any) -> dict[str, Any]:
        return {
            "Versions": [
                {"VersionId": version, "VersionStages": stages}
                for version, stages in self.versions.items()
            ]
        }

    def put_secret_value(self, **kwargs: Any) -> dict[str, Any]:
        value = kwargs["SecretString"].encode()
        version = kwargs["ClientRequestToken"]
        if hashlib.sha256(value).hexdigest() != version:
            raise AssertionError("version ID is not the canonical secret-value hash")
        for existing, stages in self.versions.items():
            if "AWSCURRENT" in stages:
                self.versions[existing] = ["AWSPREVIOUS"]
            elif "AWSPREVIOUS" in stages:
                self.versions[existing] = []
        self.versions[version] = ["AWSCURRENT"]
        return {"ARN": SECRET_ARN, "VersionId": version}

    def get_secret_value(self, **_: Any) -> dict[str, Any]:
        raise ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
            "GetSecretValue",
        )


class _VisibilityClock:
    def __init__(self) -> None:
        self.seconds = 0.0

    def monotonic(self) -> float:
        return self.seconds

    def sleep(self, seconds: float) -> None:
        self.seconds += seconds


class _StaleThenCurrentSecretClient(FakeSecretClient):
    """Replay two stale list reads before three stable current reads."""

    def __init__(self) -> None:
        super().__init__(historical_versions=1)
        self.created_version: str | None = None
        self.visibility_reads = 0
        self.put_calls = 0
        self.stale_fixture = json.loads(
            (
                ROOT
                / "tests/fixtures/aws/secretsmanager-list-secret-version-ids-stale-after-put.json"
            ).read_bytes()
        )
        self.current_fixture = json.loads(
            (
                ROOT
                / "tests/fixtures/aws/secretsmanager-list-secret-version-ids-current-after-put.json"
            ).read_bytes()
        )

    def put_secret_value(self, **kwargs: Any) -> dict[str, Any]:
        self.put_calls += 1
        result = super().put_secret_value(**kwargs)
        self.created_version = str(result["VersionId"])
        return result

    def list_secret_version_ids(self, **_: Any) -> dict[str, Any]:
        if self.created_version is None:
            return super().list_secret_version_ids()
        self.visibility_reads += 1
        if self.visibility_reads <= 2:
            return copy.deepcopy(self.stale_fixture)
        response = copy.deepcopy(self.current_fixture)
        for item in response["Versions"]:
            if item["VersionId"] == "__EXACT_CREATED_VERSION_ID__":
                item["VersionId"] = self.created_version
        return response


def _credential_visibility_rehearsal() -> dict[str, Any]:
    clock = _VisibilityClock()
    client = _StaleThenCurrentSecretClient()
    with tempfile.TemporaryDirectory(prefix="medzen-b6-credential-visibility-") as temporary:
        result = rotate_and_verify(
            client,
            Path(temporary) / "token",
            material_factory=lambda size: bytes(range(size)),
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
    if (
        client.put_calls != 1
        or client.visibility_reads != 5
        or result.get("stable_current_observations") != 3
        or result.get("visibility_polls") != 5
    ):
        raise AssertionError("stale-to-current credential visibility replay differs")
    fixtures = (
        "tests/fixtures/aws/secretsmanager-list-secret-version-ids-stale-after-put.json",
        "tests/fixtures/aws/secretsmanager-list-secret-version-ids-current-after-put.json",
    )
    return {
        "status": "PASS",
        "injection": "TWO_STALE_READS_THEN_THREE_STABLE_CURRENT_READS",
        "created_version_verified_exactly": True,
        "put_secret_value_calls": client.put_calls,
        "visibility_polls": result["visibility_polls"],
        "stable_current_observations": result["stable_current_observations"],
        "additional_credentials_generated": 0,
        "recorded_fixture_hashes": {
            relative: sha256_file(ROOT / relative) for relative in fixtures
        },
        "real_aws_calls": 0,
        "aws_mutations": 0,
    }


class Clock:
    def __init__(self):
        self.value = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self) -> str:
        result = self.value.isoformat(timespec="seconds").replace("+00:00", "Z")
        self.value += timedelta(seconds=1)
        return result


class FakeOperations:
    def __init__(self, fail_stage: str | None):
        self.fail_stage = fail_stage
        self.secret = FakeSecretClient()
        self.guards_invoked: dict[str, list[str]] = {}
        self.platform_mutations = 0
        self.real_aws_calls = 0
        self.real_kubectl_calls = 0
        self.zero_state = True

    def before_run(self, context: RunContext) -> None:
        del context

    def execute(self, stage: str, context: RunContext) -> StageResult:
        self.guards_invoked[stage] = GUARDS[stage]
        if stage == "stage0":
            payload = rotate_and_verify(
                self.secret,
                context.token_file,
                material_factory=lambda size: bytes(range(size)),
                sleep=lambda _: None,
            )
        elif stage == "cleanup":
            context.token_file.unlink(missing_ok=True)
            self.zero_state = True
            payload = {
                "zero_state": True,
                "persistent_secret_retained": True,
                "local_material_removed": True,
                "deadline_actions": 0,
                "worker_instances": 0,
                "window_resources": 0,
            }
        else:
            if stage == "workers_ready":
                self.zero_state = False
            payload = {
                "cold_rehearsal": True,
                "invariants_verified": GUARDS[stage],
            }
        if self.fail_stage == stage:
            if stage == "stage0":
                raise StageFailure(
                    "STAGE0_CREDENTIAL_OR_PREFLIGHT_REFUSED",
                    {
                        "stage0_refusal": {
                            "reason_code": "STAGE0_TEST_REGISTRY_REFUSED",
                            "failed_assertion": "TEST_REGISTRY_PARAMETER_COUNT_IS_THREE",
                            "stage_exit_code": 35,
                            "safe_error_text": "injected safe stage-zero detail",
                            "pre_model_and_audio": True,
                        }
                    },
                )
            raise StageFailure(
                "INJECTED_COLD_REHEARSAL_FAILURE",
                {"injected_stage": stage, "guards_invoked": GUARDS[stage]},
            )
        return StageResult(payload=payload)

    def recover_cleanup(self, context: RunContext) -> dict[str, Any]:
        context.token_file.unlink(missing_ok=True)
        self.zero_state = True
        return {
            "recovery_completed": True,
            "zero_state": True,
            "persistent_secret_retained": True,
        }


class FakeStageAOperations:
    def __init__(self, fail_stage: str | None):
        self.fail_stage = fail_stage
        self.real_aws_calls = 0
        self.real_kubectl_calls = 0
        self.eks_worker_mutations = 0

    def before_run(self, context: StageAContext) -> None:
        del context

    def execute(self, stage: str, context: StageAContext) -> dict[str, Any]:
        del context
        if self.fail_stage == stage:
            raise StageARefusal("INJECTED_STAGE_A_FAILURE", {"injected_stage": stage})
        if stage.startswith("stage_a_probe_"):
            return {
                "ordinal": int(stage.rsplit("_", 1)[1]),
                "application_started": True,
                "image_pull_proven": True,
                "assign_public_ip": "DISABLED",
                "probe_task_security_group_count": 1,
            }
        if stage == "stage_a_cleanup":
            return {
                "cleanup_complete": True,
                "window_terraform_resources": 0,
                "cpu_desired": 0,
                "gpu_desired": 0,
                "eks_worker_mutations": 0,
            }
        return {
            "stage_a_guard_verified": stage,
            "eks_worker_mutations": 0,
        }

    def recover_cleanup(self, context: StageAContext) -> dict[str, Any]:
        del context
        return {
            "recovery_completed": True,
            "zero_state": True,
            "window_terraform_resources": 0,
            "probe_vpc_endpoints": 0,
            "probe_iam_roles": 0,
            "active_probe_ecs_clusters": 0,
            "cpu_desired": 0,
            "gpu_desired": 0,
        }


def _stage_a_scenario(root: Path, name: str, fail_stage: str | None) -> dict[str, Any]:
    directory = root / name
    operations = FakeStageAOperations(fail_stage)
    context = StageAContext(
        authorization=root / "fake-authorization.json",
        packet_sha256="0" * 64,
        receipts_dir=directory,
    )
    runner = StageARunner(operations, ReceiptStore(directory, clock=Clock()))
    result = runner.run(context)
    receipts = [
        {
            "stage": stage,
            "status": runner.store.load(stage)["status"],
            "sha256": sha256_file(runner.store.path(stage)),
        }
        for stage in STAGE_A_STAGES
        if runner.store.path(stage).exists()
    ]
    if fail_stage is None:
        if result.outcome != "PASS" or [item["stage"] for item in receipts] != list(STAGE_A_STAGES):
            raise AssertionError("Stage A cold pass did not produce its complete receipt chain")
        if any(item["status"] != "PASS" for item in receipts):
            raise AssertionError("Stage A cold pass contains a non-PASS receipt")
    else:
        statuses = {item["stage"]: item["status"] for item in receipts}
        if result.outcome != "REFUSED" or statuses.get(fail_stage) != "REFUSED":
            raise AssertionError(f"Stage A failure did not refuse exactly {fail_stage}")
        failure_receipt = runner.store.load(fail_stage)
        if (
            failure_receipt.get("payload", {}).get("safe_exception_text")
            != "INJECTED_STAGE_A_FAILURE"
        ):
            raise AssertionError("Stage A refusal lost its exact safe exception text")
        expected_cleanup = "REFUSED" if fail_stage == "stage_a_cleanup" else "PASS"
        if statuses.get("stage_a_cleanup") != expected_cleanup:
            raise AssertionError("Stage A injected cleanup status differs")
        if statuses.get("stage_a") != "REFUSED":
            raise AssertionError("Stage A aggregate did not refuse")
    return {
        "scenario": name,
        "injected_failure_stage": fail_stage,
        "outcome": result.outcome,
        "failure_stage": result.failure_stage,
        "cleanup_complete": result.cleanup_complete,
        "receipts": receipts,
        "real_aws_calls": operations.real_aws_calls,
        "real_kubectl_calls": operations.real_kubectl_calls,
        "eks_worker_mutations": operations.eks_worker_mutations,
        "maximum_seconds": MAXIMUM_SECONDS,
        "maximum_cost_usd": MAXIMUM_COST_USD,
        "required_consecutive_probe_passes": STABLE_PROBE_PASSES,
    }


def _scenario(root: Path, name: str, fail_stage: str | None) -> dict[str, Any]:
    directory = root / name
    operations = FakeOperations(fail_stage)
    context = RunContext(
        kubeconfig=root / "fake-kubeconfig",
        authorization=root / "fake-authorization.json",
        packet_sha256="0" * 64,
        receipts_dir=directory,
        token_file=root / f"{name}.token",
        attempt=1,
    )
    runner = Runner(operations, ReceiptStore(directory, clock=Clock()))
    result = runner.run(context)
    receipts = [
        {
            "stage": stage,
            "status": runner.store.load(stage)["status"],
            "sha256": sha256_file(runner.store.path(stage)),
        }
        for stage in WINDOW_STAGES
        if runner.store.path(stage).exists()
    ]
    if fail_stage is None:
        if result.outcome != "PASS" or len(receipts) != len(WINDOW_STAGES):
            raise AssertionError("full cold rehearsal did not produce 23 PASS receipts")
        if any(item["status"] != "PASS" for item in receipts):
            raise AssertionError("full cold rehearsal contains a non-PASS receipt")
    else:
        refused = [item for item in receipts if item["status"] == "REFUSED"]
        if result.outcome != "REFUSED" or [item["stage"] for item in refused] != [fail_stage]:
            raise AssertionError(f"injected failure did not refuse exactly {fail_stage}")
        cleanup = next(item for item in receipts if item["stage"] == "cleanup")
        expected_cleanup = "REFUSED" if fail_stage == "cleanup" else "PASS"
        if cleanup["status"] != expected_cleanup or not operations.zero_state:
            raise AssertionError("injected failure cleanup did not complete")
        if fail_stage == "stage0":
            stage0 = runner.store.load("stage0").get("payload", {})
            if (
                stage0.get("stage0_refusal", {}).get("safe_error_text")
                != "injected safe stage-zero detail"
            ):
                raise AssertionError("stage-zero refusal lost exact safe detail")
    if fail_stage is None:
        expected_guards = set(WINDOW_STAGES)
    else:
        expected_guards = set(
            WINDOW_STAGES[: WINDOW_STAGES.index(fail_stage) + 1]
        )
        if fail_stage != "cleanup":
            expected_guards.add("cleanup")
    if set(operations.guards_invoked) != expected_guards:
        raise AssertionError("cold rehearsal guard invocation set differs")
    return {
        "scenario": name,
        "injected_failure_stage": fail_stage,
        "outcome": result.outcome,
        "failure_stage": result.failure_stage,
        "cleanup_complete": operations.zero_state,
        "guards_invoked": operations.guards_invoked,
        "receipts": receipts,
        "real_aws_calls": operations.real_aws_calls,
        "real_kubectl_calls": operations.real_kubectl_calls,
        "platform_mutations": operations.platform_mutations,
    }


def run(output_dir: Path) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"cold rehearsal output already exists: {output_dir}")
    terraform_description_charset_lint = _terraform_description_charset_lint()
    aws_read_fixture_fidelity = _aws_read_fixture_fidelity()
    new_gate_rehearsal = _new_gate_rehearsal()
    proof_diagnostic_rehearsal = _proof_diagnostic_rehearsal()
    pre_deadline_cleanup_rehearsal = _pre_deadline_cleanup_rehearsal()
    credential_visibility_rehearsal = _credential_visibility_rehearsal()
    post_mutation_stability_audit = audit_post_mutation(ROOT)
    with tempfile.TemporaryDirectory(prefix="medzen-b6-cold-") as temporary:
        root = Path(temporary)
        scenarios = [_scenario(root, "full-pass", None)]
        scenarios.extend(
            _scenario(root, f"fail-{index:02d}-{stage}", stage)
            for index, stage in enumerate(WINDOW_STAGES, start=1)
        )
        stage_a_scenarios = [_stage_a_scenario(root, "stage-a-full-pass", None)]
        stage_a_scenarios.extend(
            _stage_a_scenario(root, f"stage-a-fail-{index:02d}-{stage}", stage)
            for index, stage in enumerate(
                (*STAGE_A_EXECUTION_STAGES, "stage_a_cleanup"), start=1
            )
        )
    results_sha256 = hashlib.sha256(
        canonical_json(
            {
                "window": scenarios,
                "stage_a": stage_a_scenarios,
                "new_gates": new_gate_rehearsal,
                "proof_diagnostics": proof_diagnostic_rehearsal,
                "pre_deadline_cleanup": pre_deadline_cleanup_rehearsal,
                "credential_visibility": credential_visibility_rehearsal,
                "post_mutation_stability": post_mutation_stability_audit,
            }
        )
    ).hexdigest()
    source_hashes = {relative: sha256_file(ROOT / relative) for relative in RUNNER_SOURCES}
    payload = {
        "review": "B6-WINDOW-DESIGN-REVIEW-2026-001",
        "status": "PASS_COLD_REHEARSAL",
        "full_pass_runs": 1,
        "injected_failure_runs": len(WINDOW_STAGES)
        + len(new_gate_rehearsal["injected_failures"])
        + proof_diagnostic_rehearsal["injected_assertion_failures"]
        + len(pre_deadline_cleanup_rehearsal["injected_paths"]),
        "stage_injected_failure_runs": len(WINDOW_STAGES),
        "new_gate_injected_failure_runs": len(
            new_gate_rehearsal["injected_failures"]
        ),
        "proof_diagnostic_injected_failure_runs": proof_diagnostic_rehearsal[
            "injected_assertion_failures"
        ],
        "pre_deadline_cleanup_injected_failure_runs": len(
            pre_deadline_cleanup_rehearsal["injected_paths"]
        ),
        "credential_visibility_transient_injection_runs": 1,
        "enumerated_stages": list(WINDOW_STAGES),
        "runner_source_hashes": source_hashes,
        "scenario_results_sha256": results_sha256,
        "scenarios": scenarios,
        "stage_a_full_pass_runs": 1,
        "stage_a_injected_failure_runs": len(stage_a_scenarios) - 1,
        "stage_a_scenarios": stage_a_scenarios,
        "new_gate_rehearsal": new_gate_rehearsal,
        "proof_diagnostic_rehearsal": proof_diagnostic_rehearsal,
        "pre_deadline_cleanup_rehearsal": pre_deadline_cleanup_rehearsal,
        "credential_visibility_rehearsal": credential_visibility_rehearsal,
        "post_mutation_stability_audit": post_mutation_stability_audit,
        "empirical_connectivity_gate": aws_read_fixture_fidelity[
            "network_reduction"
        ],
        "terraform_description_charset_lint": terraform_description_charset_lint,
        "aws_read_fixture_fidelity": aws_read_fixture_fidelity,
        "real_aws_calls": 0,
        "real_kubectl_calls": 0,
        "aws_mutations": 0,
        "kubernetes_mutations": 0,
    }
    store = ReceiptStore(output_dir)
    return store.persist("cold_rehearsal", "PASS", payload)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run(args.output_dir.resolve())
    except Exception as exc:
        print(json.dumps({"status": "REFUSED", "reason_code": type(exc).__name__}))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
