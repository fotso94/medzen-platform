"""SEALED-EVALUATOR-SPEC-2026-001 item 9: a complete successful-launch
rehearsal against a stubbed AWS session — packet load, owner authorization,
budget, durable acquisition ordering, composition and launch ordering — plus
the refusal paths. No AWS, no network, no ledger writes."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "services/model-loader"))

import sealed_evaluator  # noqa: E402


KMS = "arn:aws:kms:eu-central-1:558069890522:key/9c336116-c648-4548-95c6-1b926478ae57"
ROLE = "arn:aws:iam::558069890522:role/medzen-sealed-evaluator-role"
IMAGE = ("558069890522.dkr.ecr.eu-central-1.amazonaws.com/"
         "medzen-trainer-omniasr@sha256:" + "a" * 64)
LANGS = ("english", "ewe", "french", "kinyarwanda", "lingala", "pidgin",
         "swahili")


def _channels() -> dict:
    base = {"s3_data_type": "ManifestFile", "input_mode": "File",
            "s3_data_distribution_type": "FullyReplicated",
            "compression_type": "None", "content_type": None}
    chans = {
        "manifests": dict(base, s3_uri="s3://medzen-speech/research/sealed/x/manifests.manifest.json"),
        "audio": dict(base, s3_uri="s3://medzen-speech/research/sealed/x/audio.manifest.json"),
        "base": dict(base, s3_data_type="S3Prefix",
                     s3_uri="s3://medzen-speech/research/asr-base-model/pilot/x/bundles/omniASR-CTC-1B-v2.pt.parts/"),
        "tokenizer": dict(base, s3_data_type="S3Prefix",
                          s3_uri="s3://medzen-speech/research/asr-base-model/pilot/x/bundles/omniASR_tokenizer_written_v2.model.parts/"),
        "arm1": dict(base, s3_data_type="S3Prefix",
                     s3_uri="s3://medzen-speech/research/b5-training/arm1-gpu-resmoke-2026-001/staging/model.pt"),
    }
    return chans


def _packet() -> dict:
    return {
        "record": "SEALED-EVAL-TEST-PACKET",
        "owner_authorization_record":
            "platform/decisions/TEST-SEALED-AUTH.json",
        "cost_ceiling_usd": 5.0,
        "anchor": {"bucket": "medzen-speech",
                   "key": "research/sealed/x/packet.json"},
        "channel_inputs_prefix": "research/sealed/x",
        "environment": {"MEDZEN_SEALED_JOB_NAME": "medzen-sealed-test-001"},
        "languages": {
            lang: {"holdout_key": f"eval/{lang}/asr/pool-sealed/manifest.jsonl",
                   "holdout_manifest_sha256": hashlib.sha256(
                       lang.encode()).hexdigest(),
                   "holdout_s3_version_id": f"vid-{lang}"}
            for lang in LANGS},
        "sealed_run_contract": {
            "job_name": "medzen-sealed-test-001",
            "image_digest": IMAGE,
            "instance_type": "ml.g5.xlarge",
            "channels": _channels(),
            "output_s3_prefix":
                "s3://medzen-sealed-results/medzen-sealed-test-001/output",
            "output_kms_key_arn": KMS,
            "account_id": "558069890522",
            "region": "eu-central-1",
            "execution_role_arn": ROLE,
            "network_isolation": True,
            "volume_kms_key_arn": KMS,
            "hyperparameters_sha256": hashlib.sha256(b"{}").hexdigest(),
            "instance_count": 1,
            "volume_size_gb": 100,
            "max_runtime_seconds": 7200,
            "vpc_config": "none",
            "environment_sha256": "e" * 64,
            "checkpoint_config": "none",
        },
    }


def _manifest_line(lang: str) -> bytes:
    row = {"audio_checksum_sha256": "c" * 64, "speaker_id": "spk1",
           "text_normalized": "SECRET REFERENCE",
           "audio_s3_uri": f"s3://medzen-speech/eval/{lang}/audio/x.wav"}
    return (json.dumps(row) + "\n").encode()


class StubSession:
    """Records every AWS call in order; serves sealed manifests whose
    sha256 the packet pins."""

    def __init__(self, packet: dict, *, existing_job=None):
        self.calls: list[tuple[str, str]] = []
        self.packet = packet
        self.existing_job = existing_job
        self.put_bodies: dict[str, bytes] = {}
        # make the packet's manifest pins match the served bytes
        for lang in packet["languages"]:
            body = _manifest_line(lang)
            packet["languages"][lang]["holdout_manifest_sha256"] = (
                hashlib.sha256(body).hexdigest())

    def client(self, name):
        session = self

        class _Client:
            def get_caller_identity(self):
                session.calls.append(("sts", "identity"))
                return {"Account": "558069890522"}

            def put_object(self, **kw):
                session.calls.append(("s3.put", kw["Key"]))
                session.put_bodies[kw["Key"]] = kw["Body"]
                return {"VersionId": "anchor-vid"}

            def get_object(self, **kw):
                session.calls.append(("s3.get", kw["Key"]))
                lang = kw["Key"].split("/")[1]

                class _Body:
                    def read(_self):
                        return _manifest_line(lang)
                return {"Body": _Body()}

            def describe_processing_job(self, **kw):
                session.calls.append(("sm.describe", kw["ProcessingJobName"]))
                if session.existing_job is None:
                    raise RuntimeError("not found")
                return session.existing_job

            def create_processing_job(self, **kw):
                session.calls.append(("sm.create", kw["ProcessingJobName"]))
                session.create_request = kw
                return {"ProcessingJobArn": "arn:aws:sagemaker:job/x"}
        return _Client()


@pytest.fixture()
def rig(monkeypatch, tmp_path):
    packet = _packet()
    session = StubSession(packet)
    raw_holder = {}

    def fake_head_bytes(rel: str) -> bytes:
        if rel == "platform/decisions/TEST-SEALED-AUTH.json":
            return json.dumps({"statement": (
                "owner authorizes sealed evaluation under packet sha256 "
                + raw_holder["sha"])}).encode()
        if rel == "packet.json":
            return raw_holder["raw"]
        raise sealed_evaluator.SealedLaunchRefusal(f"uncommitted {rel}")

    def finalize():
        raw = json.dumps(packet, sort_keys=True).encode()
        raw_holder["raw"] = raw
        raw_holder["sha"] = hashlib.sha256(raw).hexdigest()
        return raw_holder["sha"]

    monkeypatch.setattr(sealed_evaluator, "_head_bytes", fake_head_bytes)
    consumed = []

    def fake_consume(pkt, job):
        consumed.append(("consume", job, len(session.calls)))

    return {"packet": packet, "session": session, "finalize": finalize,
            "consumed": consumed, "tmp": tmp_path,
            "consume": fake_consume}


def test_rehearsal_full_success_ordering(rig):
    sha = rig["finalize"]()
    rc = sealed_evaluator.main(
        ["--packet", "packet.json", "--packet-sha256", sha,
         "--out", str(rig["tmp"] / "env.json")],
        session=rig["session"], consume=rig["consume"])
    assert rc == 0
    calls = rig["session"].calls
    kinds = [k for k, _ in calls]
    # identity precedes everything AWS-mutating
    assert kinds[0] == "sts"
    # anchor put happens BEFORE any sealed manifest read
    anchor_at = kinds.index("s3.put")
    first_get = kinds.index("s3.get")
    assert anchor_at < first_get
    # consumption happens after the anchor and BEFORE the first sealed read
    (label, job, at_call_count) = rig["consumed"][0]
    assert job == "medzen-sealed-test-001"
    assert at_call_count <= first_get
    # describe precedes create (idempotency), create is last
    assert kinds.index("sm.describe") < kinds.index("sm.create")
    assert kinds[-1] == "sm.create"
    # the composed request is network-isolated with the packet's exact pins
    request = rig["session"].create_request
    assert request["NetworkConfig"] == {"EnableNetworkIsolation": True}
    assert request["AppSpecification"]["ImageUri"] == IMAGE
    assert request["RoleArn"] == ROLE
    assert request["StoppingCondition"]["MaxRuntimeInSeconds"] == 7200
    assert request["ProcessingOutputConfig"]["KmsKeyId"] == KMS
    names = {i["InputName"] for i in request["ProcessingInputs"]}
    assert names == {"manifests", "audio", "base", "tokenizer", "arm1"}
    # the audio channel manifest was composed from sealed rows' audio keys
    audio_key = "research/sealed/x/audio.manifest.json"
    body = rig["session"].put_bodies[audio_key].decode()
    assert "eval/english/audio/x.wav" in body
    # and reference text NEVER leaks into anything the launcher writes
    for key, body in rig["session"].put_bodies.items():
        assert b"SECRET REFERENCE" not in (
            body if isinstance(body, bytes) else body.encode()), key
    env = json.loads((rig["tmp"] / "env.json").read_text())
    assert env["packet_sha256"] == sha
    assert env["storage"]["version_id"] == "anchor-vid"


def test_wrong_packet_sha_refuses(rig):
    rig["finalize"]()
    with pytest.raises(sealed_evaluator.SealedLaunchRefusal,
                       match="refusing"):
        sealed_evaluator.main(
            ["--packet", "packet.json", "--packet-sha256", "f" * 64],
            session=rig["session"], consume=rig["consume"])


def test_missing_owner_quote_refuses(rig, monkeypatch):
    sha = rig["finalize"]()
    real = sealed_evaluator._head_bytes

    def head(rel):
        if rel == "platform/decisions/TEST-SEALED-AUTH.json":
            return json.dumps({"statement": "owner said nothing"}).encode()
        return real(rel)
    monkeypatch.setattr(sealed_evaluator, "_head_bytes", head)
    with pytest.raises(sealed_evaluator.SealedLaunchRefusal,
                       match="committed is not"):
        sealed_evaluator.main(
            ["--packet", "packet.json", "--packet-sha256", sha],
            session=rig["session"], consume=rig["consume"])


def test_budget_ceiling_refuses(rig):
    rig["packet"]["cost_ceiling_usd"] = 1.0   # worst = 1.6*7200/3600 = 3.2
    sha = rig["finalize"]()
    with pytest.raises(sealed_evaluator.SealedLaunchRefusal,
                       match="exceeds the packet ceiling"):
        sealed_evaluator.main(
            ["--packet", "packet.json", "--packet-sha256", sha],
            session=rig["session"], consume=rig["consume"])


def test_incomplete_contract_refuses(rig):
    del rig["packet"]["sealed_run_contract"]["network_isolation"]
    sha = rig["finalize"]()
    with pytest.raises(Exception, match="PREDECLARE the COMPLETE"):
        sealed_evaluator.main(
            ["--packet", "packet.json", "--packet-sha256", sha],
            session=rig["session"], consume=rig["consume"])


def test_isolation_false_refuses(rig):
    rig["packet"]["sealed_run_contract"]["network_isolation"] = False
    sha = rig["finalize"]()
    with pytest.raises(Exception, match="REQUIRE network isolation"):
        sealed_evaluator.main(
            ["--packet", "packet.json", "--packet-sha256", sha],
            session=rig["session"], consume=rig["consume"])


def test_existing_job_with_different_image_refuses(rig):
    sha = rig["finalize"]()
    rig["session"].existing_job = {
        "AppSpecification": {"ImageUri": "other@sha256:" + "b" * 64},
        "ProcessingJobStatus": "Completed"}
    with pytest.raises(sealed_evaluator.SealedLaunchRefusal,
                       match="DIFFERENT image"):
        sealed_evaluator.main(
            ["--packet", "packet.json", "--packet-sha256", sha],
            session=rig["session"], consume=rig["consume"])


def test_existing_matching_job_is_adopted_not_recreated(rig):
    sha = rig["finalize"]()
    rig["session"].existing_job = {
        "AppSpecification": {"ImageUri": IMAGE},
        "ProcessingJobStatus": "InProgress",
        "ProcessingJobArn": "arn:aws:sagemaker:job/existing"}
    rc = sealed_evaluator.main(
        ["--packet", "packet.json", "--packet-sha256", sha,
         "--out", str(rig["tmp"] / "env.json")],
        session=rig["session"], consume=rig["consume"])
    assert rc == 0
    kinds = [k for k, _ in rig["session"].calls]
    assert "sm.create" not in kinds
    env = json.loads((rig["tmp"] / "env.json").read_text())
    assert env["launch"]["adopted"] is True


# --------------------------------------------------------------------------
# REAL-PACKET container regression (Codex sealed-review finding 2: the
# launcher rehearsals missed a container-side tree bug). These pin the
# CONTAINER's tree recomputation against the COMMITTED packet's own pins —
# the exact refusal the first implementation would have died on.
# --------------------------------------------------------------------------

REAL_PACKET = ROOT / "platform/decisions/SEALED-EVAL-ARM1-PACKET-2026-001.json"


@pytest.mark.skipif(not REAL_PACKET.is_file(),
                    reason="real sealed packet not authored yet")
def test_container_tree_recomputes_from_the_real_packet_pins():
    from pipeline.sealed_eval import artifact_tree
    packet = json.loads(REAL_PACKET.read_bytes())
    env = packet["environment"]
    assert artifact_tree(env["MEDZEN_SEALED_ARM1_SHA256"],
                         env["MEDZEN_SEALED_TOKENIZER_SHA256"]) == \
        env["MEDZEN_SEALED_ARTIFACT_TREE"], (
        "the container's tree recomputation does not reproduce the packet's "
        "candidate tree — the sealed job would refuse before decoding")
    assert packet["candidate_digest"] == \
        "sha256:" + env["MEDZEN_SEALED_ARTIFACT_TREE"]


@pytest.mark.skipif(not REAL_PACKET.is_file(),
                    reason="real sealed packet not authored yet")
def test_container_tree_over_base_sha_is_the_codex_bug():
    from pipeline.sealed_eval import artifact_tree
    packet = json.loads(REAL_PACKET.read_bytes())
    env = packet["environment"]
    assert artifact_tree(env["MEDZEN_SEALED_BASE_SHA256"],
                         env["MEDZEN_SEALED_TOKENIZER_SHA256"]) != \
        env["MEDZEN_SEALED_ARTIFACT_TREE"], (
        "regression guard: a tree computed over the BASE checkpoint must "
        "never equal the candidate tree (Codex sealed-review finding 1)")
