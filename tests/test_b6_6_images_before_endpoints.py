from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_v2_cleanup_binds_refused_stage_without_requiring_it_to_pass(tmp_path: Path):
    from pipeline.b6_integration_receipts_v2 import ReceiptRefusal, ReceiptStore

    store = ReceiptStore(tmp_path)
    store.persist("local_bindings", "PASS", {"proven": True})
    refused = store.persist("deadline", "REFUSED", {"reason_code": "DEADLINE_REFUSED"})
    with pytest.raises(ReceiptRefusal, match="not PASS"):
        store.persist("workers_ready", "PASS", {"proven": True})
    cleanup = store.persist("cleanup", "PASS", {"zero": True})
    assert cleanup["dependencies"]["deadline"] == refused["receipt_sha256"]
    assert store.load("deadline")["status"] == "REFUSED"
    assert store.load("cleanup")["status"] == "PASS"


def test_same_stage_wrapper_persists_pass_and_refused_receipts(tmp_path: Path):
    script = f"""
set -euo pipefail
cd {ROOT}
source scripts/b6_6_stage_runtime.sh
export B6_STAGE_RECEIPTS_DIR={tmp_path}
export B6_ENDPOINTS_ENABLED=false
pass_stage() {{ b6_stage_payload '{{"proof":true}}'; }}
refuse_stage() {{ return 17; }}
b6_stage_execute local_bindings LOCAL_REFUSAL pass_stage
set +e
b6_stage_execute deadline STRUCTURAL_REFUSAL refuse_stage
status=$?
set -e
[[ "$status" == "17" ]]
"""
    subprocess.run(["bash", "-c", script], check=True, cwd=ROOT, capture_output=True)
    passed = json.loads((tmp_path / "local_bindings.json").read_bytes())
    refused = json.loads((tmp_path / "deadline.json").read_bytes())
    assert passed["status"] == "PASS"
    assert refused["status"] == "REFUSED"
    assert refused["payload"]["reason_code"] == "STRUCTURAL_REFUSAL"
    assert refused["payload"]["command_exit_code"] == 17


def _pod(namespace: str, app: str, digests: list[str], *, waiting: str | None = None) -> dict:
    name = f"{app}-abc"
    specs = [
        {"name": f"container-{index}", "image": f"example.invalid/{app}@{digest}"}
        for index, digest in enumerate(digests)
    ]
    statuses = [
        {
            "name": item["name"],
            "imageID": item["image"],
            "ready": waiting is None,
            "restartCount": 0,
            "state": {"waiting": {"reason": waiting}} if waiting else {"running": {}},
        }
        for item in specs
    ]
    return {
        "metadata": {
            "namespace": namespace,
            "name": name,
            "labels": {"app.kubernetes.io/name": app},
        },
        "spec": {"nodeName": f"node-{app}", "containers": specs},
        "status": {
            "phase": "Running" if waiting is None else "Pending",
            "conditions": [{"type": "Ready", "status": "True" if waiting is None else "False"}],
            "containerStatuses": statuses,
        },
    }


def _all_expected_pods() -> list[dict]:
    from scripts.b6_6_pre_endpoint_images import EXPECTED

    return [
        _pod(namespace, app, sorted(digests))
        for (namespace, app), digests in EXPECTED.items()
    ]


def test_pre_endpoint_proof_requires_every_exact_running_resident_image():
    from scripts.b6_6_pre_endpoint_images import (
        EXPECTED,
        ImageReadinessRefusal,
        verify_pre_endpoint,
    )

    pods = _all_expected_pods()
    result = verify_pre_endpoint(pods)
    assert result["status"] == "PASS"
    assert result["pod_count"] == result["application_count"] == len(EXPECTED) == 7
    assert result["private_ecr_endpoints_present"] is False
    assert result["all_pods_running_and_ready"] is True
    assert result["all_images_present_on_scheduled_nodes"] is True
    with pytest.raises(ImageReadinessRefusal):
        verify_pre_endpoint(pods[:-1])
    pods[0]["status"]["containerStatuses"][0]["imageID"] = "example.invalid/wrong@sha256:" + "0" * 64
    with pytest.raises(ImageReadinessRefusal):
        verify_pre_endpoint(pods)


def test_post_endpoint_new_pull_is_a_sanitized_fatal_classification():
    from scripts.b6_6_pre_endpoint_images import classify_post_endpoint_failure

    pods = _all_expected_pods()
    clean = classify_post_endpoint_failure(pods)
    assert clean["status"] == "NO_KUBERNETES_IMAGE_PULL_FAILURE_OBSERVED"
    pods[0] = _pod(
        pods[0]["metadata"]["namespace"],
        pods[0]["metadata"]["labels"]["app.kubernetes.io/name"],
        [pods[0]["spec"]["containers"][0]["image"].split("@", 1)[1]],
        waiting="ImagePullBackOff",
    )
    refused = classify_post_endpoint_failure(pods)
    assert refused["status"] == "REFUSED"
    assert refused["reason_code"] == "POST_ENDPOINT_NEW_KUBERNETES_IMAGE_PULL_FATAL"
    assert "message" not in json.dumps(refused).lower()
    unrelated = _pod(
        "kube-system",
        "unplanned-system-pod",
        ["sha256:" + "1" * 64],
        waiting="ErrImagePull",
    )
    unrelated["metadata"]["labels"] = {}
    refused = classify_post_endpoint_failure(_all_expected_pods() + [unrelated])
    assert refused["status"] == "REFUSED"
    assert refused["failures"][0]["application"] == "unlabeled"


def test_manifest_is_deterministically_split_before_endpoint_creation():
    import yaml
    from scripts.b6_6_manifest_slice import render

    pre = [item for item in yaml.safe_load_all(render("pre-endpoint")) if item]
    ingress = [item for item in yaml.safe_load_all(render("ingress")) if item]
    assert pre and all(item["kind"] != "Ingress" for item in pre)
    assert len(ingress) == 1
    assert ingress[0]["kind"] == "Ingress"
    assert ingress[0]["metadata"]["name"] == "speech-orchestrator-b6-window"


def test_split_plan_guards_require_one_controller_then_eleven_endpoint_resources(monkeypatch):
    from scripts import check_b6_6_images_before_endpoints_plan as guard

    controller = {
        "resource_changes": [{
            "address": guard.CONTROLLER,
            "change": {"actions": ["create"], "after": {
                "name": "aws-load-balancer-controller",
                "namespace": "kube-system",
                "chart": "aws-load-balancer-controller",
                "version": "3.5.0",
                "repository": "https://aws.github.io/eks-charts",
                "atomic": True,
                "wait": True,
                "wait_for_jobs": True,
            }},
        }],
    }
    guard.validate_controller(controller)
    controller["resource_changes"][0]["change"]["after"]["atomic"] = False
    with pytest.raises(ValueError, match="boundary differs"):
        guard.validate_controller(controller)

    endpoints = {
        "resource_changes": [
            {"address": address, "change": {"actions": ["create"], "after": {}}}
            for address in sorted(guard.ENDPOINT_ADDRESSES)
        ] + [{
            "address": guard.CONTROLLER,
            "change": {"actions": ["no-op"], "after": {}},
        }],
    }
    observed = {}
    monkeypatch.setattr(
        guard.successor,
        "validate_create",
        lambda plan: observed.update(changes=guard.proven.changes(plan)),
    )
    guard.validate_endpoints(endpoints)
    assert observed["changes"] == {
        address: ["create"] for address in guard.proven.ADDRESSES
    }


def test_runner_orders_all_kubernetes_images_before_endpoints_and_wraps_every_stage():
    runner = (ROOT / "scripts/b6_6_images_before_endpoints_window.sh").read_text()
    stages = [
        "local_bindings", "deadline", "workers_ready", "dra_ready", "rag_ready",
        "asr_ready", "tts_ready", "llm_ready", "orchestrator_ready",
        "controller_window", "controller_ready", "pre_endpoint_images",
        "terraform_window", "endpoints_ready", "fargate_probe", "alb_ready",
        "alb_tag_mutation_warning", "file_proof", "websocket_proof",
        "cancellation_proof", "failure_drills", "isolation_proof",
    ]
    positions = [runner.index(f"b6_stage_execute {stage} ") for stage in stages]
    assert positions == sorted(positions)
    assert runner.index("b6_stage_execute pre_endpoint_images ") < runner.index(
        "b6_stage_execute terraform_window "
    ) < runner.index("export B6_ENDPOINTS_ENABLED=true") < runner.index(
        "b6_stage_execute endpoints_ready "
    )
    assert "b6_6_receipt_v2.py" not in runner
    assert "scale deployment/rag-index --namespace medzen --replicas=0" not in runner
    assert "patch service/rag-index" in runner


def test_packet_017_and_v1_runtime_remain_immutable():
    expected = {
        "platform/decisions/B6-AWS-CHANGE-PACKET-2026-017-b6-6-principal-independent-successor.md": "8fa32f4013445fd18ad353119ddd10a1c5c199935059a63afedf951c61a045b6",
        "platform/decisions/B6-AWS-AUTH-2026-017-b6-6-principal-independent-successor.json": "9450d516fa57564dcaacf76bbe79f031fdf993cd3517db2e931d14df9191e54e",
        "platform/evidence/B6-PACKET-2026-017-REFUSED-DRA-ECR-ENDPOINT-DNS.json": "89331592c6ab848b94e297d2e5ae0e62b75c4a08db9cfb6ecd0ce5e34361c48a",
        "pipeline/b6_integration_receipts.py": "a5eb39b8b022021db63bb115ff905f5b229e96cd48b6d56da6919d952b19664e",
        "scripts/b6_6_successor_window.sh": "ccb03a030ff427683afafe8db3b33687570918a4be3f39ec5b47b484025900fc",
    }
    for relative, digest in expected.items():
        assert sha256(ROOT / relative) == digest


def test_new_credential_stage_advances_from_packet_017_fresh_version():
    from scripts import b6_6_images_before_endpoints_credential_stage as stage

    assert stage.expected_before(pending=True) == {
        "201f9790-72c4-45f7-a05b-967551532aef": ["AWSCURRENT"],
        "daacb67e-fcd1-41e1-bf62-47a3f18c8d0b": [],
        "d09d567e-9bde-482a-b95a-3cab990a1006": [],
        "f78c8aa8-2765-4788-9928-dd1ba7c406bf": [],
    }
    manifest = json.loads(
        (ROOT / "platform/manifests/B6-CLIENT-API-KEYS-RESTORE-2026-005.json").read_bytes()
    )
    assert manifest["required_starting_state"]["current_version_id"] == stage.CURRENT_VERSION
    assert len(manifest["required_starting_state"]["unstaged_version_ids"]) == 3


def test_packet_is_not_authorized_during_local_preparation():
    packet_path = (
        ROOT
        / "platform/decisions/B6-AWS-CHANGE-PACKET-2026-018-b6-6-images-before-endpoints.md"
    )
    packet = packet_path.read_text()
    assert "DRAFT — AWAITING INDEPENDENT REVIEW AND OWNER APPROVAL" in packet
    assert "6,852" in packet
    assert "exactly one full attempt" in packet
    assert "1 add / 0 change / 0 destroy" in packet
    assert "11 add / 0 change / 0 destroy" in packet
    assert "0 add / 0 change / 15 destroy" in packet
    assert "POST_ENDPOINT_NEW_KUBERNETES_IMAGE_PULL_FATAL" in packet
    assert "the same wrapper\npersists `PASS` and `REFUSED` receipts" in packet
    assert "Approve B6 AWS change packet 2026-018 only." in packet
    assert not (
        ROOT / "platform/decisions/B6-AWS-AUTH-2026-018-b6-6-images-before-endpoints.json"
    ).exists()


def test_packet_principal_source_table_is_hash_exact():
    packet = (
        ROOT
        / "platform/decisions/B6-AWS-CHANGE-PACKET-2026-018-b6-6-images-before-endpoints.md"
    ).read_text()
    bindings = [
        item for item in re.findall(r"\| `([^`]+)` \| `([0-9a-f]{64})` \|", packet)
        if not item[0].startswith("/")
    ]
    assert len(bindings) == 14
    for relative, digest in bindings:
        assert sha256(ROOT / relative) == digest


def test_local_preparation_evidence_is_packet_bound_and_non_authorizing():
    evidence = json.loads(
        (
            ROOT
            / "platform/evidence/B6-6-IMAGES-BEFORE-ENDPOINTS-LOCAL-PREPARATION-2026-001.json"
        ).read_bytes()
    )
    packet = ROOT / evidence["packet"]["path"]
    assert sha256(packet) == evidence["packet"]["sha256"]
    assert evidence["packet"]["authorized"] is False
    assert evidence["packet"]["executable"] is False
    assert evidence["implementation"]["receipt_protocol"] == "V2_STRUCTURAL_PASS_OR_REFUSED"
    assert evidence["implementation"]["remaining_seconds_before"] == 6852
    assert evidence["implementation"]["attempts_without_new_owner_allowance"] == 1
    assert evidence["non_events"]["aws_mutations"] == 0
    assert evidence["non_events"]["compute_started"] is False


def test_cleanup_refuses_structurally_and_uses_partial_guard_after_refused_create():
    cleanup = (
        ROOT / "scripts/b6_6_images_before_endpoints_cleanup.sh"
    ).read_text()
    assert '"$cleanup_receipt_stage" REFUSED' in cleanup
    assert '"$cleanup_receipt_stage" INCOMPLETE' not in cleanup
    assert "jq -e '.status == \"PASS\"'" in cleanup
