from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PACKET = (
    ROOT
    / "platform/decisions/B6-AWS-CHANGE-PACKET-2026-016-b6-6-final-window.md"
)
EVIDENCE = (
    ROOT
    / "platform/evidence/B6-CLIENT-SECRET-RESTORATION-AWS-EXECUTION-2026-002.json"
)
RECEIPTS = ROOT / "platform/evidence/receipts/B6-2026-015-LIVE"


def test_restoration_evidence_binds_every_receipt_and_contains_no_plaintext():
    from scripts.b6_6_bindings import CREDENTIAL_EVIDENCE_SHA256

    evidence = json.loads(EVIDENCE.read_bytes())
    assert hashlib.sha256(EVIDENCE.read_bytes()).hexdigest() == CREDENTIAL_EVIDENCE_SHA256
    assert evidence["status"] == "VERIFIED_COMPLETE"
    assert evidence["execution"]["restore_secret_calls"] == 1
    assert evidence["execution"]["new_secret_arns"] == 0
    assert evidence["execution"]["old_plaintext_reads"] == 0
    assert evidence["credential_binding"]["plaintext_recorded"] is False
    assert evidence["credential_binding"]["old_plaintext_read_or_reused"] is False
    assert evidence["final_window_boundary"]["window_authorized_by_this_record"] is False
    for stage in evidence["stages"]:
        receipt = RECEIPTS / f"{stage['stage']}.json"
        assert hashlib.sha256(receipt.read_bytes()).hexdigest() == stage["receipt_sha256"]
        receipt_value = json.loads(receipt.read_bytes())
        assert receipt_value.get("plaintext_recorded", receipt_value.get("plaintext_read")) is False


def test_fresh_version_and_token_hash_are_the_only_executable_bindings():
    from scripts.b6_6_bindings import EXPECTED_CREDENTIAL_BINDING
    from scripts.b6_6_secret_preflight import NEW_VERSION, PRIOR_CURRENT_VERSION
    from scripts.b6_6_token_binding import BEARER_SHA256
    from scripts.run_b6_client_secret_restoration import OLD_VERSION

    evidence = json.loads(EVIDENCE.read_bytes())["credential_binding"]
    assert NEW_VERSION == evidence["new_version_id"]
    assert PRIOR_CURRENT_VERSION == evidence["prior_current_version_id"]
    assert OLD_VERSION == evidence["older_version_id"]
    assert BEARER_SHA256 == evidence["bearer_token_sha256"]
    assert EXPECTED_CREDENTIAL_BINDING["secret_value_sha256"] == evidence["secret_value_sha256"]
    assert evidence["new_version_stages"] == ["AWSCURRENT"]
    assert evidence["prior_current_version_stages"] == []
    assert evidence["older_version_stages"] == []


def test_final_runner_is_packet_016_only_and_deadline_first():
    runner = (ROOT / "scripts/run_b6_6_integration_window.sh").read_text()
    cleanup = (ROOT / "scripts/b6_6_cleanup.sh").read_text()
    assert "packet 2026-016" in runner
    assert "B6-2026-016-LIVE" in runner
    assert "execution requires a clean reviewed worktree" in runner
    assert "/private/tmp/b6-016-create-" in runner
    assert "/private/tmp/b6-016-cleanup-" in cleanup
    assert runner.index("b6_6_deadline.py arm") < runner.index(
        "update-nodegroup-config --cluster-name medzen-speech --nodegroup-name cpu"
    )
    assert runner.index("b6_6_probe_endpoints.py available") < runner.index(
        "rollout status deployment/aws-load-balancer-controller"
    )
    assert runner.index("b6_6_probe_endpoints.py available") < runner.index(
        "b6_6_fargate_probe.py"
    )


def test_packet_014_remains_immutable_and_unexecuted():
    historical = (
        ROOT
        / "platform/decisions/B6-AWS-CHANGE-PACKET-2026-014-b6-6-private-probe-successor.md"
    )
    assert hashlib.sha256(historical.read_bytes()).hexdigest() == (
        "f31cb8f36d76d32884639bbe8bfb750ca807a92847d24f0abf4e1eef7d8c6428"
    )
    evidence = json.loads(EVIDENCE.read_bytes())
    assert evidence["final_window_boundary"]["packet_2026_014_executed"] is False


def test_final_packet_is_hash_bound_and_still_non_authorizing():
    from scripts.b6_6_bindings import REQUIRED_SOURCES

    packet = PACKET.read_text()
    assert "DRAFT — AWAITING INDEPENDENT REVIEW AND OWNER APPROVAL" in packet
    assert "This draft itself authorizes no AWS or Kubernetes mutation" in packet
    assert "exactly `12 add / 0 change / 0 destroy`" in packet
    assert "exactly `0 add / 0 change / 15 destroy`" in packet
    assert "Maximum final-window deadline: `9,581 seconds`" in packet
    assert "daacb67e-fcd1-41e1-bf62-47a3f18c8d0b" in packet
    assert "77f2979e024c42e91db938fecdb6214359637b316ad5edf6bbf1008fe59a89ea" in packet
    assert "Approve B6 AWS change packet 2026-016 only." in packet
    for relative in REQUIRED_SOURCES:
        digest = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        assert f"| `{relative}` | `{digest}` |" in packet


def test_local_preparation_evidence_is_packet_bound_and_non_authorizing():
    preparation = json.loads(
        (
            ROOT
            / "platform/evidence/B6-6-FINAL-WINDOW-LOCAL-PREPARATION-2026-001.json"
        ).read_bytes()
    )
    assert preparation["packet"]["authorized"] is False
    assert preparation["packet"]["executed"] is False
    assert preparation["packet"]["owner_authorization_record_created"] is False
    assert hashlib.sha256(PACKET.read_bytes()).hexdigest() == preparation["packet"]["sha256"]
    assert preparation["verification"]["focused"]["passed"] == 63
    assert preparation["verification"]["canonical"]["passed"] == 1386
    assert all(
        value == 0
        for value in preparation[
            "explicit_non_events_during_packet_2026_016_preparation"
        ].values()
    )
