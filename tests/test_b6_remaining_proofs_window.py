from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
PACKET = ROOT / "platform/decisions/B6-AWS-CHANGE-PACKET-2026-032-remaining-proofs.md"
SCAN = ROOT / "platform/evidence/B6-PACKET-2026-031-SCAN-RESULT.json"
FILE_RECEIPT = ROOT / "platform/evidence/receipts/B6-2026-030A-A2-LIVE/file_proof.json"
NEW = "sha256:a3bd7170dbef4541ff6286324974a79d0b0da2287dcdcaf8f77a20654c7befed"
OLD = "sha256:fa2cccdf9891c080fcc1eb408a325e8afbd623e4f89469ea228ddf166dad62aa"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_packet_binds_scan_pass_file_pass_and_exact_allowance():
    packet = PACKET.read_text()
    assert _sha(SCAN) in packet
    assert _sha(FILE_RECEIPT) in packet
    assert NEW in packet
    assert "must not be rerun" in packet
    assert "two non-transferable" in packet
    assert "4,500-second attempts within the existing $10 reservation" in packet
    assert "New reservation | `$0.00`" in packet
    assert "A third attempt" in packet


def test_successor_stages_exclude_file_and_keep_only_remaining_live_proofs():
    from scripts.b6_remaining_runner import (
        REMAINING_EXECUTION_STAGES,
        REMAINING_WINDOW_STAGES,
    )

    assert "file_proof" not in REMAINING_EXECUTION_STAGES
    assert REMAINING_EXECUTION_STAGES[-4:] == (
        "websocket_proof",
        "cancellation_proof",
        "failure_drills",
        "isolation_proof",
    )
    assert REMAINING_WINDOW_STAGES[-1] == "cleanup"
    assert len(REMAINING_WINDOW_STAGES) == 22
    operations = (ROOT / "scripts/b6_remaining_operations.sh").read_text()
    assert "stage_file_proof" not in operations
    assert "file_proof)" not in operations


def test_all_successor_digest_projections_use_scan_passed_child():
    for relative in (
        "platform/k8s/b6-6/remaining-proofs-window.yaml",
        "scripts/b6_remaining_operations.sh",
        "scripts/b6_remaining_pre_endpoint_images.py",
    ):
        text = (ROOT / relative).read_text()
        assert NEW in text
        assert OLD not in text
    scan = json.loads(SCAN.read_bytes())
    assert scan["subject"]["child_manifest_digest"] == NEW
    assert scan["outcome"] == "PASS_SCAN_ONLY"


def test_manifest_is_digest_pinned_and_contains_exact_seven_workload_pods():
    path = ROOT / "platform/k8s/b6-6/remaining-proofs-window.yaml"
    documents = [item for item in yaml.safe_load_all(path.read_text()) if item]
    images = []
    for item in documents:
        template = item.get("spec", {}).get("template", {}).get("spec", {})
        images.extend(
            container.get("image", "")
            for container in (*template.get("initContainers", []), *template.get("containers", []))
        )
    assert images
    assert all("@sha256:" in image and ":PLACEHOLDER" not in image for image in images)
    assert any(image.endswith(NEW) for image in images)


def test_manifest_renderer_is_separate_from_historical_projection():
    from scripts.b6_remaining_manifest_slice import MANIFEST, render

    assert MANIFEST.name == "remaining-proofs-window.yaml"
    pre = list(yaml.safe_load_all(render("pre-endpoint")))
    ingress = list(yaml.safe_load_all(render("ingress")))
    assert all(item.get("kind") != "Ingress" for item in pre if item)
    assert [item.get("kind") for item in ingress if item] == ["Ingress"]


def test_cold_rehearsal_is_deterministic_and_never_creates_file_receipt(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    for output in (first, second):
        subprocess.run(
            [
                sys.executable,
                "scripts/b6_remaining_cold_rehearsal.py",
                "--output-dir",
                str(output),
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    one = json.loads((first / "cold_rehearsal.json").read_bytes())
    two = json.loads((second / "cold_rehearsal.json").read_bytes())
    assert one == two
    payload = one["payload"]
    assert payload["status"] == "PASS_COLD_REHEARSAL"
    assert payload["full_pass_runs"] == 1
    assert payload["injected_failure_runs"] == 22
    assert payload["file_proof_receipts_created"] == 0
    assert payload["preserved_proofs_not_executed"] == ["file_proof"]
    assert payload["real_aws_calls"] == 0
    assert payload["real_kubectl_calls"] == 0
    assert all(not (scenario_dir / "file_proof.json").exists() for scenario_dir in first.iterdir())


def test_attempt_parser_allows_only_one_or_two():
    script = ROOT / "scripts/b6_remaining_runner.py"
    common = [
        sys.executable,
        str(script),
        "--kubeconfig",
        "/tmp/missing-kubeconfig",
        "--authorization",
        "/tmp/missing-authorization",
        "--packet-sha256",
        "0" * 64,
        "--receipts-dir",
        "/tmp/missing-receipts",
        "--token-file",
        "/tmp/missing-token",
    ]
    result = subprocess.run([*common, "--attempt", "3"], capture_output=True, text=True)
    assert result.returncode != 0
    assert "invalid choice: '3'" in result.stderr


def test_stage0_proves_worker_deployment_and_alb_zero_state():
    operations = (ROOT / "scripts/b6_remaining_operations.sh").read_text()
    for binding in (
        "nodegroup.scalingConfig.desiredSize",
        "SYNTHETIC_DEPLOYMENT_COUNT_IS_ZERO",
        "LoadBalancerNotFound",
        "WINDOW_ALB_IS_ABSENT",
    ):
        assert binding in operations


def test_successor_stage0_safe_refusals_retain_exact_pre_model_detail(tmp_path):
    from scripts.b6_remaining_runner import safe_remaining_stage0_refusal

    for reason, code in (
        ("STAGE0_WORKER_CAPACITY_ZERO_REFUSED", 44),
        ("STAGE0_ALB_ABSENCE_REFUSED", 45),
        ("STAGE0_SYNTHETIC_DEPLOYMENT_ZERO_REFUSED", 46),
    ):
        path = tmp_path / f"{code}.json"
        path.write_text(
            json.dumps(
                {
                    "status": "REFUSED",
                    "reason_code": reason,
                    "failed_assertion": "EXACT_ZERO_STATE_ASSERTION",
                    "stage_exit_code": code,
                    "safe_error_text": "synthetic pre-model diagnostic",
                    "pre_model_and_audio": True,
                }
            )
        )
        assert safe_remaining_stage0_refusal(path, code) == {
            "reason_code": reason,
            "failed_assertion": "EXACT_ZERO_STATE_ASSERTION",
            "stage_exit_code": code,
            "safe_error_text": "synthetic pre-model diagnostic",
            "pre_model_and_audio": True,
        }
