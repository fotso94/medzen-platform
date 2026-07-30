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
    assert "src = base_model_source(a.base_model, rev, allow_hub=allow_hub)" in src
    assert src.count("**src.kwargs") == 2, "revision must reach model AND processor"
    assert "from_pretrained(src.path" in src
    # only --smoke or a deliberate env override may reach the Hub
    assert 'allow_hub = bool(a.smoke or os.environ.get("MEDZEN_ALLOW_HUB"))' in src


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


def _seed_local(dirp, contents):
    """Write files and return a manifest with their REAL sha256 and sizes."""
    import hashlib
    files = {}
    for name, data in contents.items():
        f = dirp / name
        f.write_bytes(data)
        files[name] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    return {"repo": "openai/whisper-large-v3", "revision": REV, "files": files}


def _manifest(rev=REV, files=("config.json", "model.safetensors")):
    return {"repo": "openai/whisper-large-v3", "revision": rev,
            "files": {f: {"sha256": "x" * 64, "bytes": 1} for f in files}}


def test_explicit_hub_opt_in_still_enforces_the_pin():
    """allow_hub is for the laptop smoke test only, and must pass revision=."""
    from pipeline.train_asr import base_model_source
    src = base_model_source("openai/whisper-large-v3", REV, allow_hub=True)
    assert src.path == "openai/whisper-large-v3"
    assert src.kwargs == {"revision": REV}


def test_cache_miss_FAILS_CLOSED_with_no_hub_fallback():
    """A fallback would train on unverified weights while reporting the pin,
    and would do so on every Spot restart. It must be an error instead."""
    from unittest.mock import MagicMock, patch
    import pipeline.train_asr as T
    cli = MagicMock()
    cli.get_object.side_effect = RuntimeError("NoSuchKey")
    with patch.object(T, "s3", return_value=cli):
        with pytest.raises(SystemExit) as e:
            T.base_model_source("openai/whisper-large-v3", REV)
    msg = str(e.value)
    assert "REFUSING" in msg and "NO Hub fallback" in msg


def test_no_hub_fallback_anywhere_in_the_cache_path():
    """Guards against the fallback being reintroduced."""
    src = TRAIN.read_text()
    fn = src[src.index("def base_model_source"):src.index("def sync_down")]
    body = fn[fn.index("if allow_hub"):]
    after_optin = body[body.index("return BaseSource(repo"):]
    assert "BaseSource(repo" not in after_optin[len("return BaseSource(repo"):], \
        "the cache path must never return the Hub repo as a source"


def test_cache_with_wrong_revision_is_refused():
    """A local directory ignores revision=, so a mismatched cache would train on
    different weights while the run reported the pinned SHA."""
    from unittest.mock import MagicMock, patch
    import pipeline.train_asr as T
    cli = MagicMock()
    cli.get_object.return_value = {
        "Body": MagicMock(read=lambda: json.dumps(_manifest(rev="dead" * 10)).encode())}
    with patch.object(T, "s3", return_value=cli):
        with pytest.raises(SystemExit) as e:
            T.base_model_source("openai/whisper-large-v3", REV)
    assert "REFUSING" in str(e.value) and REV in str(e.value)


def test_verified_cache_loads_locally_with_no_network(monkeypatch, tmp_path):
    """The offline path: a good cache resolves to a local dir, and the HF Hub is
    never consulted -- snapshot_download would raise if it were."""
    from unittest.mock import MagicMock, patch
    import pipeline.train_asr as T
    monkeypatch.setenv("MEDZEN_MODEL_DIR", str(tmp_path))
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    local = tmp_path / "whisper-large-v3" / REV
    local.mkdir(parents=True)
    man = _seed_local(local, {"config.json": b"{}", "model.safetensors": b"weights"})
    (local / "MANIFEST.json").write_text(json.dumps(man))

    cli = MagicMock()
    cli.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(man).encode())}
    import huggingface_hub
    def boom(*a, **k):
        raise AssertionError("the Hub was contacted on the cache path")
    with patch.object(T, "s3", return_value=cli), \
         patch.object(huggingface_hub, "snapshot_download", boom):
        src = T.base_model_source("openai/whisper-large-v3", REV)
    assert src.path == str(local)
    assert src.kwargs == {}, "revision= on a local dir is silently ignored; do not pass it"


def test_corrupt_file_is_refused_even_though_it_exists(monkeypatch, tmp_path):
    """Verification must refuse, and refuse even after a refetch that also
    returns bad bytes -- fail closed, never fall back, never loop."""
    from unittest.mock import patch
    import hashlib
    import pipeline.train_asr as T
    monkeypatch.setenv("MEDZEN_MODEL_DIR", str(tmp_path))
    good = b"weights"
    man = {"repo": "openai/whisper-large-v3", "revision": REV,
           "files": {"config.json": {"sha256": hashlib.sha256(b"{}").hexdigest(), "bytes": 2},
                     "model.safetensors": {"sha256": hashlib.sha256(good).hexdigest(),
                                           "bytes": len(good)}}}

    def bad_sync(uri, local):
        Path(local).mkdir(parents=True, exist_ok=True)
        (Path(local) / "config.json").write_bytes(b"{}")
        (Path(local) / "model.safetensors").write_bytes(b"TAMPERED")

    with patch.object(T, "s3", return_value=_mock_cli(man)), \
         patch.object(T, "sync_down", bad_sync):
        with pytest.raises(SystemExit) as e:
            T.base_model_source("openai/whisper-large-v3", REV)
    assert "failed verification" in str(e.value)
    assert not (tmp_path / "whisper-large-v3" / REV).exists()

def test_truncated_file_is_refused_on_size(monkeypatch, tmp_path):
    """Verification must refuse, and refuse even after a refetch that also
    returns bad bytes -- fail closed, never fall back, never loop."""
    from unittest.mock import patch
    import hashlib
    import pipeline.train_asr as T
    monkeypatch.setenv("MEDZEN_MODEL_DIR", str(tmp_path))
    good = b"weights"
    man = {"repo": "openai/whisper-large-v3", "revision": REV,
           "files": {"config.json": {"sha256": hashlib.sha256(b"{}").hexdigest(), "bytes": 2},
                     "model.safetensors": {"sha256": hashlib.sha256(good).hexdigest(),
                                           "bytes": len(good)}}}

    def bad_sync(uri, local):
        Path(local).mkdir(parents=True, exist_ok=True)
        (Path(local) / "config.json").write_bytes(b"{}")
        (Path(local) / "model.safetensors").write_bytes(b"weig")

    with patch.object(T, "s3", return_value=_mock_cli(man)), \
         patch.object(T, "sync_down", bad_sync):
        with pytest.raises(SystemExit) as e:
            T.base_model_source("openai/whisper-large-v3", REV)
    assert "bytes, expected" in str(e.value)
    assert not (tmp_path / "whisper-large-v3" / REV).exists()

def test_missing_file_is_refused(monkeypatch, tmp_path):
    """Verification must refuse, and refuse even after a refetch that also
    returns bad bytes -- fail closed, never fall back, never loop."""
    from unittest.mock import patch
    import hashlib
    import pipeline.train_asr as T
    monkeypatch.setenv("MEDZEN_MODEL_DIR", str(tmp_path))
    good = b"weights"
    man = {"repo": "openai/whisper-large-v3", "revision": REV,
           "files": {"config.json": {"sha256": hashlib.sha256(b"{}").hexdigest(), "bytes": 2},
                     "model.safetensors": {"sha256": hashlib.sha256(good).hexdigest(),
                                           "bytes": len(good)}}}

    def bad_sync(uri, local):
        Path(local).mkdir(parents=True, exist_ok=True)
        (Path(local) / "config.json").write_bytes(b"{}")
        pass  # model.safetensors deliberately absent

    with patch.object(T, "s3", return_value=_mock_cli(man)), \
         patch.object(T, "sync_down", bad_sync):
        with pytest.raises(SystemExit) as e:
            T.base_model_source("openai/whisper-large-v3", REV)
    assert "missing" in str(e.value)
    assert not (tmp_path / "whisper-large-v3" / REV).exists()

def test_image_forces_hub_offline():
    """The container must be unable to reach the Hub at all."""
    df = (ROOT / "pipeline" / "Dockerfile.trainer").read_text()
    assert "HF_HUB_OFFLINE=1" in df


def test_seeder_revision_matches_trainer_pin():
    import re
    seeder = (ROOT / "scripts" / "seed_base_model.py").read_text()
    m = re.search(r'^REVISION = "([0-9a-f]{40})"', seeder, re.M)
    assert m, "seeder must pin a 40-char SHA"
    t = re.search(r'BASE_REVISION = os\.environ\.get\("BASE_REVISION", "([0-9a-f]{40})"\)',
                  TRAIN.read_text())
    assert m.group(1) == t.group(1), "cache would hold weights the trainer does not pin"


# --------------------------------------------------------------------------- #
# training must work with NO Hub and NO network for the model
# --------------------------------------------------------------------------- #
@pytest.mark.slow
def test_real_cache_loads_offline_from_s3(tmp_path, monkeypatch):
    """End-to-end offline proof against the ACTUAL seeded cache.

    Downloads the real 3.09 GB cache from S3, verifies every sha256, and loads
    the processor from the local directory with HF_HUB_OFFLINE=1 set — so any
    attempt to reach the Hub raises rather than silently succeeding. The mocked
    tests prove the refusal logic; this proves a real checkpoint loads with the
    Hub unreachable.
    """
    monkeypatch.setenv("MEDZEN_MODEL_DIR", str(tmp_path))
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("AWS_PROFILE", "medzen")

    from pipeline.train_asr import BASE_REVISION, base_model_source
    src = base_model_source("openai/whisper-large-v3", BASE_REVISION)
    assert src.kwargs == {}
    local = Path(src.path)
    assert (local / "model.safetensors").stat().st_size == 3_087_130_976

    from transformers import WhisperProcessor
    proc = WhisperProcessor.from_pretrained(src.path)
    assert proc.tokenizer is not None and proc.feature_extractor is not None
    # the tokenizer must know the language tokens the trainer sets
    proc.tokenizer.set_prefix_tokens(language="en", task="transcribe")


# --------------------------------------------------------------------------- #
# atomic download + provenance
# --------------------------------------------------------------------------- #
def _mock_cli(man):
    """A mock S3 client that cannot hang.

    list_objects_v2 must return a REAL dict: with a bare MagicMock,
    r.get("IsTruncated") is a truthy mock and sync_down's pagination loop never
    terminates -- which is how three of these tests wedged the suite for seven
    minutes rather than failing.
    """
    from unittest.mock import MagicMock
    cli = MagicMock()
    cli.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(man).encode())}
    cli.list_objects_v2.return_value = {"Contents": [], "IsTruncated": False}
    return cli


def test_download_is_atomic_partial_never_visible(monkeypatch, tmp_path):
    """A transfer that dies must leave nothing at the real path, or a later run
    finds a plausible cache and either trusts it or refuses forever."""
    from unittest.mock import patch
    import pipeline.train_asr as T
    monkeypatch.setenv("MEDZEN_MODEL_DIR", str(tmp_path))
    man = {"repo": "openai/whisper-large-v3", "revision": REV,
           "files": {"model.safetensors": {"sha256": "a" * 64, "bytes": 7}}}
    final = tmp_path / "whisper-large-v3" / REV

    def dying_sync(uri, local):
        Path(local).mkdir(parents=True, exist_ok=True)
        (Path(local) / "model.safetensors").write_bytes(b"part")   # short
        raise RuntimeError("connection reset")

    with patch.object(T, "s3", return_value=_mock_cli(man)), \
         patch.object(T, "sync_down", dying_sync):
        with pytest.raises(RuntimeError):
            T.base_model_source("openai/whisper-large-v3", REV)
    assert not final.exists(), "a partial download became visible at the real path"


def test_failed_verification_leaves_nothing_behind(monkeypatch, tmp_path):
    from unittest.mock import patch
    import pipeline.train_asr as T
    monkeypatch.setenv("MEDZEN_MODEL_DIR", str(tmp_path))
    man = {"repo": "openai/whisper-large-v3", "revision": REV,
           "files": {"model.safetensors": {"sha256": "a" * 64, "bytes": 7}}}
    final = tmp_path / "whisper-large-v3" / REV

    def bad_sync(uri, local):
        Path(local).mkdir(parents=True, exist_ok=True)
        (Path(local) / "model.safetensors").write_bytes(b"1234567")   # right size, wrong bytes

    with patch.object(T, "s3", return_value=_mock_cli(man)), \
         patch.object(T, "sync_down", bad_sync):
        with pytest.raises(SystemExit):
            T.base_model_source("openai/whisper-large-v3", REV)
    assert not final.exists()
    assert not list((tmp_path / "whisper-large-v3").glob("*.partial-*")), "scratch dir left behind"


def test_corrupt_existing_cache_self_heals(monkeypatch, tmp_path):
    """Fail-closed must not mean stuck: a bad local copy is refetched."""
    from unittest.mock import patch
    import hashlib
    import pipeline.train_asr as T
    monkeypatch.setenv("MEDZEN_MODEL_DIR", str(tmp_path))
    good = b"real weights"
    man = {"repo": "openai/whisper-large-v3", "revision": REV,
           "files": {"model.safetensors":
                     {"sha256": hashlib.sha256(good).hexdigest(), "bytes": len(good)}}}
    final = tmp_path / "whisper-large-v3" / REV
    final.mkdir(parents=True)
    (final / "model.safetensors").write_bytes(b"TAMPERED!!!!")   # same length, wrong bytes

    def good_sync(uri, local):
        Path(local).mkdir(parents=True, exist_ok=True)
        (Path(local) / "model.safetensors").write_bytes(good)

    with patch.object(T, "s3", return_value=_mock_cli(man)), \
         patch.object(T, "sync_down", good_sync):
        src = T.base_model_source("openai/whisper-large-v3", REV)
    assert (final / "model.safetensors").read_bytes() == good


def test_stale_partial_dirs_are_cleaned(monkeypatch, tmp_path):
    from unittest.mock import patch
    import hashlib
    import pipeline.train_asr as T
    monkeypatch.setenv("MEDZEN_MODEL_DIR", str(tmp_path))
    good = b"w"
    man = {"repo": "openai/whisper-large-v3", "revision": REV,
           "files": {"m": {"sha256": hashlib.sha256(good).hexdigest(), "bytes": 1}}}
    root = tmp_path / "whisper-large-v3"
    stale = root / f"{REV}.partial-99999"
    stale.mkdir(parents=True)
    (stale / "junk").write_bytes(b"x" * 100)

    def good_sync(uri, local):
        Path(local).mkdir(parents=True, exist_ok=True)
        (Path(local) / "m").write_bytes(good)

    with patch.object(T, "s3", return_value=_mock_cli(man)), \
         patch.object(T, "sync_down", good_sync):
        T.base_model_source("openai/whisper-large-v3", REV)
    assert not stale.exists(), "abandoned scratch dir from a killed run was not cleaned"


def test_provenance_is_recorded(monkeypatch, tmp_path):
    """Cache URI, manifest digest and verified revision must be logged."""
    from unittest.mock import patch
    import hashlib
    import pipeline.train_asr as T
    monkeypatch.setenv("MEDZEN_MODEL_DIR", str(tmp_path))
    good = b"w"
    man = {"repo": "openai/whisper-large-v3", "revision": REV,
           "files": {"m": {"sha256": hashlib.sha256(good).hexdigest(), "bytes": 1}}}
    expect_sha = hashlib.sha256(json.dumps(man).encode()).hexdigest()

    def good_sync(uri, local):
        Path(local).mkdir(parents=True, exist_ok=True)
        (Path(local) / "m").write_bytes(good)

    with patch.object(T, "s3", return_value=_mock_cli(man)), \
         patch.object(T, "sync_down", good_sync):
        src = T.base_model_source("openai/whisper-large-v3", REV)
    p = src.provenance()
    assert p["base_source"] == "s3_cache"
    assert p["base_cache_uri"].endswith(f"models/base/whisper-large-v3/{REV}")
    assert p["base_manifest_sha256"] == expect_sha
    assert p["base_revision_verified"] == REV
    assert p["base_cache_files"] == 1


def test_hub_provenance_is_distinguishable():
    from pipeline.train_asr import base_model_source
    p = base_model_source("openai/whisper-large-v3", REV, allow_hub=True).provenance()
    assert p["base_source"] == "hf_hub" and p["base_cache_uri"] == ""


def test_provenance_reaches_mlflow_and_run_json():
    src = TRAIN.read_text()
    assert src.count("**src.provenance()") == 2, \
        "provenance must land in BOTH the MLflow params and run.json"


# --------------------------------------------------------------------------- #
# run provenance: which artifact, from which commit
# --------------------------------------------------------------------------- #
def test_runtime_provenance_distinguishes_container_from_venv(monkeypatch):
    from pipeline.train_asr import BaseSource
    monkeypatch.setenv("MEDZEN_IMAGE_DIGEST", "sha256:" + "a" * 64)
    monkeypatch.setenv("MEDZEN_GIT_SHA", "b" * 40)
    p = BaseSource.runtime_provenance()
    assert p["ran_in_container"] is True
    assert p["image_digest"].startswith("sha256:")
    assert p["image_git_sha"] == "b" * 40

    monkeypatch.delenv("MEDZEN_IMAGE_DIGEST")
    p = BaseSource.runtime_provenance()
    assert p["ran_in_container"] is False, "no digest means the EC2 venv path"
    assert p["image_digest"] == ""


def test_provenance_reaches_mlflow_and_run_json_both():
    src = TRAIN.read_text()
    assert src.count("**BaseSource.runtime_provenance()") == 2, \
        "artifact provenance must land in BOTH the MLflow params and run.json"


def test_commit_is_baked_not_passed():
    """An image told its own provenance can be told the wrong thing."""
    df = (ROOT / "pipeline" / "Dockerfile.trainer").read_text()
    assert "ARG GIT_SHA" in df and "ENV MEDZEN_GIT_SHA=$GIT_SHA" in df
    # and below the expensive layers, or every commit invalidates the pip layer
    assert df.index("RUN pip install -r requirements.txt") < df.index("ARG GIT_SHA")


def test_build_verifies_the_baked_commit():
    s = (ROOT / "pipeline" / "build_image.sh").read_text()
    assert "--build-arg GIT_SHA=" in s
    assert "printenv" in s and "MEDZEN_GIT_SHA" in s
    assert "exit 37" in s, "a mismatched baked commit must fail the build"


def test_launcher_passes_the_verified_digest_into_the_container():
    s = (ROOT / "pipeline" / "container_userdata.sh").read_text()
    assert '-e MEDZEN_IMAGE_DIGEST="$DIGEST"' in s
    # and only after the pulled digest has been checked against the pin
    assert s.index("DIGEST VERIFIED") < s.index('-e MEDZEN_IMAGE_DIGEST')


def test_training_cannot_promote_to_approved():
    """Promotion is B5 and manual. Nothing in the training path may write
    approved/ or the SSM registry that gates serving."""
    for f in (TRAIN, ROOT / "pipeline" / "container_entrypoint.sh"):
        code = "\n".join(l for l in f.read_text().splitlines()
                         if not l.lstrip().startswith("#"))
        assert "approved/" not in code, f"{f.name} references approved/"
    spec = (ROOT / "platform" / "services.yaml").read_text()
    trainer = spec.split("  trainer:")[1].split("\n  builder:")[0]
    assert "approved" not in trainer, "the trainer must have no approved/ grant"
    assert "ssm:" not in trainer, "the trainer must not write the serving registry"
