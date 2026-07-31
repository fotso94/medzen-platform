"""Behavioural tests for the evaluation launcher and its fail-closed guards.

Every test here CALLS something. Source-string assertions can show a guard was
typed; only invoking it shows the guard fires, and the defect that started this
whole investigation was a guard that was typed correctly and never fired.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
UD = ROOT / "pipeline/eval_userdata.sh"

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
    out = tmp_path_factory.mktemp("ud") / "ud.sh"
    names = " ".join("${%s}" % k for k in RENDER)
    r = subprocess.run(["envsubst", names], stdin=open(UD),
                       capture_output=True, text=True,
                       env={**os.environ, **RENDER})
    assert r.returncode == 0, r.stderr
    out.write_text(r.stdout)
    return out


def test_rendered_userdata_is_valid_bash(rendered):
    r = subprocess.run(["bash", "-n", str(rendered)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_render_placeholders_are_all_substituted(rendered):
    code = "\n".join(l for l in rendered.read_text().splitlines()
                     if not l.lstrip().startswith("#"))
    for k in RENDER:
        assert "${%s}" % k not in code, f"{k} survived rendering"
    assert RENDER["IMAGE_DIGEST"] in code
    assert RENDER["GIT_SHA"] in code


def test_runtime_variables_survive_rendering(rendered):
    """Bare envsubst would erase these and still produce valid bash."""
    code = rendered.read_text()
    for v in ("$RUN_ID", "$S3", "$SHIPPER", "$DIGEST", "$CODE_SHA", "$RC"):
        assert v in code, v


def test_run_id_carries_the_commit_at_runtime(rendered):
    """`${GIT_SHA:0:12}` is the substring form, which envsubst never
    substitutes -- it would have produced a run id with no commit in it."""
    code = rendered.read_text()
    assert "${GIT_SHA:0:12}" not in code.split("# ---")[0].replace(
        "# the runtime copy CODE_SHA rather than from ${GIT_SHA:0:12}", "")
    line = next(l for l in code.splitlines() if l.startswith("RUN_ID="))
    assert "$CODE_SHA" in line


def test_bundle_is_mounted_read_only_and_entrypoint_overridden(rendered):
    """Without --entrypoint this runs the image's baked 202b005 TRAINER."""
    code = rendered.read_text()
    assert "-v /opt/evalsrc/src:/opt/medzen:ro" in code
    assert "--entrypoint python" in code
    assert "scripts/evaluate_candidate.py" in code


def test_writable_mounts_are_separate_from_the_verified_tree(rendered):
    code = rendered.read_text()
    assert "-v /opt/medzen-eval-out:/out" in code
    assert "-v /opt/medzen-eval-cache:/cache" in code
    assert "MEDZEN_EVAL_CACHE=/cache" in code
    assert "--out /out/evaluation.json" in code


def test_archive_hash_comes_from_user_data_not_s3(rendered):
    code = rendered.read_text()
    assert 'ACTUAL=$(sha256sum medzen_code.tgz | cut -d\' \' -f1)' in code
    assert '[ "$ACTUAL" = "$CODE_TAR" ]' in code
    assert "ARCHIVE HASH VERIFIED against user-data" in code
    # and the bundle manifest is checked too, after the archive matches
    assert code.index("ARCHIVE HASH VERIFIED") < code.index("BUNDLE VERIFIED")


def test_extraction_uses_the_data_filter(rendered):
    assert 'filter="data"' in rendered.read_text()


def test_image_digest_is_verified_after_pull(rendered):
    code = rendered.read_text()
    assert '[ "$GOT" = "$DIGEST" ]' in code
    assert "DIGEST VERIFIED" in code


def test_outputs_go_only_under_the_unique_evaluation_prefix(rendered):
    code = rendered.read_text()
    assert "candidates/evaluations/$RUN_ID" in code
    uploads = [l for l in code.splitlines() if "aws s3 cp" in l and "$S3" in l]
    assert uploads, "logs and results must be uploaded"
    assert all("$S3" in l for l in uploads)
    assert 'aws s3 cp /tmp/kms_probe.txt s3://medzen-speech/eval/' in code, \
        "the eval-deny probe is the one deliberate write elsewhere, and it must fail"


def test_watchdog_trap_and_termination_are_preserved(rendered):
    code = rendered.read_text()
    assert 'WATCHDOG="1800"' in code
    assert "trap finish EXIT" in code
    assert code.count("shutdown -h now") >= 2, "watchdog AND exit path"


def test_it_does_not_train_sweep_promote_or_deploy(rendered):
    code = rendered.read_text()
    for forbidden in ("train_asr", "--max-steps", "checkpoint-", "publish_registry",
                      "promote", "mlflow"):
        assert forbidden not in code, forbidden


def test_eval_write_denial_is_exercised_not_assumed(rendered):
    code = rendered.read_text()
    assert "A3 guardrail breached" in code
    assert "EVAL DENY INTACT" in code
    assert "failed for the WRONG reason" in code
