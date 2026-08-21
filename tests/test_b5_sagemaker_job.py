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


def _mini_repo(tmp_path):
    import subprocess
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"],
                   check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"],
                   check=True)
    return repo


def _commit(repo, msg="c"):
    import subprocess
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", msg], check=True)
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True,
                          check=True).stdout.strip()


def test_review_gate_is_a_structured_committed_record(tmp_path):
    """Codex review #22 finding 1 (reproduced: HOLD_BEFORE_LAST_MARKER_
    ACCEPTED / CHANGES_REQUIRED_BEFORE_LAST_MARKER_ACCEPTED): free-text
    parsing is gone. Only a committed record with decision APPROVED and
    the exact packet sha passes; HOLD and CHANGES_REQUIRED refuse."""
    import json as _json
    from b5_sagemaker_job import (canonical_bindings_sha256,
                                  review_record_approves)
    b = bindings()
    job = b["job_id"]
    repo = _mini_repo(tmp_path)
    reviews = repo / "platform/decisions/reviews"
    reviews.mkdir(parents=True)
    path = reviews / f"{job}.json"

    record = {"job_id": job,
              "bindings_sha256": canonical_bindings_sha256(b),
              "decision": "APPROVED", "reviewer": "codex",
              "basis": "packet reviewed"}
    path.write_text(_json.dumps(record))
    oid = _commit(repo)
    assert review_record_approves(job, b, repo, oid)["decision"] == "APPROVED"

    for decision in ("HOLD", "CHANGES_REQUIRED", "REJECTED", ""):
        path.write_text(_json.dumps(dict(record, decision=decision)))
        oid = _commit(repo)
        with pytest.raises(JobRefusal, match="not APPROVED|held"):
            review_record_approves(job, b, repo, oid)

    # a mutated packet cannot ride an old APPROVED record
    path.write_text(_json.dumps(record))
    oid = _commit(repo)
    mutated = bindings()
    mutated["environment"]["MEDZEN_SEED"] = "999"
    with pytest.raises(JobRefusal, match="DIFFERENT packet"):
        review_record_approves(job, mutated, repo, oid)
    # an APPROVED record superseded by HOLD in a later commit refuses at
    # the later commit even though the old bytes still exist in history
    path.write_text(_json.dumps(dict(record, decision="HOLD",
                                      basis="regression found")))
    oid2 = _commit(repo)
    with pytest.raises(JobRefusal):
        review_record_approves(job, b, repo, oid2)
    # ...and the working tree alone never counts
    path.write_text(_json.dumps(record))
    with pytest.raises(JobRefusal):
        review_record_approves(job, b, repo, oid2)


def test_prohibited_scope_smuggled_through_environment_is_caught():
    b = bindings()
    b["environment"]["MEDZEN_EXCLUSIONS_REF"] = "s3://medzen-speech/eval/oops.json"
    request = render_request(b)
    with pytest.raises(JobRefusal, match="prohibited"):
        validate_request(request, b)


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



def test_calibration_receipt_recipe_authority_is_the_committed_packet(tmp_path):
    """Codex review #22 finding 2 (reproduced: LR/batch/accum/seed/
    schedule drift accepted; verdict PASSWORD; boolean billable; wrong-
    account KMS). The committed calibration packet is the recipe
    authority; only the declared scale keys may differ."""
    import json as _json
    from b5_sagemaker_job import (JobRefusal as JR,
                                  canonical_bindings_sha256,
                                  verify_calibration_receipt)
    import hashlib as h

    repo = _mini_repo(tmp_path)
    (repo / "platform/evidence").mkdir(parents=True)
    (repo / "platform/manifests").mkdir(parents=True)
    image = ("558069890522.dkr.ecr.eu-central-1.amazonaws.com/"
             "medzen-trainer-omniasr@sha256:" + "b" * 64)
    cal_env = {"MEDZEN_VARIANT": "ctc", "MEDZEN_TRAIN_MODE": "full",
               "MEDZEN_LANGUAGES": "english,pidgin",
               "MEDZEN_TEMPERATURE": "0.5", "MEDZEN_LR": "1e-5",
               "MEDZEN_LR_SCHEDULE": "constant", "MEDZEN_BATCH_SIZE": "2",
               "MEDZEN_GRAD_ACCUM": "8", "MEDZEN_SEED": "20260820",
               "MEDZEN_EXCLUSIONS_REF": "s3://x/d.json",
               "MEDZEN_EXPECT_EXCLUDED": "1579",
               "MEDZEN_MANIFEST_VERSION": "gb9",
               "MEDZEN_MAX_STEPS": "30", "MEDZEN_CHECKPOINT_EVERY": "30",
               "MEDZEN_WARMUP_STEPS": "10", "MEDZEN_AUDIO_CAP_HOURS": "2"}
    cal = {"job_id": "cal-1", "image_uri_with_digest": image,
           "environment": cal_env}
    (repo / "platform/manifests/CAL.json").write_text(_json.dumps(cal))
    (repo / "platform/evidence/B5-GB9-ADOPTION-2026-001.json").write_text(
        _json.dumps({"version": "gb9", "complete_raw_sha256": "e0" * 32}))
    from b5_sagemaker_job import SCALE_KEYS
    KMS = ("arn:aws:kms:eu-central-1:558069890522:key/"
           "9c336116-c648-4548-95c6-1b926478ae57")

    def receipt_record(**over):
        rec = {"terminal_status": "Completed", "billable_seconds": 1128,
               "verdict": "PASS — chain proven", "job": "cal-1",
               "declared_scale_keys": sorted(SCALE_KEYS),
               "dataset_version": "gb9",
               "dataset_complete_raw_sha256": "e0" * 32,
               "image_uri_with_digest": image,
               "calibration_packet": "platform/manifests/CAL.json",
               "calibration_bindings_sha256":
                   canonical_bindings_sha256(cal),
               "export": {"status": "PASS_MERGED_EXPORT",
                          "model_sha256": "1" * 64,
                          "manifest_sha256": "2" * 64},
               "artifact": {"s3_uri": "s3://b/k", "s3_version_id": "V1",
                            "kms_key": KMS}}
        rec.update(over)
        return rec

    rec_path = repo / "platform/evidence/RECEIPT.json"
    rec_path.write_text(_json.dumps(receipt_record()))
    _commit(repo)

    def arm(**env_over):
        env = dict(cal_env, MEDZEN_MAX_STEPS="40000",
                   MEDZEN_CHECKPOINT_EVERY="2000",
                   MEDZEN_WARMUP_STEPS="500", MEDZEN_AUDIO_CAP_HOURS="100")
        env.update(env_over)
        return {"environment": env, "image_uri_with_digest": image,
                "calibration_receipt": {
                    "record": "platform/evidence/RECEIPT.json",
                    "record_sha256": h.sha256(
                        rec_path.read_bytes()).hexdigest()}}

    verify_calibration_receipt(arm(), repo)   # scale-only deltas PASS

    # THE reproduced bypass: uncalibrated recipe drift refuses
    for key, value in (("MEDZEN_LR", "2e-5"), ("MEDZEN_BATCH_SIZE", "1"),
                        ("MEDZEN_GRAD_ACCUM", "1"), ("MEDZEN_SEED", "999"),
                        ("MEDZEN_LR_SCHEDULE", "cosine")):
        with pytest.raises(JR, match="recipe drift"):
            verify_calibration_receipt(arm(**{key: value}), repo)

    # fabricated-receipt fields Codex reproduced
    fabrications = [
        ({"verdict": "PASSWORD"}, "not PASS"),
        ({"billable_seconds": True}, "positive integer"),
        ({"artifact": {"s3_uri": "s3://b/k", "s3_version_id": "V1",
                        "kms_key": "arn:aws:kms:eu-central-1:"
                                    "999999999999:key/x"}},
         "not this account"),
        ({"declared_scale_keys": ["MEDZEN_MAX_STEPS"]}, "scale keys"),
        ({"calibration_bindings_sha256": "9" * 64}, "calibration packet"),
    ]
    for over, needle in fabrications:
        rec_path.write_text(_json.dumps(receipt_record(**over)))
        _commit(repo)
        with pytest.raises(JR, match=needle):
            verify_calibration_receipt(arm(), repo)
    rec_path.write_text(_json.dumps(receipt_record()))
    _commit(repo)

    # AWS re-verification refuses when AWS disagrees with the receipt
    from b5_sagemaker_job import verify_receipt_against_aws
    record = receipt_record()

    class SM:
        def __init__(self, **over):
            self.desc = {"TrainingJobStatus": "Completed",
                         "BillableTimeInSeconds": 1128,
                         "AlgorithmSpecification": {"TrainingImage": image}}
            self.desc.update(over)
        def describe_training_job(self, TrainingJobName):
            return self.desc

    class S3:
        def __init__(self, vid="V1", kms=KMS):
            self.vid, self.kms = vid, kms
        def head_object(self, Bucket, Key):
            return {"VersionId": self.vid, "SSEKMSKeyId": self.kms}

    verify_receipt_against_aws(record, SM(), S3())
    with pytest.raises(JR, match="not Completed"):
        verify_receipt_against_aws(record,
                                   SM(TrainingJobStatus="Stopped"), S3())
    with pytest.raises(JR, match="billable"):
        verify_receipt_against_aws(record,
                                   SM(BillableTimeInSeconds=900), S3())
    with pytest.raises(JR, match="VersionId"):
        verify_receipt_against_aws(record, SM(), S3(vid="FORGED"))
    with pytest.raises(JR, match="KMS"):
        verify_receipt_against_aws(record, SM(),
                                   S3(kms=KMS.replace("9c33", "dead")))


def test_reservation_must_bind_this_packet_and_registry_arithmetic(tmp_path):
    """Codex review #22 finding 4 (reproduced: DIFFERENT_PACKET_ACTIVE_
    RESERVATION_ACCEPTED, OVER_BUDGET_REGISTRY_ACCEPTED)."""
    import json as _json
    import hashlib as h
    from b5_sagemaker_job import (JobRefusal as JR,
                                  canonical_bindings_sha256,
                                  verify_active_reservation)
    b = bindings()
    repo = _mini_repo(tmp_path)
    (repo / "platform/finance").mkdir(parents=True)
    reg_path = repo / "platform/finance/REG.json"

    def commit_registry(packet_sha=None, reservation=70.0,
                         recognized=584.43, ceiling=800.0,
                         summary_active=None):
        reg = {"allocations": [
            {"allocation_id": "ARM-1",
             "financial_state": "ACTIVE_RESERVED",
             "reservation_usd": reservation,
             "packet_bindings_sha256":
                 packet_sha or canonical_bindings_sha256(b)}],
            "guardrail_summary": {
                "aggregate_ceiling_usd": ceiling,
                "recognized_committed_guardrail_usd": recognized,
                "active_reservations_usd": (reservation
                                             if summary_active is None
                                             else summary_active)}}
        reg_path.write_text(_json.dumps(reg))
        _commit(repo)
        return {"file": "platform/finance/REG.json",
                "sha256": h.sha256(reg_path.read_bytes()).hexdigest(),
                "allocation_id": "ARM-1"}

    verify_active_reservation(commit_registry(), b, 64.0, repo)
    # ANOTHER packet's reservation cannot fund this launch
    with pytest.raises(JR, match="DIFFERENT packet"):
        verify_active_reservation(commit_registry(packet_sha="f" * 64),
                                  b, 64.0, repo)
    # an over-budget registry refuses on its own arithmetic
    with pytest.raises(JR, match="aggregate ceiling"):
        verify_active_reservation(
            commit_registry(recognized=780.0), b, 64.0, repo)
    # a summary lying about its own reservations refuses
    with pytest.raises(JR, match="does not match its own"):
        verify_active_reservation(
            commit_registry(summary_active=0.0), b, 64.0, repo)


def test_owner_intent_requires_a_verifying_ssh_signature(tmp_path):
    """Codex review #22 finding 3: commit ids are deterministic, so
    oid-quoting proved reference, not identity. The owner now SIGNS the
    committed launch intent (ssh-keygen -Y, namespace medzen-launch);
    no signature, wrong key, wrong namespace or tampered intent
    refuses."""
    import json as _json
    import subprocess
    from b5_sagemaker_job import JobRefusal as JR, owner_intent_is_signed

    repo = _mini_repo(tmp_path)
    intents = repo / "platform/decisions/launch-intents"
    intents.mkdir(parents=True)
    job = "b5-universal-arm1-2026-005"
    intent = {"job_id": job, "packet": {"file": "p.json",
                                         "canonical_sha256": "a" * 64}}
    intent_path = intents / f"{job}.json"
    intent_path.write_text(_json.dumps(intent))

    # owner key (test key standing in for the owner's real one)
    key = tmp_path / "owner_ed25519"
    subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-q",
                    "-f", str(key)], check=True)
    pub = (tmp_path / "owner_ed25519.pub").read_text().strip()
    signers = repo / "platform/decisions/OWNER-ALLOWED-SIGNERS"
    signers.write_text(f"owner@medzen {pub.split(' ')[0]} {pub.split(' ')[1]}\n")

    oid = _commit(repo)
    with pytest.raises(JR, match="not authorized"):
        owner_intent_is_signed(job, repo, oid)

    # owner signs the exact committed bytes
    subprocess.run(["ssh-keygen", "-Y", "sign", "-f", str(key),
                    "-n", "medzen-launch", str(intent_path)], check=True)
    (intents / f"{job}.json.sig").rename(intents / f"{job}.sig")
    oid = _commit(repo)
    assert owner_intent_is_signed(job, repo, oid)["job_id"] == job

    # tampering the intent AFTER signing breaks verification
    intent_path.write_text(_json.dumps(dict(intent, packet={
        "file": "p.json", "canonical_sha256": "f" * 64})))
    oid2 = _commit(repo)
    with pytest.raises(JR, match="does NOT verify"):
        owner_intent_is_signed(job, repo, oid2)

    # a signature from an UNENROLLED key refuses
    intent_path.write_text(_json.dumps(intent))
    rogue = tmp_path / "rogue_ed25519"
    subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-q",
                    "-f", str(rogue)], check=True)
    subprocess.run(["ssh-keygen", "-Y", "sign", "-f", str(rogue),
                    "-n", "medzen-launch", str(intent_path)], check=True)
    (intents / f"{job}.json.sig").rename(intents / f"{job}.sig")
    oid3 = _commit(repo)
    with pytest.raises(JR, match="does NOT verify"):
        owner_intent_is_signed(job, repo, oid3)


def test_protocol_loads_from_captured_commit_with_containment(tmp_path):
    """Codex review #22 finding 5: working-tree bytes and an
    uncontained pointer path were accepted. With an oid, both files come
    from that commit; escape paths refuse."""
    import json as _json
    import hashlib as h
    from b5_sagemaker_job import JobRefusal as JR, load_protocol

    repo = _mini_repo(tmp_path)
    (repo / "platform/decisions").mkdir(parents=True)
    protocol = {"record": "P-1", "mandatory_languages": ["x"]}
    proto_path = repo / "platform/decisions/P-1.json"
    proto_path.write_text(_json.dumps(protocol))
    pointer = {"record": "P-1", "file": "platform/decisions/P-1.json",
               "sha256": h.sha256(proto_path.read_bytes()).hexdigest()}
    (repo / "platform/decisions/CURRENT-PROMOTION-PROTOCOL.json"
     ).write_text(_json.dumps(pointer))
    oid = _commit(repo)
    assert load_protocol(repo, oid)["record"] == "P-1"
    # WORKING-TREE tampering after the capture changes nothing at oid
    proto_path.write_text(_json.dumps({"record": "P-1",
                                        "mandatory_languages": ["evil"]}))
    assert load_protocol(repo, oid)["mandatory_languages"] == ["x"]
    # coordinated working-tree pointer+protocol edit refuses at oid too
    # (the committed pointer still governs)
    # escape path refuses
    (repo / "platform/decisions/CURRENT-PROMOTION-PROTOCOL.json"
     ).write_text(_json.dumps(dict(pointer, file="/etc/passwd")))
    oid2 = _commit(repo)
    with pytest.raises(JR, match="escapes"):
        load_protocol(repo, oid2)
