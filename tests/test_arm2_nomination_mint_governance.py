"""Arm-2 nomination-mint GOVERNANCE invariants (Codex round 36 A8/A11/A12/A13/
A14/A15). Static assertions over the committed trust documents — the IAM trust
policies (rendered from terraform), the two role permission policies, the packet,
the workflows and CODEOWNERS — because an offline cross-role STS 'simulation'
proves nothing (A12); the trust DOCUMENTS are the authority.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_WF = _REPO / ".github/workflows"
_TF = (_REPO / "infra/arm2_nomination_mint_role.tf").read_text()
_PACKET = json.loads((_REPO / "platform/decisions/"
                      "B5-UNIVERSAL-ARM2-NOMINATION-LIVE-MINT-PACKET-2026-001.json"
                      ).read_bytes())

_PRODUCER_ENV = "arm2-nomination-mint-producer"
_MINT_ENV = "arm2-nomination-mint-mint"
_PRODUCER_EXEC = "arm2-nomination-mint-producer-exec.yml"
_MINT_EXEC = "arm2-nomination-mint-mint-exec.yml"


# --------------------------------------------------------------------------
# A8/A12 — the two OIDC trusts differ in TWO claims (env AND job_workflow_ref)
# --------------------------------------------------------------------------

def _trust_block(role_marker: str) -> str:
    # slice the tf around each trust policy document by its sub environment
    idx = _TF.index(role_marker)
    return _TF[idx - 1200:idx + 400]


def test_producer_and_mint_trust_distinct_environments():
    assert f"environment:{_PRODUCER_ENV}" in _TF
    assert f"environment:{_MINT_ENV}" in _TF
    # the two environments are distinct strings
    assert _PRODUCER_ENV != _MINT_ENV


def test_producer_and_mint_trust_distinct_job_workflow_refs():
    prod_ref = f"{_PRODUCER_EXEC}@refs/heads/master"
    mint_ref = f"{_MINT_EXEC}@refs/heads/master"
    assert prod_ref in _TF and mint_ref in _TF
    assert prod_ref != mint_ref
    # the mint trust block names the MINT ref and NOT the producer ref
    mint_block = _trust_block(f"environment:{_MINT_ENV}")
    assert _MINT_EXEC in mint_block and _PRODUCER_EXEC not in mint_block
    prod_block = _trust_block(f"environment:{_PRODUCER_ENV}")
    assert _PRODUCER_EXEC in prod_block and _MINT_EXEC not in prod_block


def test_every_trust_pins_aud_and_has_no_wildcards():
    assert _TF.count('variable = "token.actions.githubusercontent.com:aud"') == 2
    assert _TF.count('values   = ["sts.amazonaws.com"]') == 2
    # no StringLike / wildcard over sub or job_workflow_ref
    assert "StringLike" not in _TF
    assert "*" not in re.sub(r"#.*", "", _TF)   # no wildcard outside comments


def test_exec_filenames_match_the_refs_their_roles_trust():
    # a rename would break the trust silently in prod; fail here instead
    assert (_WF / _PRODUCER_EXEC).exists()
    assert (_WF / _MINT_EXEC).exists()


# --------------------------------------------------------------------------
# A8 — both new environments are verified by the preflight, gate unchanged
# --------------------------------------------------------------------------

def test_known_environments_include_both_and_required_unchanged():
    import sys
    sys.path.insert(0, str(_REPO / "scripts"))
    from verify_protected_environments import (KNOWN_ENVIRONMENTS,
                                               REQUIRED_ENVIRONMENTS)
    assert REQUIRED_ENVIRONMENTS == ("trainer-image-publish", "arm2-calibration")
    assert _PRODUCER_ENV in KNOWN_ENVIRONMENTS
    assert _MINT_ENV in KNOWN_ENVIRONMENTS


def test_caller_preflight_verifies_both_environments():
    caller = (_WF / "arm2-nomination-mint.yml").read_text()
    assert f"arm2-nomination-mint-producer=" in caller
    assert f"arm2-nomination-mint-mint=" in caller
    assert "verify_protected_environments.py --only-supplied" in caller


# --------------------------------------------------------------------------
# permissions follow the repo convention (test_b7_pipelines): OIDC workflows
# carry EXACTLY top-level {contents:read, id-token:write} and no undocumented
# job-level additions. Role ISOLATION comes from the OIDC TRUST (distinct env +
# job_workflow_ref), not from token-grant scope — a token minted in one exec
# carries that exec's job_workflow_ref and can assume ONLY its own role.
# --------------------------------------------------------------------------

def test_all_three_workflows_are_minimal_oidc_permissions():
    import yaml
    for name in ("arm2-nomination-mint.yml", _PRODUCER_EXEC, _MINT_EXEC):
        doc = yaml.safe_load((_WF / name).read_text())
        assert doc.get("permissions") == {"contents": "read", "id-token": "write"}
        for job in doc.get("jobs", {}).values():
            assert not (isinstance(job, dict) and job.get("permissions")),                 f"{name}: no undocumented job-level permissions"


# --------------------------------------------------------------------------
# A13 — same-run handoff: producer exports the sha, mint binds it
# --------------------------------------------------------------------------

def test_producer_exports_artifact_sha_and_mint_binds_it():
    prod = (_WF / _PRODUCER_EXEC).read_text()
    mint = (_WF / _MINT_EXEC).read_text()
    caller = (_WF / "arm2-nomination-mint.yml").read_text()
    assert "artifact_sha256:" in prod and "GITHUB_OUTPUT" in prod
    assert "producer_artifact_sha256" in mint
    assert "!= \"$WANT\"" in mint or "!= \"${WANT}\"" in mint
    # the caller wires the producer output into the mint input, mint needs produce
    assert "needs.produce.outputs.artifact_sha256" in caller
    assert "needs: produce" in caller
    # id-token lives ONLY at the top level (repo convention), not per job
    assert caller.count("id-token: write") == 1


def test_mint_downloads_same_run_artifact_no_cross_run():
    mint = (_WF / _MINT_EXEC).read_text()
    assert "download-artifact" in mint
    assert "run-id" not in mint and "github-token" not in mint


def test_execs_bind_runnable_code_to_master_head():
    for exe in (_PRODUCER_EXEC, _MINT_EXEC):
        body = (_WF / exe).read_text()
        assert "origin/master HEAD" in body
        assert "git rev-parse origin/master" in body


# --------------------------------------------------------------------------
# A14 — neither role can read an eval/*-sealed (or any sealed/eval) object
# --------------------------------------------------------------------------

def _policy(name):
    return json.loads((_REPO / f"platform/iam/{name}").read_bytes())


def test_mint_role_reads_only_the_seven_pinned_versioned_objects():
    policy = _policy("medzen-arm2-nomination-mint-role.json")
    allows = [s for s in policy["Statement"] if s["Effect"] == "Allow"]
    version_reads = [s for s in allows if s["Action"] == ["s3:GetObjectVersion"]]
    assert len(version_reads) == 7
    for s in version_reads:
        assert "s3:VersionId" in json.dumps(s["Condition"])
        # never a sealed object
        assert "sealed" not in json.dumps(s["Resource"])
    # unversioned GetObject denied bucket-wide
    assert any(s["Effect"] == "Deny" and "s3:GetObject" in s["Action"]
               and s["Action"] == ["s3:GetObject"] for s in policy["Statement"])


def test_producer_role_denies_eval_and_sealed_reads_absolutely():
    policy = _policy("medzen-arm2-training-index-role.json")
    denies = [s for s in policy["Statement"] if s["Effect"] == "Deny"]
    assert any("eval/*" in json.dumps(s.get("Resource", "")) for s in denies)
    assert any("NotResource" in s and "curated/*" in json.dumps(s["NotResource"])
               for s in denies)
    allows = [s for s in policy["Statement"] if s["Effect"] == "Allow"]
    assert all("eval" not in json.dumps(s.get("Resource", "")) for s in allows)


def test_iam_policy_files_match_the_packet_mint_policy():
    committed = _policy("medzen-arm2-nomination-mint-role.json")
    assert committed == _PACKET["minimal_read_role"]["policy"]


# --------------------------------------------------------------------------
# A11 — CODEOWNERS protects the ledgers + decision records
# --------------------------------------------------------------------------

def test_codeowners_covers_the_ledgers_decisions_and_workflows():
    co = (_REPO / ".github/CODEOWNERS").read_text()
    assert "ARM2-TRAINING-INDEX-ADMISSION-LEDGER.jsonl" in co
    assert "ARM2-SEALED-EXCLUSION-LEDGER.jsonl" in co
    assert "/platform/decisions/" in co
    # impl red-team #8: the exec filenames are the job_workflow_ref in each
    # role's OIDC trust — they must be owner-reviewed too
    for wf in ("arm2-nomination-mint.yml", _PRODUCER_EXEC, _MINT_EXEC):
        assert wf in co, f"CODEOWNERS must cover {wf}"
    assert "@fotso94" in co


# --------------------------------------------------------------------------
# packet consistency (round 36 rev 004)
# --------------------------------------------------------------------------

def test_packet_pins_both_ledger_head_shas_matching_the_committed_files():
    import hashlib
    for field, path in (
            ("training_index_ledger_sha256",
             "platform/evidence/ARM2-TRAINING-INDEX-ADMISSION-LEDGER.jsonl"),
            ("sealed_exclusion_ledger_sha256",
             "platform/evidence/ARM2-SEALED-EXCLUSION-LEDGER.jsonl")):
        want = hashlib.sha256((_REPO / path).read_bytes()).hexdigest()
        assert _PACKET[field] == want


def test_packet_reads_no_sealed_object():
    classes = {o["class"] for o in _PACKET["pinned_objects"]}
    assert "SEALED" not in classes
    assert _PACKET["sealed_exclusion"]["mint_reads_sealed_bytes"] is False
    assert len(_PACKET["pinned_objects"]) == 7


def test_packet_names_both_roles_environments_and_execs():
    auth = _PACKET["authorization_mechanism"]
    assert auth["producer"]["environment"] == _PRODUCER_ENV
    assert auth["mint"]["environment"] == _MINT_ENV
    assert _PRODUCER_EXEC in auth["producer"]["exec_workflow"]
    assert _MINT_EXEC in auth["mint"]["exec_workflow"]


# --------------------------------------------------------------------------
# Codex round 37 #1 — branch-protection verifier + CODEOWNERS self-coverage
# --------------------------------------------------------------------------

def test_branch_protection_verifier_requires_codeowner_review():
    import sys
    sys.path.insert(0, str(_REPO / "scripts"))
    from verify_branch_protection import require_code_owner_review
    assert require_code_owner_review({"__missing__": True})
    assert require_code_owner_review({"message": "Branch not protected"})
    assert require_code_owner_review(
        {"required_pull_request_reviews": {"require_code_owner_reviews": False,
                                           "required_approving_review_count": 1}})
    assert require_code_owner_review(
        {"required_pull_request_reviews": {"require_code_owner_reviews": True,
                                           "required_approving_review_count": 0}})
    # a FULLY-hardened branch passes (Codex round 38 #4)
    strong = {"required_pull_request_reviews": {
                  "require_code_owner_reviews": True,
                  "required_approving_review_count": 1,
                  "dismiss_stale_reviews": True,
                  "require_last_push_approval": True},
              "enforce_admins": {"enabled": True},
              "allow_force_pushes": {"enabled": False},
              "allow_deletions": {"enabled": False}}
    assert require_code_owner_review(strong) == []


def test_branch_protection_verifier_rejects_weak_configs():
    import sys
    sys.path.insert(0, str(_REPO / "scripts"))
    from verify_branch_protection import require_code_owner_review
    base = {"required_pull_request_reviews": {
                "require_code_owner_reviews": True,
                "required_approving_review_count": 1,
                "dismiss_stale_reviews": True, "require_last_push_approval": True},
            "enforce_admins": {"enabled": True},
            "allow_force_pushes": {"enabled": False},
            "allow_deletions": {"enabled": False}}
    import copy
    for weaken in (
            lambda c: c["enforce_admins"].update(enabled=False),
            lambda c: c["allow_force_pushes"].update(enabled=True),
            lambda c: c["allow_deletions"].update(enabled=True),
            lambda c: c["required_pull_request_reviews"].update(dismiss_stale_reviews=False),
            lambda c: c["required_pull_request_reviews"].update(require_last_push_approval=False)):
        cfg = copy.deepcopy(base)
        weaken(cfg)
        assert require_code_owner_review(cfg), "weak protection must be rejected"


def test_codeowners_names_the_exact_owner_and_covers_dev_sentinels():
    import sys
    sys.path.insert(0, str(_REPO / "scripts"))
    from verify_branch_protection import codeowners_covers
    co = (_REPO / ".github/CODEOWNERS").read_text()
    assert "dev-sentinels" in co
    assert codeowners_covers(co) == []                      # exact owner @fotso94
    assert codeowners_covers(co, owner="@someone-else")     # wrong owner fails


def test_branch_protection_verifier_is_referenced_as_owner_receipt():
    # round 39 #2: the verifier is an OWNER-RUN receipt (referenced in the caller
    # note + the packet precondition), NOT an in-workflow API call
    caller = (_WF / "arm2-nomination-mint.yml").read_text()
    assert "verify_branch_protection.py" in caller
    assert "branches/master/protection" not in caller


def test_codeowners_covers_itself_and_every_trust_path():
    import sys
    sys.path.insert(0, str(_REPO / "scripts"))
    from verify_branch_protection import codeowners_covers, REQUIRED_CODEOWNER_PATHS
    co = (_REPO / ".github/CODEOWNERS").read_text()
    assert codeowners_covers(co) == []          # every required path is owned
    assert "/.github/CODEOWNERS" in REQUIRED_CODEOWNER_PATHS   # protects itself
    # a CODEOWNERS missing itself fails
    stripped = "\n".join(l for l in co.splitlines()
                         if "/.github/CODEOWNERS" not in l)
    assert any("CODEOWNERS" in f for f in codeowners_covers(stripped))


def test_packet_requires_review_record_and_branch_protection():
    assert _PACKET["independent_review_record"]["record_id"].endswith("REVIEW-2026-001")
    assert "trust_oid_binding" in _PACKET
    joined = " ".join(_PACKET["preconditions"]).lower()
    assert "branch-protection receipt" in joined or "branch protection" in joined
    assert "approved independent-review" in joined
    # round 39: sealed exclusion is identity-only (no by-construction rule)
    assert _PACKET["sealed_exclusion"]["identity_only_rule"]
    assert "trust_manifest" in _PACKET
    assert _PACKET["independent_review_record"]["binds"].count("trust_manifest_sha256")


def test_sealed_exclusion_is_identity_only():
    """Codex round 39 #1: no by-construction; every sealed pool is an identity
    anti-join."""
    se = _PACKET["sealed_exclusion"]
    assert "by_construction_rule" not in se
    rule = se["identity_only_rule"].lower()
    assert "identity anti-join" in rule and "audio_checksum_sha256 only" in rule
    assert "no by-construction" in rule
    assert se["mint_reads_sealed_bytes"] is False


def test_branch_protection_is_an_owner_receipt_not_a_workflow_api_call():
    """Codex round 39 #2: the workflow does NOT read the protection API (the
    token lacks Administration:read); it is an owner-run receipt + the protected
    environment is the live boundary."""
    caller = (_WF / "arm2-nomination-mint.yml").read_text()
    assert "branches/master/protection" not in caller   # no broken API call
    assert "OWNER-RUN ACTIVATION RECEIPT" in caller
    # the protected environments remain the live authorization boundary
    assert "verify_protected_environments.py --only-supplied" in caller
    joined = " ".join(_PACKET["preconditions"]).lower()
    assert "owner-run branch-protection receipt" in joined
    assert "administration:read" in joined
