from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError

import scripts.b6_6_k8s_stability as k8s_stability
import scripts.b6_6_lbc_runtime as lbc_runtime
from scripts.b6_6_credential import (
    KMS_KEY,
    SECRET_ARN,
    SECRET_NAME,
    CredentialRefusal,
    rotate_and_verify,
)
from scripts.b6_6_k8s_stability import StabilityPending, observe_stably
from scripts.b6_6_post_mutation_audit import audit
from scripts.b6_6_pre_endpoint_images import EXPECTED, wait_pre_endpoint
from scripts.b6_6_wait_workers import WorkerReadinessRefusal, wait_for_workers


ROOT = Path(__file__).resolve().parents[1]


class Clock:
    def __init__(self) -> None:
        self.seconds = 0.0

    def monotonic(self) -> float:
        return self.seconds

    def sleep(self, seconds: float) -> None:
        self.seconds += seconds


class VisibilityClient:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses
        self.created_version: str | None = None
        self.put_calls = 0
        self.list_calls = 0

    def describe_secret(self, **_: Any) -> dict[str, Any]:
        return {"Name": SECRET_NAME, "ARN": SECRET_ARN, "KmsKeyId": KMS_KEY}

    def put_secret_value(self, **kwargs: Any) -> dict[str, Any]:
        self.put_calls += 1
        self.created_version = str(kwargs["ClientRequestToken"])
        return {"ARN": SECRET_ARN, "VersionId": self.created_version}

    def list_secret_version_ids(self, **_: Any) -> dict[str, Any]:
        self.list_calls += 1
        index = min(self.list_calls - 1, len(self.responses) - 1)
        value = json.loads(json.dumps(self.responses[index]))
        for item in value.get("Versions", []):
            if item.get("VersionId") == "__EXACT_CREATED_VERSION_ID__":
                item["VersionId"] = self.created_version
        return value

    def get_secret_value(self, **_: Any) -> dict[str, Any]:
        raise ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
            "GetSecretValue",
        )


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((ROOT / "tests/fixtures/aws" / name).read_bytes())


def test_rotation_polls_exact_version_through_stale_then_stable_current(
    tmp_path: Path,
) -> None:
    stale = _fixture("secretsmanager-list-secret-version-ids-stale-after-put.json")
    current = _fixture("secretsmanager-list-secret-version-ids-current-after-put.json")
    client = VisibilityClient([stale, stale, current, current, current])
    clock = Clock()
    result = rotate_and_verify(
        client,
        tmp_path / "token",
        material_factory=lambda size: bytes(range(size)),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )
    assert client.put_calls == 1
    assert client.list_calls == 5
    assert result["fresh_version_id"] == client.created_version
    assert result["stable_current_observations"] == 3
    assert result["visibility_polls"] == 5
    assert clock.seconds == 24


def test_other_current_version_never_satisfies_exact_version_poll(
    tmp_path: Path,
) -> None:
    stale = _fixture("secretsmanager-list-secret-version-ids-stale-after-put.json")
    client = VisibilityClient([stale])
    clock = Clock()
    with pytest.raises(CredentialRefusal, match="stably AWSCURRENT"):
        rotate_and_verify(
            client,
            tmp_path / "token",
            material_factory=lambda size: bytes(range(size)),
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
    assert client.put_calls == 1
    assert client.list_calls > 1
    assert clock.seconds == 120


def test_generic_stability_poll_resets_on_pending_and_shape_change() -> None:
    observations: list[dict[str, int] | Exception] = [
        StabilityPending("not ready"),
        {"value": 1},
        {"value": 1},
        {"value": 2},
        {"value": 2},
        {"value": 2},
    ]
    clock = Clock()

    def observe() -> dict[str, int]:
        value = observations.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    result = observe_stably(
        observe,
        60,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )
    assert result == {
        "value": 2,
        "stable_observations": 3,
        "verification_polls": 6,
        "poll_interval_seconds": 5,
    }


def test_worker_gate_resets_after_transient_read_and_requires_three_sets() -> None:
    calls = 0

    def snapshot(workload: str) -> list[dict[str, Any]]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise WorkerReadinessRefusal("KUBECTL_NODE_READ_FAILED")
        count = 2 if workload == "cpu" else 1
        return [
            {"status": {"conditions": [{"type": "Ready", "status": "True"}]}}
            for _ in range(count)
        ]

    clock = Clock()
    result = wait_for_workers(
        snapshot,
        60,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )
    assert result["stable_observations"] == 3
    assert calls == 7


def _resident_pods() -> list[dict[str, Any]]:
    result = []
    for index, ((namespace, application), digests) in enumerate(sorted(EXPECTED.items())):
        init_digests = sorted(digests)[:-1]
        app_digest = sorted(digests)[-1]
        result.append(
            {
                "metadata": {
                    "name": f"{application}-{index}",
                    "namespace": namespace,
                    "labels": {"app.kubernetes.io/name": application},
                },
                "spec": {
                    "nodeName": f"node-{index}",
                    "initContainers": [
                        {"name": f"init-{item}", "image": digest}
                        for item, digest in enumerate(init_digests)
                    ],
                    "containers": [{"name": "app", "image": app_digest}],
                },
                "status": {
                    "phase": "Running",
                    "conditions": [{"type": "Ready", "status": "True"}],
                    "initContainerStatuses": [
                        {
                            "name": f"init-{item}",
                            "imageID": digest,
                            "state": {"terminated": {"exitCode": 0}},
                        }
                        for item, digest in enumerate(init_digests)
                    ],
                    "containerStatuses": [
                        {
                            "name": "app",
                            "imageID": app_digest,
                            "ready": True,
                            "state": {"running": {}},
                        }
                    ],
                },
            }
        )
    return result


def test_full_pre_endpoint_image_proof_requires_three_stable_reads() -> None:
    calls = 0

    def runner(*_: Any, **__: Any) -> Any:
        nonlocal calls
        calls += 1
        return type("Completed", (), {"stdout": json.dumps({"items": _resident_pods()})})()

    clock = Clock()
    result = wait_pre_endpoint(
        Path("/synthetic/kubeconfig"),
        60,
        runner=runner,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )
    assert calls == 3
    assert result["stable_observations"] == 3
    assert result["pod_count"] == 7


def test_isolation_proof_requires_three_stable_read_pairs(monkeypatch: Any) -> None:
    calls = 0

    def fake_kubectl(_: Path, arguments: list[str]) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if "services" in arguments:
            return {
                "items": [
                    {"metadata": {"name": name}, "spec": {"type": "ClusterIP"}}
                    for name in sorted(k8s_stability.WINDOW_SERVICES)
                ]
            }
        return {"items": [{"metadata": {"name": k8s_stability.WINDOW_INGRESS}}]}

    monkeypatch.setattr(k8s_stability, "kubectl_json", fake_kubectl)
    clock = Clock()
    args = type(
        "Args",
        (),
        {"kubeconfig": Path("/synthetic/kubeconfig"), "wait_seconds": 60},
    )()
    result = k8s_stability.wait_isolation(args)
    assert calls == 6
    assert result["stable_observations"] == 3
    assert result["orchestrator_ingresses"] == 1


def test_tag_classification_requires_three_stable_log_reads(monkeypatch: Any) -> None:
    calls = 0

    def logs(_: Path, __: str) -> str:
        nonlocal calls
        calls += 1
        return ""

    monkeypatch.setattr(lbc_runtime, "_controller_logs", logs)
    clock = Clock()
    receipt_hash = "a" * 64
    fargate_hash = "b" * 64
    resources = [
        "arn:aws:elasticloadbalancing:eu-central-1:558069890522:listener/app/medzen-b6-window/abc/def",
        *[
            f"arn:aws:elasticloadbalancing:eu-central-1:558069890522:listener-rule/app/medzen-b6-window/abc/def/{suffix}"
            for suffix in ("111", "222", "333")
        ],
    ]
    receipt = {
        "stage": "alb_ready",
        "status": "PASS",
        "dependencies": {"endpoints_ready": "c" * 64},
        "payload": {
            "internal_alb": True,
            "alb_security_group": lbc_runtime.ALB_SECURITY_GROUP,
            "listener_port": 80,
            "route_count": 3,
            "target_healthy": True,
            "creation_time_exact_tags": True,
            "tagged_resource_count": 5,
            "tag_mutation_resource_arns": resources,
        },
    }
    fargate = {
        "stage": "fargate_probe",
        "status": "PASS",
        "dependencies": {"alb_ready": receipt_hash},
    }
    result = lbc_runtime.wait_for_stable_tag_classification(
        kubeconfig=Path("/synthetic/kubeconfig"),
        since_time="2026-08-11T00:00:00Z",
        receipt=receipt,
        receipt_sha256=receipt_hash,
        fargate_receipt=fargate,
        fargate_receipt_sha256=fargate_hash,
        wait_seconds=30,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )
    assert calls == 3
    assert result["stable_tag_classification_observations"] == 3
    assert result["status"] == "PASS_NO_TAG_MUTATION_DENIAL"


def test_whole_runner_post_mutation_audit_has_no_one_shot_verifier() -> None:
    result = audit(ROOT)
    evidence = json.loads(
        (
            ROOT
            / "platform/evidence/B6-POST-MUTATION-VERIFIER-AUDIT-2026-001.json"
        ).read_bytes()
    )
    assert evidence == result
    assert result["status"] == "PASS"
    assert result["post_mutation_paths"] == 31
    assert result["corrected_paths"] == 30
    assert result["preexisting_compliant_paths"] == 1
    assert result["one_shot_paths_remaining"] == 0
    assert result["minimum_stable_observations"] == 2
    assert result["deviations"] == []
    assert len(result["controls"]) == len(
        {item["id"] for item in result["controls"]}
    )


def test_visibility_fixtures_are_bound_to_terminal_refusal_class() -> None:
    terminal = json.loads(
        (
            ROOT
            / "platform/evidence/B6-PACKET-2026-028-TERMINAL-STAGE0-CREDENTIAL-CONSISTENCY-REFUSALS.json"
        ).read_bytes()
    )
    stale = _fixture("secretsmanager-list-secret-version-ids-stale-after-put.json")
    current = _fixture("secretsmanager-list-secret-version-ids-current-after-put.json")
    provenance = json.loads(
        (
            ROOT
            / "platform/evidence/B6-SECRETSMANAGER-VISIBILITY-FIXTURE-PROVENANCE-2026-001.json"
        ).read_bytes()
    )
    assert terminal["diagnosis"]["classification"] == (
        "REPRODUCED_SECRETS_MANAGER_READ_AFTER_WRITE_VISIBILITY_GAP"
    )
    assert sum(
        "AWSCURRENT" in item["VersionStages"] for item in stale["Versions"]
    ) == 1
    assert any(
        item["VersionId"] == "__EXACT_CREATED_VERSION_ID__"
        and item["VersionStages"] == ["AWSCURRENT"]
        for item in current["Versions"]
    )
    bound = {
        item["path"]: item["sha256"]
        for item in provenance["regression_projections"]
    }
    for name in (
        "secretsmanager-list-secret-version-ids-stale-after-put.json",
        "secretsmanager-list-secret-version-ids-current-after-put.json",
    ):
        relative = f"tests/fixtures/aws/{name}"
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == bound[relative]
    assert provenance["boundaries"]["real_aws_calls"] == 0
    assert provenance["boundaries"]["additional_credentials_after_stale_read"] == 0
