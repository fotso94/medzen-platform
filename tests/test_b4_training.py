"""B4 trainer invariants: EC2 credentials, GPU gating, spot recovery, pins.

Network tests are marked `net` and deselected by default.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TRAIN = ROOT / "pipeline" / "train_asr.py"
TRACK = ROOT / "pipeline" / "tracking.py"
REQ = ROOT / "requirements.txt"


# --------------------------------------------------------------------------- #
# #1 credentials must work on EC2
# --------------------------------------------------------------------------- #
def test_no_hardcoded_profile_in_aws_calls():
    """boto3.Session(profile_name=...) and `aws --profile` both fail on EC2,
    where credentials come from the instance profile."""
    src = TRAIN.read_text()
    assert '"--profile"' not in src, "aws CLI --profile fails on EC2"
    assert "profile_name=PROFILE" not in src, "hardcoded profile fails on EC2"
    assert "def boto_session" in src, "must go through the shared session helper"


def test_session_falls_back_to_instance_role(monkeypatch):
    from pipeline.train_asr import boto_session
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("MEDZEN_FORCE_PROFILE", raising=False)
    s = boto_session()
    assert s.profile_name in ("default", None), \
        f"without AWS_PROFILE the session must not pin a named profile, got {s.profile_name}"


# --------------------------------------------------------------------------- #
# #2 mlx-whisper is Apple-silicon only
# --------------------------------------------------------------------------- #
def test_mlx_whisper_is_darwin_only():
    line = [l for l in REQ.read_text().splitlines() if l.startswith("mlx-whisper")]
    assert line, "mlx-whisper must be pinned"
    assert 'sys_platform == "darwin"' in line[0], \
        "mlx-whisper is not installable on the Linux trainer; needs an env marker"


def test_training_stack_is_pinned():
    text = REQ.read_text()
    for pkg in ("torch", "transformers", "peft", "accelerate", "mlflow"):
        assert f"\n{pkg}==" in text, f"{pkg} is not pinned"


# --------------------------------------------------------------------------- #
# #3 non-smoke must refuse to run without CUDA
# --------------------------------------------------------------------------- #
def test_non_smoke_refuses_without_cuda():
    """On CPU/MPS a real run appears to work and takes days, silently burning
    the instance. Only --smoke may run off-GPU."""
    src = TRAIN.read_text()
    assert "REFUSING: non-smoke training requires CUDA" in src
    assert "MEDZEN_ALLOW_NO_CUDA" in src, "there must be a deliberate override"


@pytest.mark.net
def test_non_smoke_actually_exits_on_this_machine():
    env = {**os.environ, "AWS_PROFILE": "medzen"}
    env.pop("MEDZEN_ALLOW_NO_CUDA", None)
    p = subprocess.run([sys.executable, "-m", "pipeline.train_asr",
                        "--max-steps", "1", "--languages", "pidgin"],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=900)
    assert p.returncode != 0, "should have refused to train off-GPU"
    assert "requires CUDA" in (p.stdout + p.stderr)


# --------------------------------------------------------------------------- #
# #4 spot recovery
# --------------------------------------------------------------------------- #
def test_checkpoints_upload_on_save_not_at_the_end():
    """Spot reclaim gives ~2 minutes' notice; anything uploaded only after
    training completes is lost."""
    src = TRAIN.read_text()
    assert "def on_save" in src, "must upload from the save hook"
    assert "make_upload_callback" in src
    assert "callbacks=callbacks" in src, "callback must actually be attached"


def test_resume_accepts_s3_and_auto():
    src = TRAIN.read_text()
    assert 'resume == "auto"' in src
    assert "sync_down(resume" in src, "s3:// resume must download first"
    assert "def latest_checkpoint" in src


def test_latest_checkpoint_sorts_numerically_not_lexically():
    """checkpoint-9 must not beat checkpoint-100."""
    src = TRAIN.read_text()
    assert 'int(c.split("-")[1])' in src, "lexical sort would pick checkpoint-9"


@pytest.mark.net
def test_s3_checkpoint_round_trip():
    import hashlib
    import shutil

    from pipeline.train_asr import BUCKET, latest_checkpoint, s3, sync_down, sync_up
    run = "pytest-roundtrip"
    prefix = f"s3://{BUCKET}/candidates/asr/{run}"
    local = Path("/tmp/pytest_rt/checkpoint-7")
    if local.parent.exists():
        shutil.rmtree(local.parent)
    (local / "nested").mkdir(parents=True)
    (local / "adapter.safetensors").write_bytes(b"W" * 4096)
    (local / "nested" / "opt.pt").write_bytes(b"O" * 2048)
    before = {f.relative_to(local).as_posix(): hashlib.sha256(f.read_bytes()).hexdigest()
              for f in local.rglob("*") if f.is_file()}

    sync_up(local, f"{prefix}/checkpoint-7")
    shutil.rmtree(local.parent)                       # instance disappears
    found = latest_checkpoint(prefix)
    assert found.endswith("checkpoint-7")
    back = sync_down(found, Path("/tmp/pytest_rt_restored"))
    after = {f.relative_to(back).as_posix(): hashlib.sha256(f.read_bytes()).hexdigest()
             for f in back.rglob("*") if f.is_file()}
    assert before == after, "checkpoint did not survive the round trip"

    cli = s3()
    objs = cli.list_objects_v2(Bucket=BUCKET,
                               Prefix=f"candidates/asr/{run}/").get("Contents", [])
    if objs:
        cli.delete_objects(Bucket=BUCKET,
                           Delete={"Objects": [{"Key": o["Key"]} for o in objs]})
    shutil.rmtree("/tmp/pytest_rt_restored", ignore_errors=True)


# --------------------------------------------------------------------------- #
# #5 tracking DB must be per-run
# --------------------------------------------------------------------------- #
def test_tracking_db_key_is_run_specific():
    """A shared 'latest' key is a lost-update race and makes per-run recovery
    impossible."""
    src = TRACK.read_text()
    assert 'f"mlflow/db/{rid}/mlflow.db"' in src
    # check CODE, not the comment that explains why "latest" is wrong
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    assert "'latest'" not in code and '"latest"' not in code, \
        "a shared latest key races between concurrent runs"


def test_mlflow_artifacts_default_to_s3():
    src = TRACK.read_text()
    assert 's3://{BUCKET}/mlflow/artifacts' in src, \
        "local artifacts die with a spot instance"


# --------------------------------------------------------------------------- #
def test_base_model_revision_is_pinned_and_passed():
    src = TRAIN.read_text()
    assert "BASE_REVISION" in src
    import re
    m = re.search(r'BASE_REVISION = os\.environ\.get\("BASE_REVISION", "([0-9a-f]{40})"\)', src)
    assert m, "base revision must default to a 40-char commit SHA"
    # The pin is now applied through base_model_source(), which either passes
    # revision= to the Hub or verifies the cache's MANIFEST revision. Both
    # from_pretrained calls must go through it -- one bypassing it would load
    # unpinned weights while the run still reported the SHA.
    assert "src = base_model_source(a.base_model, rev)" in src
    assert src.count("**src.kwargs") == 2, "revision must reach model AND processor"
    assert "from_pretrained(src.path" in src


# --------------------------------------------------------------------------- #
# final/ must not carry nested checkpoints
# --------------------------------------------------------------------------- #
def test_final_upload_excludes_nested_checkpoints(tmp_path):
    """The adapter is saved into the Trainer's own output_dir, so an unfiltered
    sync copies every retained checkpoint inside final/. Observed in the B4
    preflight: final/ contained a full duplicate of checkpoint-3. At 600 steps
    with --save-steps 100 that is over a gigabyte of redundant upload, and it
    makes final/ ambiguous for anything loading the adapter.
    """
    from unittest.mock import MagicMock, patch
    from pipeline.train_asr import sync_up

    out = tmp_path / "asr-lora"
    (out / "checkpoint-3").mkdir(parents=True)
    (out / "adapter_model.safetensors").write_text("w")
    (out / "run.json").write_text("{}")
    (out / "checkpoint-3" / "optimizer.pt").write_text("o")

    cli = MagicMock()
    with patch("pipeline.train_asr.s3", return_value=cli):
        sync_up(out, "s3://b/candidates/asr/RID/final", skip_checkpoints=True)
    keys = sorted(c.args[2] for c in cli.upload_file.call_args_list)
    assert not [k for k in keys if "checkpoint-" in k], f"leaked checkpoints: {keys}"
    assert "candidates/asr/RID/final/adapter_model.safetensors" in keys
    assert "candidates/asr/RID/final/run.json" in keys


def test_checkpoint_upload_still_complete(tmp_path):
    """A checkpoint must upload in full -- a partial one cannot be resumed."""
    from unittest.mock import MagicMock, patch
    from pipeline.train_asr import sync_up

    ck = tmp_path / "checkpoint-3"
    (ck / "sub").mkdir(parents=True)
    (ck / "optimizer.pt").write_text("o")
    (ck / "sub" / "extra.bin").write_text("e")

    cli = MagicMock()
    with patch("pipeline.train_asr.s3", return_value=cli):
        sync_up(ck, "s3://b/candidates/asr/RID/checkpoint-3")
    keys = sorted(c.args[2] for c in cli.upload_file.call_args_list)
    assert len(keys) == 2, keys


def test_final_sync_call_uses_the_exclusion():
    src = TRAIN.read_text()
    assert "sync_up(a.out, dest, skip_checkpoints=True)" in src, \
        "the final upload must exclude nested checkpoints"


# --------------------------------------------------------------------------- #
# base model cache: the revision must be ENFORCED, not just recorded
# --------------------------------------------------------------------------- #
REV = "06f233fe06e710322aca913c1bc4249a0d71fce1"


def _manifest(rev=REV, files=("config.json", "model.safetensors")):
    return {"repo": "openai/whisper-large-v3", "revision": rev,
            "files": {f: {"sha256": "x" * 64, "bytes": 1} for f in files}}


def test_cache_disabled_falls_back_to_hub_with_revision(monkeypatch):
    from pipeline.train_asr import base_model_source
    monkeypatch.setenv("MEDZEN_NO_MODEL_CACHE", "1")
    src = base_model_source("openai/whisper-large-v3", REV)
    assert src.path == "openai/whisper-large-v3"
    assert src.kwargs == {"revision": REV}, "the Hub path must still enforce the pin"


def test_cache_miss_falls_back_to_hub(monkeypatch):
    from unittest.mock import MagicMock, patch
    import pipeline.train_asr as T
    monkeypatch.delenv("MEDZEN_NO_MODEL_CACHE", raising=False)
    cli = MagicMock()
    cli.get_object.side_effect = RuntimeError("NoSuchKey")
    with patch.object(T, "s3", return_value=cli):
        src = T.base_model_source("openai/whisper-large-v3", REV)
    assert src.kwargs == {"revision": REV}


def test_cache_with_wrong_revision_is_refused(monkeypatch):
    """A local directory ignores revision=, so a mismatched cache would train on
    different weights while the run reported the pinned SHA."""
    from unittest.mock import MagicMock, patch
    import pipeline.train_asr as T
    monkeypatch.delenv("MEDZEN_NO_MODEL_CACHE", raising=False)
    cli = MagicMock()
    cli.get_object.return_value = {
        "Body": MagicMock(read=lambda: json.dumps(_manifest(rev="dead" * 10)).encode())}
    with patch.object(T, "s3", return_value=cli):
        with pytest.raises(SystemExit) as e:
            T.base_model_source("openai/whisper-large-v3", REV)
    assert "REFUSING" in str(e.value) and REV in str(e.value)


def test_cache_hit_loads_locally_without_a_meaningless_revision(monkeypatch, tmp_path):
    from unittest.mock import MagicMock, patch
    import pipeline.train_asr as T
    monkeypatch.delenv("MEDZEN_NO_MODEL_CACHE", raising=False)
    monkeypatch.setenv("MEDZEN_MODEL_DIR", str(tmp_path))
    local = tmp_path / "whisper-large-v3" / REV
    local.mkdir(parents=True)
    for f in ("MANIFEST.json", "config.json", "model.safetensors"):
        (local / f).write_text("x")
    cli = MagicMock()
    cli.get_object.return_value = {
        "Body": MagicMock(read=lambda: json.dumps(_manifest()).encode())}
    with patch.object(T, "s3", return_value=cli):
        src = T.base_model_source("openai/whisper-large-v3", REV)
    assert src.path == str(local)
    assert src.kwargs == {}, "revision= on a local dir is silently ignored; do not pass it"


def test_incomplete_cache_is_refused(monkeypatch, tmp_path):
    from unittest.mock import MagicMock, patch
    import pipeline.train_asr as T
    monkeypatch.delenv("MEDZEN_NO_MODEL_CACHE", raising=False)
    monkeypatch.setenv("MEDZEN_MODEL_DIR", str(tmp_path))
    local = tmp_path / "whisper-large-v3" / REV
    local.mkdir(parents=True)
    (local / "MANIFEST.json").write_text("x")     # present, so no re-download
    (local / "config.json").write_text("x")       # model.safetensors missing
    cli = MagicMock()
    cli.get_object.return_value = {
        "Body": MagicMock(read=lambda: json.dumps(_manifest()).encode())}
    with patch.object(T, "s3", return_value=cli):
        with pytest.raises(SystemExit) as e:
            T.base_model_source("openai/whisper-large-v3", REV)
    assert "incomplete" in str(e.value)


def test_seeder_revision_matches_trainer_pin():
    import re
    seeder = (ROOT / "scripts" / "seed_base_model.py").read_text()
    m = re.search(r'^REVISION = "([0-9a-f]{40})"', seeder, re.M)
    assert m, "seeder must pin a 40-char SHA"
    t = re.search(r'BASE_REVISION = os\.environ\.get\("BASE_REVISION", "([0-9a-f]{40})"\)',
                  TRAIN.read_text())
    assert m.group(1) == t.group(1), "cache would hold weights the trainer does not pin"
