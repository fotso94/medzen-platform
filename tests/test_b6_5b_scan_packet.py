from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "platform/evidence/B6-5B-LOCAL-RELEASE-ENGINEERING-2026-001.json"
PACKET = ROOT / "platform/decisions/B6-AWS-CHANGE-PACKET-2026-003-b6-5b-ecr-scan-only.md"


def sha(relative: str) -> str:
    return hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()


def git_sha(commit: str, relative: str) -> str:
    content = subprocess.run(
        ["git", "show", f"{commit}:{relative}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    return hashlib.sha256(content).hexdigest()


def test_local_evidence_binds_four_unique_children_and_current_sources():
    evidence = json.loads(EVIDENCE.read_bytes())
    assert evidence["status"] == "VERIFIED_LOCAL_COMPLETE_ECR_SCAN_NOT_AUTHORIZED"
    assert evidence["source"]["git_commit"] == (
        "7ec176b2b69a3a552c6f135c36a8a1fc51cedc69"
    )
    assert evidence["tests"]["canonical_suite"] == {
        "passed": 1274,
        "failed": 0,
        "skipped": 0,
        "deselected": 7,
        "warnings": 1,
    }
    assert set(evidence["images"]) == {
        "medzen-rag-index",
        "medzen-llm-gateway",
        "medzen-orchestrator",
        "medzen-speech-tts-gateway",
    }
    children = set()
    dockerfiles = {
        "medzen-rag-index": "services/rag-index/Dockerfile",
        "medzen-llm-gateway": "services/llm-gateway/Dockerfile",
        "medzen-orchestrator": "services/speech-orchestrator/Dockerfile",
        "medzen-speech-tts-gateway": "services/speech-tts-gateway/Dockerfile",
    }
    for name, image in evidence["images"].items():
        assert image["critical_findings"] == image["high_findings"] == 0
        assert image["linux_amd64_child"].startswith("sha256:")
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", image["config_digest"])
        assert image["dockerfile_sha256"] == git_sha(
            evidence["source"]["git_commit"], dockerfiles[name]
        )
        assert image["committed_runtime_receipt_sha256"] == sha(
            image["committed_runtime_receipt_path"]
        )
        runtime_receipt = json.loads(
            (ROOT / image["committed_runtime_receipt_path"]).read_bytes()
        )
        assert runtime_receipt["status"] == "PASS"
        assert runtime_receipt["source_commit"] == evidence["source"]["git_commit"]
        assert runtime_receipt["runtime_smoke"]["uid"] == 10001
        assert runtime_receipt["runtime_smoke"]["gid"] == 10001
        assert runtime_receipt["runtime_smoke"]["package_manager_records"] == 0
        assert runtime_receipt["runtime_smoke"]["language_installers"] == 0
        assert image["committed_local_scout_sarif_sha256"] == sha(
            image["committed_local_scout_sarif_path"]
        )
        sarif = json.loads(
            (ROOT / image["committed_local_scout_sarif_path"]).read_bytes()
        )
        assert sarif["runs"]
        assert all(run.get("results", []) == [] for run in sarif["runs"])
        children.add(image["linux_amd64_child"])
    assert len(children) == 4
    assert evidence["registry"]["generated_sha256"] == sha(
        "platform/generated/registry-ssm/b6-v0-synthetic.json"
    )
    assert evidence["network_and_cost"]["network_design_sha256"] == sha(
        "platform/designs/B6-5B-ORCHESTRATOR-ALB-BOUNDARY-2026-001.json"
    )
    assert evidence["network_and_cost"]["cost_registry_sha256"] == sha(
        "platform/finance/COST-REGISTRY-2026-002.json"
    )
    assert all(value == 0 for key, value in evidence["aws_boundary"].items()
               if key != "read_only_discovery_performed")


def test_packet_is_scan_only_blocked_and_binds_every_local_child():
    packet = PACKET.read_text()
    evidence = json.loads(EVIDENCE.read_bytes())
    assert "Status: **BLOCKED — NOT AUTHORIZED**" in packet
    assert "Approve B6 AWS change packet 2026-003 only." in packet
    assert "Only `PASS_SCAN_ONLY` permits" in packet
    assert "does not authorize SSM publication or deployment" in packet
    for image in evidence["images"].values():
        assert image["linux_amd64_child"] in packet
        assert image["config_digest"] in packet
    assert "Manual `ecr:StartImageScan`" in packet
    assert "IAM, KMS, S3, SSM, EKS, Kubernetes, ALB or security-group mutation" in packet


def test_packet_expands_only_the_exact_scan_rule_and_has_a_bounded_reservation():
    packet = PACKET.read_text()
    repositories = {
        "medzen-model-loader",
        "medzen-nvidia-dra",
        "medzen-asr-runtime",
        "medzen-rag-index",
        "medzen-llm-gateway",
        "medzen-orchestrator",
        "medzen-speech-tts-gateway",
    }
    for repository in repositories:
        assert f"`{repository}`" in packet
    assert "one `BASIC` / `SCAN_ON_PUSH` rule with exactly seven" in packet
    assert "maximum `$1.00` allocation" in packet
    assert "Current active reservations before approval: `$0`" in packet
    assert "GPU and CPU desired capacity remain zero" in packet
