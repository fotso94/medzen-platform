"""C2 tests: exact render, drift refusal, ceilings, gates. No AWS anywhere."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from b5_sagemaker_job import (  # noqa: E402
    JobRefusal,
    render_request,
    review_is_recorded,
    validate_request,
)


def bindings() -> dict:
    return {
        "job_id": "t5-calibration-yemba",
        "image_uri_with_digest": (
            "558069890522.dkr.ecr.eu-central-1.amazonaws.com/"
            "medzen-trainer-omniasr@sha256:" + "a" * 64),
        "instance_type": "ml.g6.xlarge",
        "kms_key_arn": ("arn:aws:kms:eu-central-1:558069890522:key/"
                        "9c336116-c648-4548-95c6-1b926478ae57"),
        "subnets": ["subnet-0000000000000aaaa"],
        "security_group_ids": ["sg-0000000000000bbbb"],
        "max_runtime_seconds": 14400,
        "max_wait_seconds": 28800,
        "cost_ceiling_usd": 10.0,
        "volume_gb": 100,
        "cost_registry_line": "ASR-EVAL-COST-REGISTRY line 035",
        "environment": {
            "MEDZEN_VARIANT": "ctc",
            "MEDZEN_MANIFEST_VERSION": "v9",
            "MEDZEN_LANGUAGES": "yemba",
            "MEDZEN_SEED": "7",
            "MEDZEN_MAX_STEPS": "600",
            # required since Codex review #4: every packet declares its mode
            "MEDZEN_TRAIN_MODE": "lora",
        },
    }


def test_render_is_deterministic_and_exact():
    a = json.dumps(render_request(bindings()), sort_keys=True)
    b = json.dumps(render_request(bindings()), sort_keys=True)
    assert a == b


def test_rendered_request_carries_the_non_negotiables():
    request = render_request(bindings())
    assert request["EnableManagedSpotTraining"] is True
    assert "VolumeKmsKeyId" not in request["ResourceConfig"], (
        "g6 NVMe storage is hardware-encrypted; AWS refuses VolumeKmsKeyId")
    assert request["OutputDataConfig"]["KmsKeyId"].startswith("arn:aws:kms:")
    assert request["CheckpointConfig"]["LocalPath"] == "/opt/ml/checkpoints"
    assert request["VpcConfig"]["Subnets"]
    assert request["StoppingCondition"]["MaxWaitTimeInSeconds"] >= \
        request["StoppingCondition"]["MaxRuntimeInSeconds"]


def test_validate_passes_the_exact_form_and_refuses_one_char_drift():
    request = render_request(bindings())
    result = validate_request(request, bindings())
    assert result["status"] == "PASS_EXACT_TRAINING_REQUEST"
    drifted = copy.deepcopy(request)
    drifted["ResourceConfig"]["VolumeSizeInGB"] += 1
    with pytest.raises(JobRefusal, match="differs"):
        validate_request(drifted, bindings())


def test_floating_tag_image_is_refused():
    b = bindings()
    b["image_uri_with_digest"] = (
        "558069890522.dkr.ecr.eu-central-1.amazonaws.com/medzen-trainer:latest")
    with pytest.raises(JobRefusal, match="digest"):
        render_request(b)


def test_foreign_registry_image_is_refused():
    b = bindings()
    b["image_uri_with_digest"] = (
        "999999999999.dkr.ecr.eu-central-1.amazonaws.com/x@sha256:" + "a" * 64)
    with pytest.raises(JobRefusal, match="ECR"):
        render_request(b)


def test_instance_outside_allowlist_is_refused():
    b = bindings()
    b["instance_type"] = "ml.p4d.24xlarge"
    with pytest.raises(JobRefusal, match="allowlist"):
        render_request(b)


def test_runtime_above_ceiling_is_refused_with_the_arithmetic():
    b = bindings()
    b["max_runtime_seconds"] = 360000  # 100h on-demand >> $10
    b["max_wait_seconds"] = 720000
    with pytest.raises(JobRefusal, match="ceiling"):
        render_request(b)


def test_wait_shorter_than_runtime_breaks_the_spot_contract():
    b = bindings()
    b["max_wait_seconds"] = b["max_runtime_seconds"] - 1
    with pytest.raises(JobRefusal, match="spot"):
        render_request(b)


def test_llm_variant_cannot_be_launched():
    b = bindings()
    b["environment"]["MEDZEN_VARIANT"] = "llm"
    with pytest.raises(JobRefusal, match="ctc"):
        render_request(b)


def test_missing_environment_keys_are_refused():
    b = bindings()
    del b["environment"]["MEDZEN_SEED"]
    with pytest.raises(JobRefusal, match="MEDZEN_SEED"):
        render_request(b)


@pytest.mark.parametrize("key", ["job_id", "kms_key_arn", "subnets",
                                 "security_group_ids", "cost_registry_line"])
def test_absent_bindings_keys_are_refused(key):
    b = bindings()
    del b[key]
    with pytest.raises(JobRefusal, match=key):
        render_request(b)


def test_launch_gate_needs_the_approval_phrase(tmp_path):
    shared = tmp_path / "claude_instructions.txt"
    shared.write_text("nothing relevant\n")
    assert review_is_recorded("t5-calibration-yemba", shared_file=shared) is False
    shared.write_text(
        "REVIEW ...\nDECISION: APPROVED — risk accepted, "
        "authorizing training job t5-calibration-yemba per packet.\n")
    assert review_is_recorded("t5-calibration-yemba", shared_file=shared) is True


def test_approval_phrase_without_approved_decision_fails(tmp_path):
    shared = tmp_path / "claude_instructions.txt"
    shared.write_text(
        "DECISION: HOLD — do not launch\n"
        "... authorizing training job t5-calibration-yemba pending fixes\n")
    text_ok = review_is_recorded("t5-calibration-yemba", shared_file=shared)
    assert text_ok is False, "HOLD text before the phrase must not authorize"


def test_prohibited_scope_smuggled_through_environment_is_caught():
    b = bindings()
    b["environment"]["MEDZEN_EXCLUSIONS_REF"] = "s3://medzen-speech/eval/oops.json"
    request = render_request(b)
    with pytest.raises(JobRefusal, match="prohibited"):
        validate_request(request, b)


def test_approval_more_than_4000_chars_before_the_phrase_does_not_carry(tmp_path):
    shared = tmp_path / "claude_instructions.txt"
    shared.write_text(
        "DECISION: APPROVED — some OTHER packet entirely\n"
        + ("x" * 4100) + "\n"
        + "notes mentioning authorizing training job t5-calibration-yemba later\n")
    assert review_is_recorded("t5-calibration-yemba", shared_file=shared) is False


def test_ceiling_arithmetic_is_conservative_by_construction():
    from b5_sagemaker_job import ON_DEMAND_USD_PER_HOUR
    assert ON_DEMAND_USD_PER_HOUR["ml.g6.xlarge"] >= 1.5, (
        "the ceiling rate must stay above any plausible published rate; "
        "lowering it widens what a ceiling authorizes")


def test_zero_or_absurd_volume_is_refused():
    b = bindings()
    b["volume_gb"] = 0
    with pytest.raises(JobRefusal, match="volume_gb"):
        render_request(b)
    b["volume_gb"] = 5000
    with pytest.raises(JobRefusal, match="volume_gb"):
        render_request(b)


def test_on_demand_opt_out_needs_explicit_flag_and_reason():
    b = bindings()
    b["managed_spot"] = False
    with pytest.raises(JobRefusal, match="reason"):
        render_request(b)
    b["managed_spot_reason"] = "spot quota 0 at T5; increase filed"
    del b["max_wait_seconds"]
    request = render_request(b)
    assert request["EnableManagedSpotTraining"] is False
    assert "MaxWaitTimeInSeconds" not in request["StoppingCondition"]


def test_on_demand_with_leftover_max_wait_is_refused():
    b = bindings()
    b["managed_spot"] = False
    b["managed_spot_reason"] = "x"
    with pytest.raises(JobRefusal, match="spot-only"):
        render_request(b)


def test_spot_remains_the_default():
    request = render_request(bindings())
    assert request["EnableManagedSpotTraining"] is True


def test_container_entrypoint_runs_the_trainer_module():
    """SageMaker's default appends 'train' to the image entrypoint; the
    request must name the trainer module explicitly (first-launch lesson)."""
    request = render_request(bindings())
    spec = request["AlgorithmSpecification"]
    assert spec["ContainerEntrypoint"] == ["/opt/venv/bin/python"]
    assert spec["ContainerArguments"] == ["-m", "pipeline.omniasr_train"]


def test_render_runs_the_trainer_parser_on_the_environment(tmp_path):
    """Codex review #4 (reproduced): a packet with LR=nan, no train mode
    and no warmup rendered + validated PASS. The launcher now runs the
    trainer's OWN parse_config, so both layers refuse identically."""
    import copy
    import json
    import pytest
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    bindings = json.loads((root / "platform/manifests/"
        "B5-KINYARWANDA-FTCAL-SAGEMAKER-BINDINGS-2026-003.json").read_bytes())

    import sys
    sys.path.insert(0, str(root / "scripts"))
    from b5_sagemaker_job import JobRefusal, render_request

    # the committed ftcal packet must still render (it declares its mode,
    # bounded LR and warmup) — but only after gaining a schedule field
    good = copy.deepcopy(bindings)
    good["environment"]["MEDZEN_LR_SCHEDULE"] = "constant"
    render_request(good)

    poisoned = copy.deepcopy(good)
    poisoned["environment"]["MEDZEN_LR"] = "nan"
    with pytest.raises(JobRefusal, match="trainer would refuse"):
        render_request(poisoned)

    modeless = copy.deepcopy(good)
    del modeless["environment"]["MEDZEN_TRAIN_MODE"]
    with pytest.raises(JobRefusal, match="MEDZEN_TRAIN_MODE"):
        render_request(modeless)

    lazy_warmup = copy.deepcopy(good)
    lazy_warmup["environment"]["MEDZEN_WARMUP_STEPS"] = "0"
    with pytest.raises(JobRefusal, match="trainer would refuse"):
        render_request(lazy_warmup)


def test_multilingual_packets_bind_the_exact_pilot_profile():
    """Codex review #8: generic limits allowed arbitrary language subsets /
    versions through. Multilingual-full packets bind the frozen profile."""
    import copy
    import json as _json
    import pytest
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    good = _json.loads((root / "platform/manifests/"
        "B5-UNIVERSAL-FTCAL-SAGEMAKER-BINDINGS-2026-004.json").read_bytes())
    render_request(good)   # the committed calibration packet must pass

    for mutation in (
            {"MEDZEN_LANGUAGES": "kinyarwanda,english"},
            {"MEDZEN_MANIFEST_VERSION": "gb8"},
            {"MEDZEN_TEMPERATURE": "0.4"},
            {"MEDZEN_EXCLUSIONS_REF": "s3://medzen-speech/other.json"}):
        bad = copy.deepcopy(good)
        bad["environment"].update(mutation)
        with pytest.raises(JobRefusal):
            render_request(bad)


def test_authorization_binds_the_canonical_packet_sha(tmp_path):
    """Codex review #9: the approval phrase bound only the job id, so a
    mutated packet (seed/LR/batch/image) launched under an old approval.
    The authorization must now cite the canonical bindings sha256."""
    from b5_sagemaker_job import canonical_bindings_sha256, review_is_recorded

    b = bindings()
    sha = canonical_bindings_sha256(b)
    shared = tmp_path / "reviews.txt"
    shared.write_text(
        "review text\nDECISION: APPROVED\n"
        f"authorizing training job {b['job_id']} bindings-sha256 {sha}\n")
    assert review_is_recorded(b["job_id"], b, shared) is True
    # job id alone no longer suffices when bindings are supplied
    shared.write_text(
        "review text\nDECISION: APPROVED\n"
        f"authorizing training job {b['job_id']} \n")
    assert review_is_recorded(b["job_id"], b, shared) is False
    # a MUTATED packet cannot ride the original approval
    shared.write_text(
        "review text\nDECISION: APPROVED\n"
        f"authorizing training job {b['job_id']} bindings-sha256 {sha}\n")
    mutated = bindings()
    mutated["environment"]["MEDZEN_SEED"] = "999"
    assert review_is_recorded(mutated["job_id"], mutated, shared) is False


def test_owner_authorization_must_be_committed_not_mutable_text(tmp_path):
    """Codex reviews #19-#21. v3 contract: committed record + a dedicated
    approval file whose phrase quotes the record's OWN commit oid —
    pre-published phrases are structurally dead (reviewer reproduced
    fabricated_record_plus_preseeded_phrase_passes=true against v2)."""
    import json as _json
    import subprocess
    from b5_sagemaker_job import (canonical_bindings_sha256,
                                  owner_authorization_is_committed)

    b = bindings()
    job = b["job_id"]
    sha = canonical_bindings_sha256(b)
    repo = tmp_path / "repo"
    auth_dir = repo / "platform/decisions/launch-authorizations"
    auth_dir.mkdir(parents=True)
    approvals = tmp_path / "approvals"
    approvals.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)

    record = {"job_id": job, "bindings_sha256": sha, "ceiling_usd": 70,
              "non_transferable": True,
              "owner_statement": "approved for exactly this packet"}
    path = auth_dir / f"{job}.json"
    path.write_text(_json.dumps(record))

    def gate(jid=job, bnd=b, worst=64.0):
        return owner_authorization_is_committed(jid, bnd, worst, repo,
                                                approvals_dir=approvals)

    # a PRE-PUBLISHED phrase (written before the record commit exists,
    # so it cannot quote the record's commit oid) can never authorize
    stale = (f"I authorize {job} ceiling usd 70 "
             f"bindings-sha256-16 {sha[:16]}")
    (approvals / f"{job}.approval").write_text(stale)
    assert gate() is False                      # record not committed yet
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "auth"], check=True)
    assert gate() is False, "phrase without record-commit oid must refuse"

    oid = subprocess.run(["git", "-C", str(repo), "log", "-1",
                          "--format=%H", "HEAD", "--",
                          f"platform/decisions/launch-authorizations/{job}.json"],
                         capture_output=True, text=True).stdout.strip()
    phrase = (f"I authorize {job} ceiling usd 70 bindings-sha256-16 "
              f"{sha[:16]} record-commit {oid[:16]}")
    (approvals / f"{job}.approval").write_text(phrase + "\n")
    assert gate() is True

    # exact-equality file: extra text refuses (no free-text windows)
    (approvals / f"{job}.approval").write_text(
        "DECISION: APPROVED\n" + phrase)
    assert gate() is False
    (approvals / f"{job}.approval").write_text(phrase)

    # a MUTATED packet cannot ride the committed authorization
    mutated = bindings()
    mutated["environment"]["MEDZEN_SEED"] = "999"
    assert gate(bnd=mutated) is False
    # insufficient ceiling refuses
    assert gate(worst=71.0) is False
    # NON-INTEGER ceiling refuses (Codex review #21: int() truncated
    # $70.9 into a phrase claiming $70)
    frac = dict(record, ceiling_usd=70.9)
    path.write_text(_json.dumps(frac))
    subprocess.run(["git", "-C", str(repo), "commit", "-aqm", "frac"], check=True)
    assert gate() is False
    # tampering the record after approval breaks the commit-oid link:
    # the phrase quotes the OLD record commit, the record is now newer
    path.write_text(_json.dumps(record))
    subprocess.run(["git", "-C", str(repo), "commit", "-aqm", "restore"], check=True)
    assert gate() is False, "phrase quoting a stale record commit must refuse"
    # path traversal in the job id can never escape the auth dir
    assert gate(jid="../evil") is False


def test_launch_requires_the_medzen_account():
    """Codex review #20 finding 5: launch ran on ambient credentials with
    no STS check; this machine's default account is NOT MedZen's."""
    from b5_sagemaker_job import assert_medzen_account

    class R:
        def __init__(self, out, rc=0):
            self.stdout, self.stderr, self.returncode = out, "", rc

    assert_medzen_account(runner=lambda *a, **k: R("558069890522\n"))
    for out, rc in (("111111111111\n", 0), ("", 1), ("None\n", 0)):
        with pytest.raises(JobRefusal, match="MedZen account"):
            assert_medzen_account(
                runner=lambda *a, _o=out, _r=rc, **k: R(_o, _r))


def test_above_tier_packet_must_bind_a_passing_calibration_receipt(tmp_path):
    """Codex reviews #20-#21: v1 of this gate accepted the genuine gb8
    receipt for a gb9 arm (wrong_gb8_calibration_receipt_accepted_
    for_gb9_arm=true) because every semantic identity came from the
    PACKET. Now the RECEIPT itself carries them all and each is
    cross-checked."""
    import hashlib as h
    import json as _json
    import subprocess
    from b5_sagemaker_job import (JobRefusal as JR,
                                  canonical_bindings_sha256,
                                  verify_calibration_receipt)

    repo = tmp_path / "repo"
    (repo / "platform/evidence").mkdir(parents=True)
    (repo / "platform/manifests").mkdir(parents=True)
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)

    image = ("558069890522.dkr.ecr.eu-central-1.amazonaws.com/"
             "medzen-trainer-omniasr@sha256:" + "b" * 64)
    invariants = {"MEDZEN_VARIANT": "ctc", "MEDZEN_TRAIN_MODE": "full",
                  "MEDZEN_LANGUAGES": "english,pidgin",
                  "MEDZEN_TEMPERATURE": "0.5",
                  "MEDZEN_EXCLUSIONS_REF": "s3://x/d.json",
                  "MEDZEN_EXPECT_EXCLUDED": "1579",
                  "MEDZEN_MANIFEST_VERSION": "gb9"}
    cal_packet = {"job_id": "cal-1", "environment": dict(invariants)}
    (repo / "platform/manifests/CAL-PACKET.json").write_text(
        _json.dumps(cal_packet))
    (repo / "platform/evidence/B5-GB9-ADOPTION-2026-001.json").write_text(
        _json.dumps({"version": "gb9", "complete_raw_sha256": "e0" * 32}))
    (repo / "platform/evidence/B5-GB8-ADOPTION-2026-001.json").write_text(
        _json.dumps({"version": "gb8", "complete_raw_sha256": "d0" * 32}))

    def receipt_record(**over):
        rec = {"terminal_status": "Completed", "billable_seconds": 1128,
               "verdict": "PASS — chain proven", "job": "cal-1",
               "dataset_version": "gb9",
               "dataset_complete_raw_sha256": "e0" * 32,
               "image_uri_with_digest": image,
               "environment_invariants": dict(invariants),
               "calibration_packet": "platform/manifests/CAL-PACKET.json",
               "calibration_bindings_sha256":
                   canonical_bindings_sha256(cal_packet),
               "export": {"status": "PASS_MERGED_EXPORT",
                          "model_sha256": "1" * 64,
                          "manifest_sha256": "2" * 64},
               "artifact": {"s3_version_id": "Vid123",
                            "kms_key": "arn:aws:kms:eu-central-1:5:key/k"}}
        rec.update(over)
        return rec

    rec_path = repo / "platform/evidence/CAL-RECEIPT.json"
    rec_path.write_text(_json.dumps(receipt_record()))
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "evidence"], check=True)
    rec_sha = h.sha256(rec_path.read_bytes()).hexdigest()

    def arm(**env_over):
        env = dict(invariants)
        env.update(env_over)
        return {"environment": env, "image_uri_with_digest": image,
                "calibration_receipt": {
                    "record": "platform/evidence/CAL-RECEIPT.json",
                    "record_sha256": rec_sha}}

    verify_calibration_receipt(arm(), repo)      # the honest chain passes

    # THE Codex #21 reproduction: a gb8 receipt for a gb9 arm refuses —
    # commit a genuine gb8 receipt and point the gb9 arm at it
    gb8_path = repo / "platform/evidence/CAL-RECEIPT-GB8.json"
    gb8_inv = dict(invariants, MEDZEN_MANIFEST_VERSION="gb8")
    gb8_path.write_text(_json.dumps(receipt_record(
        dataset_version="gb8", dataset_complete_raw_sha256="d0" * 32,
        environment_invariants=gb8_inv)))
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "gb8"], check=True)
    wrong = arm()
    wrong["calibration_receipt"] = {
        "record": "platform/evidence/CAL-RECEIPT-GB8.json",
        "record_sha256": h.sha256(gb8_path.read_bytes()).hexdigest()}
    with pytest.raises(JR, match="wrong calibration"):
        verify_calibration_receipt(wrong, repo)

    # each semantic mismatch refuses on the RECEIPT's own declarations
    cases = [
        (receipt_record(terminal_status="Stopped"), "Completed"),
        (receipt_record(billable_seconds=0), "billable"),
        (receipt_record(dataset_complete_raw_sha256="f0" * 32), "adoption"),
        (receipt_record(image_uri_with_digest=image.replace("b" * 64,
                                                            "c" * 64)),
         "different image"),
        (receipt_record(environment_invariants=dict(
            invariants, MEDZEN_TEMPERATURE="0.4")), "invariant"),
        (receipt_record(calibration_bindings_sha256="9" * 64),
         "calibration packet"),
        (receipt_record(export={"status": "PASS_MERGED_EXPORT",
                                 "model_sha256": "zz",
                                 "manifest_sha256": "2" * 64}),
         "hash-complete"),
        (receipt_record(artifact={"s3_version_id": "",
                                    "kms_key": "arn:aws:kms:x"}),
         "VersionId"),
    ]
    for record, needle in cases:
        rec_path.write_text(_json.dumps(record))
        subprocess.run(["git", "-C", str(repo), "commit", "-aqm", "mut"],
                       check=True)
        bad = arm()
        bad["calibration_receipt"]["record_sha256"] = h.sha256(
            rec_path.read_bytes()).hexdigest()
        with pytest.raises(JR, match=needle):
            verify_calibration_receipt(bad, repo)


def test_launch_requires_an_active_committed_reservation(tmp_path):
    """Codex review #21: the launcher never read the cost registry — a
    job could launch while its reservation said PENDING. The packet
    binds the registry sha; the allocation must be the single
    ACTIVE_RESERVED line covering the worst case."""
    import hashlib as h
    import json as _json
    import subprocess
    from b5_sagemaker_job import (JobRefusal as JR,
                                  verify_active_reservation)

    repo = tmp_path / "repo"
    (repo / "platform/finance").mkdir(parents=True)
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    reg_path = repo / "platform/finance/REG.json"

    def commit_registry(state, reservation=70.0, extra_active=False):
        allocations = [
            {"allocation_id": "OLD-1",
             "financial_state": "CLOSED_RECONCILED"},
            {"allocation_id": "ARM-1", "financial_state": state,
             "reservation_usd": reservation}]
        if extra_active:
            allocations.append({"allocation_id": "OTHER",
                                 "financial_state": "ACTIVE_RESERVED",
                                 "reservation_usd": 10})
        reg_path.write_text(_json.dumps({"allocations": allocations}))
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "reg"],
                       check=True)
        return h.sha256(reg_path.read_bytes()).hexdigest()

    def packet(sha):
        return {"cost_registry_binding": {
            "file": "platform/finance/REG.json", "sha256": sha,
            "allocation_id": "ARM-1"}}

    sha = commit_registry("PENDING_OWNER_AUTHORIZATION")
    with pytest.raises(JR, match="not ACTIVE_RESERVED"):
        verify_active_reservation(packet(sha), 64.0, repo)
    sha = commit_registry("ACTIVE_RESERVED", reservation=50.0)
    with pytest.raises(JR, match="does not cover"):
        verify_active_reservation(packet(sha), 64.0, repo)
    sha = commit_registry("ACTIVE_RESERVED", extra_active=True)
    with pytest.raises(JR, match="one-active-reservation"):
        verify_active_reservation(packet(sha), 64.0, repo)
    sha = commit_registry("ACTIVE_RESERVED")
    verify_active_reservation(packet(sha), 64.0, repo)   # the honest one
    # a stale sha (registry changed since the packet bound it) refuses
    commit_registry("ACTIVE_RESERVED", reservation=71.0)
    with pytest.raises(JR, match="sha256 does not match"):
        verify_active_reservation(packet(sha), 64.0, repo)


def test_review_hold_after_approval_overrides(tmp_path):
    """Codex review #21: a newer DECISION: HOLD did not override an
    older nearby APPROVED, and the FIRST mention governed instead of
    the last."""
    shared = tmp_path / "shared.txt"
    marker = "authorizing training job t5-calibration-yemba "
    shared.write_text(
        f"DECISION: APPROVED\n{marker}per packet\n"
        f"later findings...\nDECISION: HOLD — regression found\n")
    assert review_is_recorded("t5-calibration-yemba",
                              shared_file=shared) is False
    # a FRESH approval after the hold (a new last mention) authorizes
    shared.write_text(shared.read_text()
                      + f"re-reviewed, fixed\nDECISION: APPROVED\n"
                        f"{marker}per packet rev2\n")
    assert review_is_recorded("t5-calibration-yemba",
                              shared_file=shared) is True
