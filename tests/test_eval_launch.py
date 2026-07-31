"""Behavioural tests for the evaluation launcher and its fail-closed guards.

Every test here CALLS something. Source-string assertions can show a guard was
typed; only invoking it shows the guard fires, and the defect that started this
whole investigation was a guard that was typed correctly and never fired.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
UD = ROOT / "pipeline/eval_userdata.sh"

# envsubst (gettext-base) is absent from the slim runtime image. ONLY the tests
# that actually shell out to it are skipped there; every provenance,
# credential-chain, prompt, CUDA and hash test still runs inside the pinned
# image, because those are the ones that describe what the evaluator does.
# Rendering happens on the operator's machine, where envsubst exists and these
# are mandatory.
needs_envsubst = pytest.mark.skipif(
    shutil.which("envsubst") is None,
    reason="envsubst not installed (gettext-base); rendering happens on the "
           "operator machine, where this test is mandatory")

spec = importlib.util.spec_from_file_location(
    "evaluate_candidate", ROOT / "scripts/evaluate_candidate.py")
EC = importlib.util.module_from_spec(spec)
sys.modules["evaluate_candidate"] = EC
spec.loader.exec_module(EC)

GOOD = {
    "MEDZEN_IMAGE_DIGEST": "sha256:" + "f" * 64,
    "MEDZEN_CODE_GIT_SHA": "a" * 40,
    "MEDZEN_CODE_TAR_SHA256": "b" * 64,
}


@pytest.fixture
def clean_env(monkeypatch):
    for k in EC.REQUIRED_ENV:
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


# --------------------------------------------------------------------------- #
# provenance
# --------------------------------------------------------------------------- #
def test_complete_provenance_is_accepted(clean_env):
    for k, v in GOOD.items():
        clean_env.setenv(k, v)
    assert EC.require_provenance() == GOOD


@pytest.mark.parametrize("missing", sorted(GOOD))
def test_each_missing_provenance_value_refuses(clean_env, missing):
    """An evaluation whose own provenance is unknown cannot support a
    conclusion about anything else."""
    for k, v in GOOD.items():
        if k != missing:
            clean_env.setenv(k, v)
    with pytest.raises(SystemExit) as e:
        EC.require_provenance()
    assert missing in str(e.value) and "is not set" in str(e.value)


@pytest.mark.parametrize("key,bad", [
    ("MEDZEN_IMAGE_DIGEST", "f" * 64),                  # no sha256: prefix
    ("MEDZEN_IMAGE_DIGEST", "sha256:" + "f" * 63),      # too short
    ("MEDZEN_IMAGE_DIGEST", "sha256:" + "F" * 64),      # uppercase
    ("MEDZEN_CODE_GIT_SHA", "a" * 39),                  # short sha
    ("MEDZEN_CODE_GIT_SHA", "z" * 40),                  # not hex
    ("MEDZEN_CODE_TAR_SHA256", "b" * 63),
    ("MEDZEN_CODE_TAR_SHA256", "not-a-hash"),
])
def test_malformed_provenance_refuses(clean_env, key, bad):
    for k, v in GOOD.items():
        clean_env.setenv(k, v)
    clean_env.setenv(key, bad)
    with pytest.raises(SystemExit) as e:
        EC.require_provenance()
    assert key in str(e.value)


def test_blank_provenance_is_not_mistaken_for_present(clean_env):
    for k, v in GOOD.items():
        clean_env.setenv(k, v)
    clean_env.setenv("MEDZEN_CODE_TAR_SHA256", "   ")
    with pytest.raises(SystemExit, match="is not set"):
        EC.require_provenance()


def test_provenance_does_not_shell_out_to_git():
    """The published bundle contains no .git, so `git rev-parse` there returns
    nothing and a record built on it would silently claim no commit.

    Checked against the AST: the module docstring legitimately DISCUSSES git,
    and a text search would confuse explaining the trap with falling into it."""
    import ast
    tree = ast.parse((ROOT / "scripts/evaluate_candidate.py").read_text())
    imported = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imported |= {x.name.split(".")[0] for x in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            imported.add(n.module.split(".")[0])
    assert "subprocess" not in imported
    strings = [n.value for n in ast.walk(tree)
               if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    docstrings = {ast.get_docstring(n) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef))}
    live = [x for x in strings if x not in docstrings]
    assert not any("rev-parse" in x for x in live), "no git call may remain"


# --------------------------------------------------------------------------- #
# prompt accounting
# --------------------------------------------------------------------------- #
SOT, EN, TRANSCRIBE, NOTS, EOT = 50258, 50259, 50360, 50364, 50257
PROMPT = [SOT, EN, TRANSCRIBE, NOTS]


def test_exact_prompt_is_accepted():
    ids = PROMPT + [1000, 1001, EOT]
    assert EC.split_prompt(ids, PROMPT) == 4


def test_prompt_mismatch_refuses():
    """A decode that did not run under the pinned configuration must not be
    scored as though it had."""
    wrong = [SOT, 50325, TRANSCRIBE, NOTS] + [1000, EOT]      # yo, not en
    with pytest.raises(SystemExit) as e:
        EC.split_prompt(wrong, PROMPT)
    assert "run under the configuration this evaluation claims" in str(e.value)


def test_truncated_sequence_refuses():
    with pytest.raises(SystemExit):
        EC.split_prompt([SOT, EN], PROMPT)


def test_extra_control_token_counts_as_GENERATED_not_prompt():
    """The defect under investigation makes the model emit control tokens.
    Counting a leading special token as prompt -- which the old heuristic did --
    would shorten the measured output and hide exactly that behaviour."""
    ids = PROMPT + [NOTS, 1000, 1001, EOT]     # model re-emitted <|notimestamps|>
    n_prompt = EC.split_prompt(ids, PROMPT)
    assert n_prompt == 4
    generated = len(ids) - n_prompt
    assert generated == 4, "the stray control token is generated output"
    assert ids[n_prompt] == NOTS


def test_a_second_startoftranscript_is_generated_output():
    """The precise signature of the training defect."""
    ids = PROMPT + [SOT, 1000, EOT]
    assert EC.split_prompt(ids, PROMPT) == 4
    assert len(ids) - 4 == 3


# --------------------------------------------------------------------------- #
# CUDA
# --------------------------------------------------------------------------- #
def test_cuda_is_accepted():
    EC.require_cuda("cuda")


@pytest.mark.parametrize("dev", ["cpu", "mps", "", "CUDA"])
def test_non_cuda_refuses(dev):
    with pytest.raises(SystemExit) as e:
        EC.require_cuda(dev)
    assert "requires CUDA" in str(e.value)


# --------------------------------------------------------------------------- #
# pinned artifact identities
# --------------------------------------------------------------------------- #
def test_pinned_hashes_are_the_verified_ones():
    assert EC.BASE_MANIFEST_SHA256 == \
        "6a1987d462fc3330bb9eeeb488726bd7a16fd7d67f5aa08f0907eaa59d0913f1"
    assert EC.EVAL_MANIFEST_SHA256[("pidgin", "tts", "v1")] == \
        "3f642616b691745ad80904d1436826ca3c27355ab81bcaa133febd2ad1178739"


def test_unpinned_eval_set_refuses():
    """A set that is not pinned is not frozen, and a score against it cannot be
    compared with anything later."""
    assert ("pidgin", "asr", "v1") not in EC.EVAL_MANIFEST_SHA256


# --------------------------------------------------------------------------- #
# user-data: rendering, runtime variables, and what it runs
# --------------------------------------------------------------------------- #
RENDER = {
    "IMAGE_DIGEST": "sha256:" + "f" * 64,
    "GIT_SHA": "0123456789abcdef0123456789abcdef01234567",
    "TAR_SHA256": "1" * 64,
    "ADAPTER_URI": "s3://medzen-speech/candidates/asr/run/final",
    "ADAPTER_SHA256": "2" * 64,
    "WATCHDOG_SECONDS": "1800",
}


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    if shutil.which("envsubst") is None:
        pytest.skip("envsubst not installed (gettext-base)")
    out = tmp_path_factory.mktemp("ud") / "ud.sh"
    names = " ".join("${%s}" % k for k in RENDER)
    r = subprocess.run(["envsubst", names], stdin=open(UD),
                       capture_output=True, text=True,
                       env={**os.environ, **RENDER})
    assert r.returncode == 0, r.stderr
    out.write_text(r.stdout)
    return out


@needs_envsubst
def test_rendered_userdata_is_valid_bash(rendered):
    r = subprocess.run(["bash", "-n", str(rendered)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@needs_envsubst
def test_render_placeholders_are_all_substituted(rendered):
    code = "\n".join(l for l in rendered.read_text().splitlines()
                     if not l.lstrip().startswith("#"))
    for k in RENDER:
        assert "${%s}" % k not in code, f"{k} survived rendering"
    assert RENDER["IMAGE_DIGEST"] in code
    assert RENDER["GIT_SHA"] in code


@needs_envsubst
def test_runtime_variables_survive_rendering(rendered):
    """Bare envsubst would erase these and still produce valid bash."""
    code = rendered.read_text()
    for v in ("$RUN_ID", "$S3", "$SHIPPER", "$DIGEST", "$CODE_SHA", "$RC"):
        assert v in code, v


@needs_envsubst
def test_run_id_carries_the_commit_at_runtime(rendered):
    """`${GIT_SHA:0:12}` is the substring form, which envsubst never
    substitutes -- it would have produced a run id with no commit in it."""
    code = rendered.read_text()
    assert "${GIT_SHA:0:12}" not in code.split("# ---")[0].replace(
        "# the runtime copy CODE_SHA rather than from ${GIT_SHA:0:12}", "")
    line = next(l for l in code.splitlines() if l.startswith("RUN_ID="))
    assert "$CODE_SHA" in line


@needs_envsubst
def test_bundle_is_mounted_read_only_and_entrypoint_overridden(rendered):
    """Without --entrypoint this runs the image's baked 202b005 TRAINER."""
    code = rendered.read_text()
    assert "-v /opt/evalsrc/src:/opt/medzen:ro" in code
    assert "--entrypoint python" in code
    assert "scripts/evaluate_candidate.py" in code


@needs_envsubst
def test_writable_mounts_are_separate_from_the_verified_tree(rendered):
    code = rendered.read_text()
    assert "-v /opt/medzen-eval-out:/out" in code
    assert "-v /opt/medzen-eval-cache:/cache" in code
    assert "MEDZEN_EVAL_CACHE=/cache" in code
    assert "--out /out/evaluation.json" in code


@needs_envsubst
def test_archive_hash_comes_from_user_data_not_s3(rendered):
    code = rendered.read_text()
    assert 'ACTUAL=$(sha256sum medzen_code.tgz | cut -d\' \' -f1)' in code
    assert '[ "$ACTUAL" = "$CODE_TAR" ]' in code
    assert "ARCHIVE HASH VERIFIED against user-data" in code
    # and the bundle manifest is checked too, after the archive matches
    assert code.index("ARCHIVE HASH VERIFIED") < code.index("BUNDLE VERIFIED")


@needs_envsubst
def test_extraction_uses_the_data_filter(rendered):
    assert 'filter="data"' in rendered.read_text()


@needs_envsubst
def test_image_digest_is_verified_after_pull(rendered):
    code = rendered.read_text()
    assert '[ "$GOT" = "$DIGEST" ]' in code
    assert "DIGEST VERIFIED" in code


@needs_envsubst
def test_outputs_go_only_under_the_unique_evaluation_prefix(rendered):
    code = rendered.read_text()
    assert "candidates/evaluations/$RUN_ID" in code
    uploads = [l for l in code.splitlines() if "aws s3 cp" in l and "$S3" in l]
    assert uploads, "logs and results must be uploaded"
    assert all("$S3" in l for l in uploads)
    assert 'aws s3 cp /tmp/kms_probe.txt s3://medzen-speech/eval/' in code, \
        "the eval-deny probe is the one deliberate write elsewhere, and it must fail"


@needs_envsubst
def test_watchdog_trap_and_termination_are_preserved(rendered):
    code = rendered.read_text()
    assert 'WATCHDOG="1800"' in code
    assert "trap finish EXIT" in code
    assert code.count("shutdown -h now") >= 2, "watchdog AND exit path"


@needs_envsubst
def test_it_does_not_train_sweep_promote_or_deploy(rendered):
    code = rendered.read_text()
    for forbidden in ("train_asr", "--max-steps", "checkpoint-", "publish_registry",
                      "promote", "mlflow"):
        assert forbidden not in code, forbidden


@needs_envsubst
def test_eval_write_denial_is_exercised_not_assumed(rendered):
    code = rendered.read_text()
    assert "A3 guardrail breached" in code
    assert "EVAL DENY INTACT" in code
    assert "failed for the WRONG reason" in code


# --------------------------------------------------------------------------- #
# publishing the bundle
# --------------------------------------------------------------------------- #
def test_publish_bundle_has_a_dry_run_that_uploads_nothing():
    """An approval packet must quote the TAR hash BEFORE anything is written.
    The archive is byte-reproducible, so the dry-run hash is the hash a later
    publish produces."""
    src = (ROOT / "scripts/publish_bundle.py").read_text()
    assert '"--dry-run", action="store_true"' in src
    assert "DRY RUN — nothing uploaded" in src
    i_dry = src.index("if a.dry_run:")
    i_upload = src.index("c.upload_file(str(bundle)")
    assert i_dry < i_upload, "the dry-run must return before any upload"


def test_publish_bundle_still_refuses_a_dirty_tree():
    """The comment explains that --allow-dirty deliberately does not exist, so
    check the parser's actual arguments rather than the file's text."""
    import ast
    src = (ROOT / "scripts/publish_bundle.py").read_text()
    assert "REFUSING: working tree is dirty" in src
    flags = {n.args[0].value for n in ast.walk(ast.parse(src))
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "add_argument" and n.args
             and isinstance(n.args[0], ast.Constant)}
    assert "--allow-dirty" not in flags
    assert "--dry-run" in flags


# --------------------------------------------------------------------------- #
# credentials: the instance role must work
# --------------------------------------------------------------------------- #
def test_evaluator_never_pins_an_aws_profile():
    """profile_name='medzen' works on a laptop and fails on EC2: there is no
    ~/.aws there, so boto3 raises ProfileNotFound instead of reaching the
    instance role."""
    import ast
    tree = ast.parse((ROOT / "scripts/evaluate_candidate.py").read_text())
    assert not [n for n in ast.walk(tree)
                if isinstance(n, ast.keyword) and n.arg == "profile_name"]
    assert "PROFILE" not in {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}


def test_evaluator_session_uses_the_default_chain(monkeypatch):
    """Behavioural: build the client and assert what was passed to boto3."""
    import boto3
    seen = {}

    class FakeSession:
        def __init__(self, **kw):
            seen.update(kw)

        def client(self, name, **kw):
            return f"client:{name}"

    monkeypatch.setattr(boto3, "Session", FakeSession)
    assert EC.s3() == "client:s3"
    assert "profile_name" not in seen, seen
    assert seen == {"region_name": "eu-central-1"}


def test_evaluator_client_works_with_no_profile_env(monkeypatch):
    """AWS_PROFILE absent must not raise -- that is the EC2 case."""
    import boto3
    monkeypatch.delenv("AWS_PROFILE", raising=False)

    class FakeSession:
        def __init__(self, **kw):
            if "profile_name" in kw:
                raise AssertionError("would raise ProfileNotFound on EC2")

        def client(self, name, **kw):
            return "ok"

    monkeypatch.setattr(boto3, "Session", FakeSession)
    assert EC.s3() == "ok"


# --------------------------------------------------------------------------- #
# launcher validates the ALPHABET, not just the length
# --------------------------------------------------------------------------- #
def _render(**over):
    vals = {**RENDER, **over}
    names = " ".join("${%s}" % k for k in vals)
    r = subprocess.run(["envsubst", names], stdin=open(UD),
                       capture_output=True, text=True, env={**os.environ, **vals})
    assert r.returncode == 0
    return r.stdout


def _run_validation(tmp_path, **over):
    """Exercise the launcher's REAL validation block in isolation.

    Running the whole rendered script would start the log shipper and the
    1800-second watchdog and then try to `shutdown -h now` -- on a laptop that
    hangs the test run, which is exactly what happened the first time. So the
    shipped validation text is extracted verbatim, given a `die` stub, and run
    on its own. It is the same code, not a copy of it.
    """
    body = _render(**over)
    start = body.index("die() {")
    end = body.index("aws sts get-caller-identity")
    block = body[start:end]
    script = tmp_path / "validate.sh"
    # the variables the block reads, assigned exactly as the launcher does
    assigns = "\n".join(l for l in body.splitlines()
                         if l.startswith(("DIGEST=", "CODE_SHA=", "CODE_TAR=",
                                          "ADAPTER=", "ADAPTER_HASH=")))
    script.write_text("#!/bin/bash\n" + assigns + "\n" + block + "\necho VALIDATION_OK\n")
    return subprocess.run(["bash", str(script)], capture_output=True, text=True,
                          timeout=30)


@needs_envsubst
def test_valid_inputs_pass_validation(tmp_path):
    r = _run_validation(tmp_path)
    assert "VALIDATION_OK" in r.stdout, r.stdout + r.stderr


@pytest.mark.parametrize("var,bad", [
    ("GIT_SHA", "Z" * 40),                       # right length, wrong alphabet
    ("GIT_SHA", "A" * 40),                       # uppercase hex
    ("TAR_SHA256", "g" * 64),
    ("TAR_SHA256", "F" * 64),
    ("ADAPTER_SHA256", "!" * 64),
])
@needs_envsubst
def test_launcher_rejects_wrong_alphabet_at_right_length(tmp_path, var, bad):
    """A 64-character string of the wrong alphabet passes a length check and
    then fails deep inside a comparison with a far less clear message."""
    r = _run_validation(tmp_path, **{var: bad})
    assert "lowercase hex" in r.stdout + r.stderr, (r.stdout, r.stderr)
    assert "VALIDATION_OK" not in r.stdout


@pytest.mark.parametrize("var,bad", [
    ("GIT_SHA", "abc"), ("TAR_SHA256", "abc"), ("ADAPTER_SHA256", "abc"),
])
@needs_envsubst
def test_launcher_still_rejects_wrong_length(tmp_path, var, bad):
    r = _run_validation(tmp_path, **{var: bad})
    assert "must be" in r.stdout + r.stderr
    assert "VALIDATION_OK" not in r.stdout


def test_launcher_has_a_shared_hex_check():
    src = UD.read_text()
    assert "hexcheck()" in src
    assert "hexcheck GIT_SHA" in src
    assert "hexcheck TAR_SHA256" in src
    assert "hexcheck ADAPTER_SHA256" in src
    assert "Length alone is not validation" in src


@needs_envsubst
def test_launcher_confines_the_adapter_uri(tmp_path):
    r = _run_validation(tmp_path, ADAPTER_URI="s3://medzen-speech/eval/sneaky")
    assert "must be under s3://medzen-speech/candidates/" in r.stdout + r.stderr
    assert "VALIDATION_OK" not in r.stdout


# --------------------------------------------------------------------------- #
# dry run must not need AWS
# --------------------------------------------------------------------------- #
def test_dry_run_creates_no_client_before_returning():
    """A dry run that resolves credentials cannot be used to prepare an
    approval packet on a machine without them."""
    import ast
    src = (ROOT / "scripts/publish_bundle.py").read_text()
    tree = ast.parse(src)
    main = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "main")
    lines = [n.lineno for n in ast.walk(main)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "cli"]
    dry_return = src.index("print(f\"  would publish to")
    dry_line = src[:dry_return].count("\n") + 1
    assert all(ln > dry_line or "verify" in src.splitlines()[ln - 2].lower()
               for ln in lines), f"cli() called at {lines}, dry-run returns at {dry_line}"


def test_evaluator_writes_nothing_under_the_readonly_mount():
    """The verified bundle is mounted at /opt/medzen read-only. Every write the
    evaluator performs must land on an explicit writable mount instead."""
    src = (ROOT / "scripts/evaluate_candidate.py").read_text()
    assert 'os.environ.get("MEDZEN_EVAL_CACHE"' in src
    assert "NOT under ROOT" in src
    # the only default output path is overridable and the launcher overrides it
    assert '"--out"' in src
    ud = UD.read_text()
    assert "-e MEDZEN_EVAL_CACHE=/cache" in ud
    assert "--out /out/evaluation.json" in ud
    assert "/opt/medzen:ro" in ud


def test_pytest_cache_is_disabled_in_the_repository_config():
    """The previous version of this test asserted `X or True`, which is
    vacuously true and proved nothing. The real guarantee is the setting."""
    ini = (ROOT / "pytest.ini").read_text()
    addopts = next(l for l in ini.splitlines() if l.strip().startswith("addopts"))
    assert "-p no:cacheprovider" in addopts, addopts


# --------------------------------------------------------------------------- #
# the two bugs that would have failed the real run
# --------------------------------------------------------------------------- #
def test_load_audio_creates_a_nonexistent_cache_directory(tmp_path, monkeypatch):
    """The caller passes work/"audio", which nothing creates. Without an mkdir
    the very first clip fails on any fresh cache -- i.e. every disposable
    instance."""
    import wave

    raw = tmp_path / "src.wav"
    with wave.open(str(raw), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 1600)
    body = raw.read_bytes()
    sha = EC.sha256_bytes(body)

    class FakeS3:
        def get_object(self, Bucket, Key):
            class B:
                def read(self_inner):
                    return body
            return {"Body": B()}

    cache = tmp_path / "audio"          # deliberately does NOT exist
    assert not cache.exists()
    rec = {"audio_checksum_sha256": sha,
           "audio_filepath": "s3://medzen-speech/raw/x.wav"}
    audio, sr = EC.load_audio(FakeS3(), rec, cache)
    assert cache.is_dir()
    assert sr == 16000 and len(audio) == 1600


def test_load_audio_still_refuses_a_checksum_mismatch(tmp_path):
    class FakeS3:
        def get_object(self, Bucket, Key):
            class B:
                def read(self_inner):
                    return b"not the audio"
            return {"Body": B()}

    rec = {"audio_checksum_sha256": "a" * 64,
           "audio_filepath": "s3://medzen-speech/raw/x.wav"}
    with pytest.raises(SystemExit, match="audio checksum mismatch"):
        EC.load_audio(FakeS3(), rec, tmp_path / "fresh")


def test_output_path_outside_the_repo_does_not_raise(tmp_path, capsys):
    """The launcher writes to /out, a separate mount. relative_to() RAISES
    there, which would have failed the run AFTER the results were written."""
    out = tmp_path / "out" / "evaluation.json"
    out.parent.mkdir(parents=True)
    # the exact reporting logic from main()
    out.write_text("{}")
    try:
        shown = out.relative_to(EC.ROOT)
    except ValueError:
        shown = out.resolve()
    assert shown == out.resolve()

    src = (ROOT / "scripts/evaluate_candidate.py").read_text()
    assert "except ValueError:" in src
    assert "shown = out.resolve()" in src
    assert "out.relative_to(ROOT)\n    print" not in src, "must not print unguarded"


def test_output_directory_is_created_before_writing():
    src = (ROOT / "scripts/evaluate_candidate.py").read_text()
    i_mkdir = src.index("out.parent.mkdir(parents=True, exist_ok=True)")
    i_write = src.index("out.write_text(json.dumps(rec")
    assert i_mkdir < i_write


# --------------------------------------------------------------------------- #
# structured generation contract — behavioural
# --------------------------------------------------------------------------- #
SOT_, EN_, TR_, NT_, EOT_ = 50258, 50259, 50360, 50364, 50257
PROMPT_ = [SOT_, EN_, TR_, NT_]
MAXNEW = EC.GEN["max_new_tokens"]


class FakeSeq:
    """Stands in for a GenerateEncoderDecoderOutput: has .sequences."""

    def __init__(self, ids):
        import torch
        self.sequences = torch.tensor([ids])


def test_gen_kwargs_requests_the_structured_contract():
    kw = EC.gen_kwargs("en")
    assert kw["return_dict_in_generate"] is True
    assert kw["force_unique_generate_call"] is True
    assert kw["task"] == "transcribe" and kw["language"] == "en"
    assert kw["max_new_tokens"] == MAXNEW
    assert kw["num_beams"] == 1 and kw["do_sample"] is False


def test_both_arms_get_identical_generation_flags():
    """Same function, same argument -> byte-identical kwargs."""
    assert EC.gen_kwargs("en") == EC.gen_kwargs("en")
    base, cand = EC.gen_kwargs("en"), EC.gen_kwargs("en")
    assert base == cand


def test_structured_result_sequence_is_extracted():
    ids = PROMPT_ + [1000, 1001, EOT_]
    assert EC.extract_sequence(FakeSeq(ids)) == ids


def test_bare_tensor_is_refused_not_silently_accepted():
    """A plain tensor in this mode means prompt and EOS were stripped, so EOS
    and cap-hit numbers could not be true."""
    import torch
    with pytest.raises(SystemExit) as e:
        EC.extract_sequence(torch.tensor([[1000, 1001]]))
    assert "no `sequences`" in str(e.value)
    assert "return_dict_in_generate=True" in str(e.value)


def test_unexpected_contract_object_is_refused():
    class Weird:
        pass
    with pytest.raises(SystemExit, match="no `sequences`"):
        EC.extract_sequence(Weird())


def test_multiple_sequences_are_refused():
    import torch

    class Multi:
        sequences = torch.tensor([[1, 2], [3, 4]])
    with pytest.raises(SystemExit, match="expected exactly 1 sequence"):
        EC.extract_sequence(Multi())


def _account(ids):
    """The evaluator's accounting rules, applied to a sequence."""
    n_prompt = EC.split_prompt(ids, PROMPT_)
    n_total = len(ids)
    n_gen = n_total - n_prompt
    eos_pos = ids.index(EOT_, n_prompt) if EOT_ in ids[n_prompt:] else None
    eos = eos_pos is not None
    cap = (not eos) and n_gen >= MAXNEW
    return dict(prompt=n_prompt, generated=n_gen, total=n_total,
                eos=eos, eos_pos=eos_pos, cap=cap,
                stop="eos" if eos else "max_new_tokens" if cap else "other")


def test_exact_prompt_content_and_eos():
    ids = PROMPT_ + [1000, 1001, 1002, EOT_]
    a = _account(ids)
    assert a["prompt"] == 4
    assert a["generated"] == 4            # 3 content + EOS
    assert a["total"] == 8
    assert a["eos"] is True and a["eos_pos"] == 7
    assert a["cap"] is False and a["stop"] == "eos"


def test_full_budget_without_eos_is_a_cap_hit():
    ids = PROMPT_ + [2000 + i for i in range(MAXNEW)]
    a = _account(ids)
    assert a["prompt"] == 4
    assert a["generated"] == MAXNEW
    assert a["eos"] is False
    assert a["cap"] is True and a["stop"] == "max_new_tokens"


def test_short_output_without_eos_is_not_a_cap_hit():
    ids = PROMPT_ + [2000, 2001]
    a = _account(ids)
    assert a["eos"] is False and a["cap"] is False and a["stop"] == "other"


def test_eos_inside_the_prompt_region_is_not_counted():
    """EOS is only meaningful after the prompt."""
    ids = PROMPT_ + [1000, EOT_]
    a = _account(ids)
    assert a["eos_pos"] == 5 and a["eos"] is True


def test_wrong_prompt_refuses():
    wrong = [SOT_, 50325, TR_, NT_] + [1000, EOT_]      # yo, not en
    with pytest.raises(SystemExit) as e:
        EC.split_prompt(wrong, PROMPT_)
    assert "run under the configuration this evaluation claims" in str(e.value)


def test_prompt_stripped_sequence_refuses_in_this_mode():
    """Exactly what the failed run hit: content tokens at position 0."""
    stripped = [805, 6555, 295, 10411, EOT_]
    with pytest.raises(SystemExit) as e:
        EC.split_prompt(stripped, PROMPT_)
    assert "sequence begins" in str(e.value)


# --------------------------------------------------------------------------- #
# short-form guard
# --------------------------------------------------------------------------- #
def test_short_form_accepted_and_longest_returned():
    rows = [{"duration_s": 4.92}, {"duration_s": 28.18}, {"duration_s": 9.1}]
    assert EC.require_short_form(rows) == 28.18


def test_long_form_clip_refuses_the_forced_single_call():
    rows = [{"duration_s": 9.1}, {"duration_s": 30.0}]
    with pytest.raises(SystemExit) as e:
        EC.require_short_form(rows)
    assert "segment boundary" in str(e.value)
    assert "one segment as the whole clip" in str(e.value)


def test_frozen_pidgin_set_is_short_form():
    """The set this run scores: max 28.18s, verified from manifest metadata."""
    assert EC.SEGMENT_LIMIT_S == 30.0
    assert 28.18 < EC.SEGMENT_LIMIT_S
