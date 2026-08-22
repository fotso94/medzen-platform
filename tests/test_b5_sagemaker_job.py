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
               "verdict": "PASS — chain proven", "job": "medzen-b5-cal-1",
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

    # AWS re-verification: the COMPLETE live job contract vs the
    # committed calibration packet (Codex review #23 reproduced the
    # genuine gb8 job passing as a gb9 calibration)
    from b5_sagemaker_job import verify_receipt_against_aws
    record = receipt_record()

    class SM:
        def __init__(self, **over):
            self.desc = {"TrainingJobStatus": "Completed",
                         "BillableTimeInSeconds": 1128,
                         "AlgorithmSpecification": {"TrainingImage": image},
                         "Environment": dict(cal_env),
                         "ModelArtifacts": {"S3ModelArtifacts": "s3://b/k"},
                         "OutputDataConfig": {"KmsKeyId": KMS}}
            self.desc.update(over)
        def describe_training_job(self, TrainingJobName):
            assert TrainingJobName == "medzen-b5-cal-1"
            return self.desc

    class S3:
        def __init__(self, vid="V1", kms=KMS):
            self.vid, self.kms = vid, kms
        def head_object(self, Bucket, Key):
            return {"VersionId": self.vid, "SSEKMSKeyId": self.kms}

    verify_receipt_against_aws(record, cal, SM(), S3())
    # a receipt naming a different job than the packet derives refuses
    with pytest.raises(JR, match="different job"):
        verify_receipt_against_aws(receipt_record(job="medzen-b5-other"),
                                   cal, SM(), S3())
    with pytest.raises(JR, match="not Completed"):
        verify_receipt_against_aws(record, cal,
                                   SM(TrainingJobStatus="Stopped"), S3())
    with pytest.raises(JR, match="billable"):
        verify_receipt_against_aws(record, cal,
                                   SM(BillableTimeInSeconds=900), S3())
    # THE #23 reproduction: the live Environment differs (a gb8 job
    # wearing a gb9 receipt) — refuses on the full-env comparison
    with pytest.raises(JR, match="LIVE SageMaker Environment"):
        verify_receipt_against_aws(record, cal, SM(Environment=dict(
            cal_env, MEDZEN_MANIFEST_VERSION="gb8")), S3())
    with pytest.raises(JR, match="model artifact"):
        verify_receipt_against_aws(record, cal, SM(ModelArtifacts={
            "S3ModelArtifacts": "s3://b/other"}), S3())
    with pytest.raises(JR, match="output KMS"):
        verify_receipt_against_aws(record, cal, SM(OutputDataConfig={
            "KmsKeyId": KMS.replace("9c33", "dead")}), S3())
    with pytest.raises(JR, match="VersionId"):
        verify_receipt_against_aws(record, cal, SM(), S3(vid="FORGED"))
    with pytest.raises(JR, match="KMS identity"):
        verify_receipt_against_aws(record, cal, SM(),
                                   S3(kms=KMS.replace("9c33", "dead")))


def test_reservation_must_bind_this_packet_and_registry_arithmetic(tmp_path):
    """Codex reviews #22-#23 (reproduced: different-packet reservation,
    over-budget registry, NaN values passing every comparison, hand-
    maintained summaries diverging from rows, no expiry)."""
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
                         recognized_row=500.0, ceiling=800.0,
                         summary_recognized=None, summary_active=None,
                         expires="2027-01-01T00:00:00Z"):
        line = {"allocation_id": "ARM-1",
                "financial_state": "ACTIVE_RESERVED",
                "reservation_usd": reservation,
                "reservation_expires_utc": expires,
                "packet_bindings_sha256":
                    packet_sha or canonical_bindings_sha256(b)}
        rec = (recognized_row if summary_recognized is None
               else summary_recognized)
        act = (reservation if summary_active is None else summary_active)
        reg = {"allocations": [
            {"allocation_id": "OLD", "financial_state": "CLOSED",
             "recognized_committed_usd": recognized_row}, line],
            "controls": {"current_active_billable_reservations": "1"},
            "guardrail_summary": {
                "aggregate_ceiling_usd": ceiling,
                "recognized_committed_guardrail_usd": rec,
                "active_reservations_usd": act,
                "committed_plus_reserved_usd": rec + act,
                "guardrail_headroom_after_reservations_usd":
                    ceiling - rec - act}}
        reg_path.write_text(_json.dumps(reg))
        _commit(repo)
        return {"file": "platform/finance/REG.json",
                "sha256": h.sha256(reg_path.read_bytes()).hexdigest(),
                "allocation_id": "ARM-1"}

    verify_active_reservation(commit_registry(), b, 64.0, repo)
    with pytest.raises(JR, match="DIFFERENT packet"):
        verify_active_reservation(commit_registry(packet_sha="f" * 64),
                                  b, 64.0, repo)
    # over-budget on the RECOMPUTED rows refuses (the honest summary
    # reports negative headroom, which the finiteness guard also
    # refuses — either refusal closes the gate)
    with pytest.raises(JR, match="aggregate ceiling|non-finite or negative"):
        verify_active_reservation(
            commit_registry(recognized_row=780.0), b, 64.0, repo)
    # a summary that disagrees with its own rows refuses (reproduced:
    # $584.43 declared vs $514.43 recomputed)
    with pytest.raises(JR, match="does not match the recompute"):
        verify_active_reservation(
            commit_registry(summary_recognized=584.43), b, 64.0, repo)
    with pytest.raises(JR, match="does not match the recompute"):
        verify_active_reservation(
            commit_registry(summary_active=0.0), b, 64.0, repo)
    # NaN and negative values refuse everywhere they appear
    with pytest.raises(JR, match="non-finite or negative"):
        verify_active_reservation(
            commit_registry(recognized_row=float("nan"),
                             summary_recognized=0.0), b, 64.0, repo)
    with pytest.raises(JR, match="non-finite or negative"):
        verify_active_reservation(
            commit_registry(reservation=-70.0, summary_active=-70.0),
            b, 64.0, repo)
    # an EXPIRED reservation refuses (no expiry existed before #23)
    with pytest.raises(JR, match="expired"):
        verify_active_reservation(
            commit_registry(expires="2026-01-01T00:00:00Z"), b, 64.0, repo)
    with pytest.raises(JR, match="valid reservation_expires_utc"):
        verify_active_reservation(
            commit_registry(expires=""), b, 64.0, repo)
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

def test_intent_binds_review_sha_so_post_signing_flips_refuse(tmp_path):
    """Codex review #23 finding 2 reproduction: owner signs while the
    review is PENDING; the review is later flipped to APPROVED; the old
    signature must NOT carry. The intent binds the review's exact
    committed bytes."""
    import json as _json
    import hashlib as h
    from b5_sagemaker_job import (JobRefusal as JR,
                                  canonical_bindings_sha256,
                                  verify_intent_chain)
    b = dict(bindings(), cost_ceiling_usd=70.0,
             calibration_receipt={"record": "platform/evidence/R.json",
                                    "record_sha256": "a" * 64})
    repo = _mini_repo(tmp_path)
    (repo / "platform/decisions/reviews").mkdir(parents=True)
    (repo / "platform/manifests").mkdir(parents=True)
    pkt_path = repo / "platform/manifests/PKT.json"
    pkt_path.write_text(_json.dumps(b))
    sha = canonical_bindings_sha256(b)
    review_path = repo / "platform/decisions/reviews/j.json"

    def write_review(decision):
        review_path.write_text(_json.dumps(
            {"job_id": b["job_id"], "bindings_sha256": sha,
             "decision": decision, "reviewer": "codex", "basis": "x"}))
        _commit(repo)
        return h.sha256(review_path.read_bytes()).hexdigest()

    pending_sha = write_review("PENDING_INDEPENDENT_REVIEW")

    def intent(review_sha, decision="APPROVED"):
        return {"job_id": b["job_id"],
                "packet": {"file": "platform/manifests/PKT.json",
                            "canonical_sha256": sha},
                "review": {"file": "platform/decisions/reviews/j.json",
                            "sha256": review_sha, "decision": decision,
                            "reviewer": "codex"},
                "receipt": {"record_sha256": "a" * 64},
                "ceiling_usd": 70,
                "expires_utc": "2027-01-01T00:00:00Z"}

    # the reproduction: signed over the PENDING review bytes...
    with pytest.raises(JR, match="only sign an APPROVED"):
        verify_intent_chain(intent(pending_sha,
                                    decision="PENDING_INDEPENDENT_REVIEW"),
                            b, 64.0, repo,
                            __import__("b5_sagemaker_job")
                            .repo_head_oid(repo))
    # ...review later flipped to APPROVED: the sha the owner signed no
    # longer matches the committed bytes -> refuses
    write_review("APPROVED")
    oid = __import__("b5_sagemaker_job").repo_head_oid(repo)
    with pytest.raises(JR, match="AFTER signing"):
        verify_intent_chain(intent(pending_sha), b, 64.0, repo, oid)
    # the honest path: intent binds the APPROVED review's exact bytes
    approved_sha = h.sha256(review_path.read_bytes()).hexdigest()
    verify_intent_chain(intent(approved_sha), b, 64.0, repo, oid)
    # strict-integer ceiling + expiry are enforced on the intent itself
    bad = intent(approved_sha)
    bad["ceiling_usd"] = 70.0
    with pytest.raises(JR, match="strict integer"):
        verify_intent_chain(bad, b, 64.0, repo, oid)
    bad = intent(approved_sha)
    bad["expires_utc"] = "2026-01-01T00:00:00Z"
    with pytest.raises(JR, match="expired"):
        verify_intent_chain(bad, b, 64.0, repo, oid)


def test_above_tier_local_launch_refuses_pointing_to_protected_workflow(tmp_path, monkeypatch):
    """Codex review #23 finding 1: the in-repo allowed-signers file was
    forgeable (reproduced), so above-tier launches move to the GitHub
    protected-environment workflow — the owner's approval lives where no
    repository writer can rewrite it. Locally they refuse always."""
    import json as _json
    import subprocess
    import sys
    monkeypatch.delenv("MEDZEN_EXECUTOR", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    root = Path(__file__).resolve().parents[1]
    b = _json.loads((root / "platform/manifests/"
                     "B5-UNIVERSAL-ARM1-SAGEMAKER-BINDINGS-2026-005.json"
                     ).read_bytes())
    request = render_request(b)
    bp = tmp_path / "b.json"; bp.write_text(_json.dumps(b))
    rp = tmp_path / "r.json"; rp.write_text(_json.dumps(request))
    proc = subprocess.run(
        [sys.executable, str(root / "scripts/b5_sagemaker_job.py"),
         "launch", "--bindings", str(bp), "--request", str(rp)],
        capture_output=True, text=True, cwd=root)
    assert proc.returncode == 2, proc.stdout
    assert "protected" in proc.stdout and "workflow" in proc.stdout

def test_derived_summary_corruption_refuses(tmp_path):
    """Codex review #24: committed_plus_reserved, headroom and the
    active-reservation COUNT were not recomputed — in-memory corruption
    passed. All derived fields are now pure functions of rows."""
    import json as _json
    import hashlib as h
    from b5_sagemaker_job import (JobRefusal as JR,
                                  canonical_bindings_sha256,
                                  verify_active_reservation)
    b = bindings()
    repo = _mini_repo(tmp_path)
    (repo / "platform/finance").mkdir(parents=True)
    reg_path = repo / "platform/finance/REG.json"

    def registry(**summary_over):
        summary = {"aggregate_ceiling_usd": 800.0,
                   "recognized_committed_guardrail_usd": 500.0,
                   "active_reservations_usd": 70.0,
                   "committed_plus_reserved_usd": 570.0,
                   "guardrail_headroom_after_reservations_usd": 230.0}
        summary.update(summary_over)
        return {"allocations": [
            {"allocation_id": "OLD", "financial_state": "CLOSED",
             "recognized_committed_usd": 500.0},
            {"allocation_id": "ARM-1",
             "financial_state": "ACTIVE_RESERVED",
             "reservation_usd": 70.0,
             "reservation_expires_utc": "2027-01-01T00:00:00Z",
             "packet_bindings_sha256": canonical_bindings_sha256(b)}],
            "controls": {"current_active_billable_reservations": "1"},
            "guardrail_summary": summary}

    def commit_reg(reg):
        reg_path.write_text(_json.dumps(reg))
        _commit(repo)
        return {"file": "platform/finance/REG.json",
                "sha256": h.sha256(reg_path.read_bytes()).hexdigest(),
                "allocation_id": "ARM-1"}

    verify_active_reservation(commit_reg(registry()), b, 64.0, repo)
    for corruption in ({"committed_plus_reserved_usd": 500.0},
                        {"guardrail_headroom_after_reservations_usd": 700.0}):
        with pytest.raises(JR, match="does not match the recompute"):
            verify_active_reservation(commit_reg(registry(**corruption)),
                                      b, 64.0, repo)
    bad = registry()
    bad["controls"]["current_active_billable_reservations"] = "0"
    with pytest.raises(JR, match="count is a derived field"):
        verify_active_reservation(commit_reg(bad), b, 64.0, repo)


def test_forged_executor_env_vars_do_not_skip_committed_gates(tmp_path, monkeypatch):
    """Codex review #24 finding 1: MEDZEN_EXECUTOR/GITHUB_ACTIONS can be
    forged locally — reproduced. Forging them must merely move the
    refusal to the NEXT committed gate (review), never to a launch; and
    the refusal text must not overclaim impossibility. The REAL local
    closure is the owner-applied permissions boundary
    (LOCAL-BOUNDARY-RUNBOOK.md); the medzen-tier tag serves the ARM
    ROLE's RequestTag condition, not a local lock."""
    import json as _json
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[1]
    b = _json.loads((root / "platform/manifests/"
                     "B5-UNIVERSAL-ARM1-SAGEMAKER-BINDINGS-2026-005.json"
                     ).read_bytes())
    request = render_request(b)
    tier = [t["Value"] for t in request["Tags"]
            if t["Key"] == "medzen-tier"]
    assert tier == ["arm"], "arm requests must carry the boundary tag"
    bp = tmp_path / "b.json"; bp.write_text(_json.dumps(b))
    rp = tmp_path / "r.json"; rp.write_text(_json.dumps(request))
    env = dict(__import__("os").environ,
               MEDZEN_EXECUTOR="github-protected-workflow",
               GITHUB_ACTIONS="true")
    proc = subprocess.run(
        [sys.executable, str(root / "scripts/b5_sagemaker_job.py"),
         "launch", "--bindings", str(bp), "--request", str(rp)],
        capture_output=True, text=True, cwd=root, env=env)
    assert proc.returncode == 2
    assert "PENDING_INDEPENDENT_REVIEW" in proc.stdout, (
        "forged executor vars must land on the next committed gate")
    # the tier tag now serves the ARM ROLE's RequestTag condition
    # (Codex review #25: a caller-controlled tag cannot LOCK the local
    # path — that closure is the owner-applied permissions boundary in
    # platform/iam/LOCAL-BOUNDARY-RUNBOOK.md)
    role = _json.loads((root / "platform/iam/"
                        "medzen-arm-launch-role.json").read_bytes())
    conditions = [s.get("Condition", {}) for s in role["Statement"]
                  if "sagemaker:CreateTrainingJob" in str(s.get("Action"))]
    assert any("aws:RequestTag/medzen-tier" in str(c) for c in conditions)


def test_arm_launch_workflow_is_hardened():
    """Reviews #24-#25 + owner fast-path packet: protected caller with
    NO inputs and NO credentials; a reusable credential-bearing exec
    workflow (the job_workflow_ref target) whose typed input appears
    only in `if:` conditions; canaries for both trust directions; no
    expressions inside any run block; pinned deps; master guard."""
    root = Path(__file__).resolve().parents[1]
    wf = root / ".github/workflows"
    caller = (wf / "arm-launch.yml").read_text()
    exec_body = (wf / "arm-launch-exec.yml").read_text()
    pos = (wf / "arm-launch-canary.yml").read_text()
    neg = (wf / "arm-launch-canary-unauthorized.yml").read_text()

    assert "workflow_dispatch: {}" in caller
    assert "${{ inputs" not in caller, "caller must reference no inputs"
    assert "configure-aws-credentials" not in caller, "caller carries no creds"
    assert "uses: ./.github/workflows/arm-launch-exec.yml" in caller
    assert "concurrency:" in caller

    assert "workflow_call:" in exec_body
    assert "environment: arm-launch-approval" in exec_body
    assert "github.ref == 'refs/heads/master'" in exec_body
    assert "MEDZEN_ARM_LAUNCH_ROLE_ARN" in exec_body
    assert "MEDZEN_CI_ROLE_ARN" not in exec_body
    assert "boto3==" in exec_body and "torch==" in exec_body
    for body, name in ((caller, "caller"), (exec_body, "exec"),
                        (pos, "canary"), (neg, "neg-canary")):
        for step_body in body.split("run: |")[1:]:
            block = step_body.split("\n      - ")[0]
            assert "${{" not in block, (
                f"{name}: expression inside a run block is injection "
                f"surface: {block[:80]}")
    assert "uses: ./.github/workflows/arm-launch-exec.yml" in pos
    assert "mode: canary" in pos
    # the negative canary must use the SAME environment but NOT the exec
    # workflow, and must only pass when assumption fails
    assert "environment: arm-launch-approval" in neg
    assert "arm-launch-exec.yml" not in neg
    assert "continue-on-error: true" in neg
    assert "ASSUME_OUTCOME" in neg

    # Codex review #26: deps must install BEFORE credential acquisition
    assert exec_body.index("install pinned") < exec_body.index(
        "configure-aws-credentials")
    # the wrong-ref negative canary is a REAL reusable workflow, so the
    # token carries an actual (wrong) job_workflow_ref
    wr_exec = (wf / "arm-launch-canary-wrongref-exec.yml").read_text()
    wr_caller = (wf / "arm-launch-canary-wrongref.yml").read_text()
    assert "workflow_call" in wr_exec
    assert "environment: arm-launch-approval" in wr_exec
    assert "continue-on-error: true" in wr_exec and "ASSUME_OUTCOME" in wr_exec
    assert "arm-launch-canary-wrongref-exec.yml" in wr_caller
    # IAM policy: non-deprecated key, mandatory presence checks, exact job
    import json as _json
    policy = _json.loads((root / "platform/iam/"
                          "medzen-arm-launch-role.json").read_text())
    create = next(s for s in policy["Statement"]
                  if s["Sid"] == "CreateArmTierB5JobsUnderHardConditions")
    assert "sagemaker:OutputKmsKeyArn" in str(create["Condition"])
    assert "sagemaker:OutputKmsKey\"" not in _json.dumps(create), (
        "deprecated OutputKmsKey key silently implicit-denies (Codex #26)")
    assert set(create["Condition"]["Null"]) == {
        "sagemaker:InstanceTypes", "sagemaker:VpcSubnets",
        "sagemaker:VpcSecurityGroupIds"}, (
        "ForAllValues without Null:false passes on ABSENT keys")
    assert create["Resource"].endswith(
        "training-job/medzen-b5-b5-universal-arm1-2026-005")
    # arm-control tests run in CI
    ci = (root / ".github/workflows/architecture-controls.yml").read_text()
    assert "test_b5_sagemaker_job.py" in ci
    tf = (root / "infra/iam.tf").read_text()
    assert "environment:arm-launch-approval" in tf
    assert "arm-launch-exec.yml@refs/heads/master" in tf, (
        "trust must bind the REUSABLE credential-bearing workflow")
    assert "arm_launch_enabled" in tf and "b7_ci_enabled" in tf, (
        "arm activation and legacy CI must be independently switched")
    assert 'data "aws_iam_openid_connect_provider" "github_existing"' in tf
    assert "refs/heads/main" not in tf
    assert "precondition" in tf and 'fotso94/medzen-platform"' in tf, (
        "activation must refuse a wrong github_repo (Codex review #26)")
def test_above_tier_requires_the_dedicated_role_identity():
    """Codex review #25 finding 5: only the account number was checked —
    forged executor vars under local credentials passed the identity
    gate. Above-tier callers must BE the arm-launch role."""
    from b5_sagemaker_job import JobRefusal as JR, assert_launch_identity
    good = ("arn:aws:sts::558069890522:assumed-role/"
            "medzen-arm-launch-role/GitHubActions")
    assert_launch_identity(good, above_tier=True)
    assert_launch_identity("arn:aws:iam::558069890522:user/local",
                           above_tier=False)   # calibrations unchanged
    for arn in ("arn:aws:iam::558069890522:user/local",
                 "arn:aws:sts::558069890522:assumed-role/medzen-ci-role/x",
                 "", None):
        with pytest.raises(JR, match="dedicated role|arm-launch"):
            assert_launch_identity(arn, above_tier=True)


def test_iam_policy_artifacts_are_valid_and_honest():
    """Codex review #25 finding 2: the boundary policy carried an
    invalid __comment element and a caller-controlled tag condition.
    Policies must be valid JSON with no non-schema keys; the tag-based
    local 'lock' is gone, replaced by the owner runbook."""
    import json as _json
    root = Path(__file__).resolve().parents[1]
    assert not (root / "platform/iam/"
                "medzen-local-boundary-policy.json").exists(), (
        "the caller-controlled tag deny was theater — removed")
    runbook = (root / "platform/iam/LOCAL-BOUNDARY-RUNBOOK.md").read_text()
    assert "permissions boundary" in runbook.lower()
    assert "separate owner-controlled admin principal" in runbook.lower()
    for policy_path in sorted((root / "platform/iam").glob("*.json")):
        doc = _json.loads(policy_path.read_text())
        assert set(doc) <= {"Version", "Statement", "Id"}, (
            f"{policy_path.name}: non-schema top-level keys break "
            "Access Analyzer (Codex review #25: __comment was invalid)")
    arm = _json.loads((root / "platform/iam/"
                       "medzen-arm-launch-role.json").read_text())
    body = _json.dumps(arm)
    assert "b5-universal-ftcal-2026-004/output" in body, (
        "S3 read must be the exact calibration artifact prefix")
    assert "sagemaker:InstanceTypes" in body
    assert "research/b5-training/*" not in body, "no broad S3 prefixes"
