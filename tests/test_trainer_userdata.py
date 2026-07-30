"""The launcher must survive rendering.

`envsubst` with no argument list substitutes EVERY variable it finds. Rendering
trainer_userdata.sh that way silently erases $RUN_ID, $S3, $SHIPPER and
$TRAIN_RC -- leaving a script that is still valid bash, uploads to
"s3://medzen-speech/candidates/preflight/" with an empty run id, and reports an
empty exit status. Nothing about it looks broken until a run is lost.

These tests render the committed launcher the documented way and assert both
that the placeholders are filled and that everything else is untouched.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = ROOT / "pipeline" / "trainer_userdata.sh"
BOOTSTRAP = ROOT / "pipeline" / "bootstrap_trainer.sh"
SUBST = "${TRAIN_ARGS} ${WATCHDOG_SECONDS} ${GIT_SHA}"

# Variables the script expands AT RUNTIME. If rendering consumes any of them,
# the generated launcher is silently wrong.
RUNTIME_VARS = ("$RUN_ID", "$S3", "$SHIPPER", "$TRAIN_RC", "$BOOT_RC",
                "$out", "$rc", "$WATCHDOG")

pytestmark = pytest.mark.skipif(shutil.which("envsubst") is None,
                                reason="envsubst not installed")


def render(train_args: str = "--max-steps 3 --languages pidgin",
           watchdog: str = "2700", subst: str | None = SUBST) -> str:
    env = {**os.environ, "TRAIN_ARGS": train_args, "WATCHDOG_SECONDS": watchdog,
           "GIT_SHA": "0" * 40}
    cmd = ["envsubst", subst] if subst is not None else ["envsubst"]
    return subprocess.run(cmd, stdin=LAUNCHER.open(), env=env,
                          capture_output=True, text=True, check=True).stdout


def test_rendered_launcher_is_valid_bash(tmp_path: Path) -> None:
    out = tmp_path / "ud.sh"
    out.write_text(render())
    r = subprocess.run(["bash", "-n", str(out)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_placeholders_are_substituted() -> None:
    rendered = render(train_args="--max-steps 3 --languages pidgin", watchdog="1234")
    assert "--max-steps 3 --languages pidgin" in rendered
    assert "${TRAIN_ARGS}" not in rendered
    assert 'WATCHDOG="1234"' in rendered          # render-time placeholder filled
    assert 'sleep "$WATCHDOG"' in rendered         # runtime variable untouched
    assert "${WATCHDOG_SECONDS}" not in rendered


def test_runtime_variables_survive_rendering() -> None:
    """The whole point of the argument list."""
    rendered = render()
    for var in RUNTIME_VARS:
        assert var in rendered, f"{var} was eaten by envsubst"
    # the run id must still be computed on the box, not baked in
    assert 'RUN_ID="preflight-$(date +%s)"' in rendered
    assert 'S3="s3://medzen-speech/candidates/preflight/$RUN_ID"' in rendered
    assert "exit $TRAIN_RC" in rendered


def test_bare_envsubst_is_destructive() -> None:
    """Guards the documentation: prove the unsafe form really does damage.

    If a future envsubst stopped eating these, the mandatory-argument warning
    in the launcher header would be stale and should be revisited.
    """
    unsafe = render(subst=None)
    eaten = [v for v in RUNTIME_VARS if v not in unsafe]
    assert eaten, "bare envsubst no longer destructive — revisit the header warning"
    assert "$RUN_ID" in eaten and "$TRAIN_RC" in eaten


def test_watchdog_defaults_when_unset() -> None:
    env = {k: v for k, v in os.environ.items() if k != "WATCHDOG_SECONDS"}
    env["TRAIN_ARGS"] = "--max-steps 3"
    r = subprocess.run(["envsubst", SUBST], stdin=LAUNCHER.open(), env=env,
                       capture_output=True, text=True, check=True)
    # unset renders empty; the bash := fallback must then supply the default
    assert 'WATCHDOG=""' in r.stdout
    assert ': "${WATCHDOG:=2700}"' in r.stdout


def test_bootstrap_is_syntactically_valid() -> None:
    r = subprocess.run(["bash", "-n", str(BOOTSTRAP)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_launcher_uses_the_clean_venv_not_the_image_interpreter() -> None:
    """Three DLAMI packages broke under the pinned torch; the venv is the fix."""
    text = LAUNCHER.read_text()
    assert "source /opt/medzen/venv/bin/activate" in text
    assert "/opt/pytorch/bin/activate" not in text


def test_trap_is_installed_before_anything_that_can_fail() -> None:
    """Attempts 1-3 uploaded nothing because the credential check ran first."""
    text = LAUNCHER.read_text()
    trap = text.index("trap finish EXIT")
    for later in ("get-caller-identity", "BUNDLE_BASE=", "bootstrap_trainer.sh"):
        assert text.index(later) > trap, f"{later!r} runs before the EXIT trap"


def test_launcher_verifies_the_bundle_before_bootstrapping():
    text = LAUNCHER.read_text()
    assert "BUNDLE VERIFIED" in text
    assert text.index("BUNDLE VERIFIED") < text.index("bootstrap_trainer.sh")
    assert "FILE SET MISMATCH" in text, "extra files must fail, not just missing ones"


def test_launcher_requires_a_full_sha_bundle_path():
    text = LAUNCHER.read_text()
    assert 'BUNDLE_BASE="s3://medzen-speech/candidates/bootstrap/$BUNDLE_SHA"' in text
    assert "${#BUNDLE_SHA} -eq 40" in text
