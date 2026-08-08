from __future__ import annotations

import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKET = (
    ROOT
    / "platform/decisions/B6A-AWS-CHANGE-PACKET-2026-003C-D-stage-receipts-and-ssm-sampler.md"
)
EVIDENCE = ROOT / "platform/evidence/B6A-LOCAL-ENGINEERING-2026-006.json"


def test_packet_requires_independent_iam_review_before_owner_approval():
    text = PACKET.read_text()
    assert "Status: **BLOCKED — INDEPENDENT IAM REVIEW AND OWNER APPROVAL REQUIRED**" in text
    assert "Approve B6A AWS change packet 2026-003C-D only." in text
    review = text.index("Independent IAM review must be recorded first")
    approval = text.index("Approve B6A AWS change packet 2026-003C-D only.")
    assert review < approval
    assert "implementer or owner approval alone is not the independent review" in text


def test_packet_binds_local_evidence_and_exact_one_create_plan():
    text = PACKET.read_text()
    assert hashlib.sha256(EVIDENCE.read_bytes()).hexdigest() in text
    assert "**1 create, 0 update, 0 delete**" in text
    assert "aws_iam_role_policy.node_ssm_core" in text
    assert "Parameter Store reads" in text
    assert "CPU and GPU nodes gain this no-ingress management channel" in text


def test_packet_orders_self_test_transcription_receipt_then_memory():
    text = PACKET.read_text()
    self_test = text.index("Persist `sampler_self_test` immediately")
    transcript = text.index("Persist and fsync the `transcription: PASS` receipt immediately")
    memory = text.index("Only after that receipt exists, start the already self-tested sampler")
    assert self_test < transcript < memory
    assert "120 numeric samples" in text
    assert "AWS-RunShellScript` **version `1`**" in text
    assert "INCOMPLETE_MEASUREMENT" in text
    assert "preserve transcription `PASS`" in text


def test_packet_preserves_budget_zero_cleanup_and_no_promotion():
    text = PACKET.read_text()
    assert "Confirmed remaining allowance: `6,520` seconds" in text
    assert "Conservative cumulative maximum: `7,200` seconds" in text
    assert "003C-D maximum GPU estimate: `$1.8227`" in text
    assert "GPU desired is zero before and after" in text
    assert "B5 remains `BLOCKED`" in text
    assert "approved-ASR" in text
    assert "production SSM/serving change" in text

