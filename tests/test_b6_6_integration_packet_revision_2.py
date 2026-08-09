from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKET = ROOT / (
    "platform/decisions/"
    "B6-AWS-CHANGE-PACKET-2026-004-b6-6-integration-window-revision-2.md"
)
SCAN = ROOT / "platform/evidence/B6-5B-ECR-SCAN-RESULT-2026-001.json"


def sha(relative: str) -> str:
    return hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()


def test_revision_two_is_explicitly_non_executable_and_preserves_history():
    packet = PACKET.read_text()
    assert "Status: **DRAFT BLOCKED — NOT APPROVABLE OR EXECUTABLE**" in packet
    assert "**Do not approve or execute this revision.**" in packet
    assert "It authorizes no AWS or\nKubernetes action" in packet
    assert sha("platform/decisions/B6-AWS-CHANGE-PACKET-2026-002-b6-6-integration-window.md") in packet
    assert "production serving pointer | absent" in packet.lower()
    assert "B5 outcome | `BLOCKED`, unchanged" in packet


def test_revision_two_binds_every_scan_passed_application_child():
    packet = PACKET.read_text()
    scan = json.loads(SCAN.read_bytes())
    for repository, subject in scan["automatic_scan_subjects"].items():
        assert f"`{repository}`" in packet
        assert subject["ecr_child_digest"] in packet
    retained = {
        "medzen-nvidia-dra": "sha256:7fb313758a20a04e80a53d5f6d1efe8e6fe936bc845001c21204c1c361d59246",
        "medzen-model-loader": "sha256:cb794f2169dc65f391a0c9ec789997ce19a31b38d9087f263fba0863ba0414a5",
        "medzen-asr-runtime": "sha256:434ac9e757b56949324e9e480490042fcbf35285f27bdee713bd771b502f4087",
    }
    for name, digest in retained.items():
        assert f"`{name}`" in packet
        assert digest in packet
    assert len(set(re.findall(r"sha256:[0-9a-f]{64}", packet))) >= 7


def test_revision_two_contains_the_required_window_and_cleanup_controls():
    packet = PACKET.read_text()
    assert "Maximum wall-clock window: `4 hours`" in packet
    assert "Intended all-in ceiling: `$10.00`" in packet
    assert "two `m6i.large` CPU and one `g6.xlarge` GPU" in packet
    assert "CPU minimum `0`, desired `0`, instances `0`, nodes `0`" in packet
    assert "GPU minimum `0`, desired `0`, instances `0`, nodes `0`" in packet
    assert "partial queue `4`, audio queue `8`" in packet
    assert "within `250 ms`" in packet
    assert "internal ALB" in packet
    assert "RAG, ASR, LLM and TTS remain `ClusterIP`" in packet


def test_revision_two_fails_closed_on_the_three_discovered_boundaries():
    packet = PACKET.read_text()
    assert "no AWS Load Balancer Controller is modeled or installed" in packet
    assert "The root is currently absent and therefore blocks this packet" in packet
    assert "requires secret\n`medzen/client-api-keys`, but that secret does not exist" in packet
    assert "no waiver" in packet.lower()
