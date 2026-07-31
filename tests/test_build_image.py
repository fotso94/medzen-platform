"""Supply-chain gates on the image build.

The image trains the models that go to production, so what goes into it must be
verifiable rather than asserted. These tests pin the guarantees that were added
after review, so a later edit cannot quietly drop one:

  * the tag is a full commit SHA, validated before anything is built
  * the bundle is fetched from a per-commit path and its COMPLETE file set,
    sizes and sha256 are checked -- extras included
  * the base image is pinned by digest, not by a moving tag
  * an image whose scan does not reach COMPLETE is a build failure
  * findings above the threshold make the image not adoptable
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "pipeline" / "build_image.sh"
DOCKERFILE = ROOT / "pipeline" / "Dockerfile.trainer"
PUBLISH = ROOT / "scripts" / "publish_bundle.py"


def test_build_script_is_valid_bash():
    r = subprocess.run(["bash", "-n", str(BUILD)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_git_sha_must_be_a_full_40_char_hex_sha():
    s = BUILD.read_text()
    assert "FATAL: GIT_SHA is required" in s
    assert "-ne 40" in s, "a short SHA must be rejected; immutable tags cannot be fixed"
    assert "*[!0-9a-f]*)" in s, "non-hex must be rejected"


def test_bundle_is_fetched_from_a_per_commit_path():
    """Fetching happens only in the trusted wrapper now."""
    s = (ROOT / "pipeline" / "builder_userdata.sh").read_text()
    assert 'B="s3://medzen-speech/candidates/bootstrap/$GIT_SHA"' in s, \
        "a shared path lets the builder fetch different bytes than were verified"
    assert '"$B/medzen_code.tgz"' in s
    assert '"$B/BUNDLE.json"' in s


def test_bundle_verification_covers_set_sizes_and_hashes():
    s = (ROOT / "pipeline" / "builder_userdata.sh").read_text()
    assert "declared - on_disk" in s and "on_disk - declared" in s, \
        "an unexpected extra file in the image must fail the build"
    assert "FILE SET MISMATCH" in s
    assert 'meta["bytes"]' in s and 'meta["sha256"]' in s
    assert "BUNDLE VERIFIED" in s


def test_build_image_has_no_fetch_or_extract_path_of_its_own():
    """A second, unfiltered way into the build is an unaudited route around the
    trusted wrapper. There must be exactly one entry point."""
    s = BUILD.read_text()
    code = "\n".join(l for l in s.splitlines() if not l.lstrip().startswith("#"))
    assert "tar xzf" not in code
    assert "medzen_code.tgz" not in code
    assert "BUNDLE_BASE" not in code


def test_bundle_dir_is_mandatory():
    s = BUILD.read_text()
    assert 'if [ -z "${BUNDLE_DIR:-}" ]; then' in s
    assert "FATAL: BUNDLE_DIR is required" in s
    assert "There is no self-service fetch path" in s
    # and it must be checked before the build
    assert s.index("BUNDLE_DIR is required") < s.index("docker build")


def test_base_image_is_pinned_by_digest():
    s = DOCKERFILE.read_text()
    m = re.search(r"^FROM \S+@sha256:([0-9a-f]{64})\s*$", s, re.M)
    assert m, "the base image must be pinned by digest, not by a moving tag"


def test_scan_must_reach_complete_or_the_build_fails():
    s = BUILD.read_text()
    assert 'SCAN_DONE=no' in s and 'SCAN_DONE=yes' in s
    assert 'if [ "$SCAN_DONE" != yes ]; then' in s
    assert "exit 33" in s, "a scan timeout must be a failure, not a warning"
    assert "FATAL: cannot read scan findings" in s


def test_vulnerability_thresholds_are_enforced_and_default_to_zero():
    s = BUILD.read_text()
    assert 'SCAN_MAX_CRITICAL="${SCAN_MAX_CRITICAL:-0}"' in s
    assert 'SCAN_MAX_HIGH="${SCAN_MAX_HIGH:-0}"' in s
    assert "SCAN GATE FAILED" in s, "the gate must have a failure path"
    assert "exit 34" in s, "exceeding the threshold must fail the build"
    assert '"adoptable": scan_rc == 0' in s, "image.json must record adoptability"
    # all four actionable severities gated, not just the top two
    for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
        assert f'"{sev}":' in s, f"{sev} must be gated"


def test_scan_gate_is_documented_as_post_push():
    """scan-on-push cannot prevent publication; the honest claim is that it
    gates adoption. If that comment goes, the semantics are being misread."""
    s = BUILD.read_text()
    assert "gates ADOPTION" in s


def test_publish_bundle_refuses_a_dirty_tree_with_no_override():
    s = PUBLISH.read_text()
    assert "REFUSING: working tree is dirty" in s
    assert "--allow-dirty" not in s.replace(
        "# There is deliberately no --allow-dirty.", ""), "no dirty-tree escape hatch"
    assert 'assert len(head) == 40' in s, "must use the full SHA"


def test_publish_bundle_uses_a_per_commit_path():
    s = PUBLISH.read_text()
    assert 'base = f"{PREFIX}/{sha}"' in s
    assert 'f"{base}/BUNDLE.json"' in s
    # BUNDLE.json must be written after the tarball, so its presence means the
    # pair is complete
    assert s.index('f"{base}/medzen_code.tgz"') < s.index('f"{base}/BUNDLE.json"')


def test_publish_bundle_conditionally_creates_or_exactly_reuses_objects():
    s = PUBLISH.read_text()
    assert 'IfNoneMatch="*"' in s
    assert "already exists with " in s and "different bytes" in s
    assert "was concurrently created" in s
    assert "verified existing" in s


# --------------------------------------------------------------------------- #
# bootstrap trust: verification must precede execution of anything from S3
# --------------------------------------------------------------------------- #
WRAPPER = ROOT / "pipeline" / "builder_userdata.sh"
TRAINER = ROOT / "pipeline" / "trainer_userdata.sh"


def test_wrapper_is_valid_bash():
    r = subprocess.run(["bash", "-n", str(WRAPPER)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_wrapper_verifies_the_archive_against_a_hash_from_user_data():
    """The root of trust must not come from the same place as the artifact.

    The first wrapper executed build_image.sh from the bundle and let that
    script verify the bundle it came from -- circular, since unverified code was
    already running. TAR_SHA256 is substituted at launch from the publishing
    machine, so S3 cannot influence it.
    """
    s = WRAPPER.read_text()
    assert 'TAR_SHA256="${TAR_SHA256}"' in s
    assert "${#TAR_SHA256} -eq 64" in s
    assert "sha256sum medzen_code.tgz" in s
    assert "ARCHIVE HASH VERIFIED against user-data" in s
    # and it must happen before anything from the bundle is executed
    assert s.index("ARCHIVE HASH VERIFIED") < s.index("bash /opt/boot/src/pipeline/build_image.sh")


def test_wrapper_extracts_safely():
    s = WRAPPER.read_text()
    assert 'filter="data"' in s, "unverified archives must not be extracted unfiltered"
    # check CODE, not the comment that explains why shell tar is unsuitable
    code = "\n".join(l for l in s.splitlines() if not l.lstrip().startswith("#"))
    assert "tar xzf" not in code, "shell tar gives no traversal protection here"


def test_wrapper_verifies_before_executing_and_full_set():
    s = WRAPPER.read_text()
    assert s.index("BUNDLE VERIFIED") < s.index("bash /opt/boot/src/pipeline/build_image.sh")
    assert "declared - on_disk" in s and "on_disk - declared" in s


def test_every_wrapper_failure_terminates():
    """A builder that limps on after a failed download is how a half-built
    image reaches a registry."""
    s = WRAPPER.read_text()
    assert "shutdown -h now" in s.split("die()")[1].split("}")[0], \
        "die() must terminate the instance"
    for critical in ("bundle download from", "BUNDLE.json download from",
                     "archive sha256 mismatch", "bundle verification",
                     "build_image.sh absent from the verified bundle"):
        assert critical in s, f"missing a die() guard for: {critical}"


def test_build_image_says_why_it_cannot_verify_itself():
    s = BUILD.read_text()
    assert "pre-verified by the trusted wrapper" in s
    assert "proves nothing" in s


def test_trainer_launcher_also_verifies_against_user_data():
    s = TRAINER.read_text()
    assert 'EXPECT_TAR="${TAR_SHA256}"' in s
    assert "ARCHIVE HASH VERIFIED against user-data" in s
    assert 'filter="data"' in s
    assert s.index("ARCHIVE HASH VERIFIED") < s.index("bootstrap_trainer.sh")


def test_bundle_archive_is_reproducible():
    """The trust root must be recomputable, not an incidental value.

    gzip records the current time and tar records mtimes and ownership, so an
    unnormalised archive hashes differently on every publish -- meaning
    TAR_SHA256 could only ever be taken on faith from the run that produced it.
    """
    s = PUBLISH.read_text()
    assert "mtime=0" in s and "ti.mtime = 0" in s
    assert "ti.uid = ti.gid = 0" in s
    assert "PAX_FORMAT" in s


def test_publish_prints_the_hash_for_launchers():
    s = PUBLISH.read_text()
    assert 'print(f"TAR_SHA256={tar_sha}")' in s
    assert "never by reading BUNDLE.json back out of S3" in s


# --------------------------------------------------------------------------- #
# digest validation: exactly sha256: + 64 lowercase hex
# --------------------------------------------------------------------------- #
CONTAINER = ROOT / "pipeline" / "container_userdata.sh"


def test_build_validates_the_digest_ecr_returns():
    """The CLI returns the string "None" for a missing image, and that would be
    recorded as the artifact identity and pinned by deployments."""
    s = BUILD.read_text()
    assert "${#DIGEST_HEX} -ne 64" in s
    assert 'DIGEST_HEX="${DIGEST#sha256:}"' in s
    assert "digest not lowercase hex" in s
    assert "exit 35" in s


def test_container_launcher_validates_the_digest():
    s = CONTAINER.read_text()
    assert "${#DIGEST_HEX} -eq 64" in s
    assert "digest must start with sha256:" in s
    assert "digest not lowercase hex" in s
    # validation must read the RUNTIME variable, not the render-time placeholder:
    # envsubst leaves ${VAR:-} alone, so the latter evaluates empty on the box
    assert 'case "$DIGEST" in' in s


def test_container_launcher_verifies_the_pulled_digest():
    s = CONTAINER.read_text()
    assert "DIGEST VERIFIED" in s
    assert '[ "$GOT" = "$DIGEST" ]' in s


def test_container_launcher_documents_the_imds_hop_limit():
    """A container on the bridge network is one hop further from IMDS; EC2's
    default limit of 1 denies it credentials and looks like an IAM fault."""
    s = CONTAINER.read_text()
    assert "HttpPutResponseHopLimit=2" in s


def test_container_launcher_is_valid_bash():
    r = subprocess.run(["bash", "-n", str(CONTAINER)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_wrapper_ships_its_boot_log_on_success_too():
    """The archive-hash check is the control this wrapper exists for. A
    successful run that leaves the evidence on a terminated instance proves
    nothing afterwards -- the same failure mode as the preflights that died
    with nothing uploaded."""
    s = WRAPPER.read_text()
    assert "ship_boot_log()" in s
    assert "BOOT_SHIPPER" in s, "the log must ship continuously, not only at the end"
    assert "BOOTSTRAP EVIDENCE:" in s
    # evidence must be recorded before control passes to bundle code
    assert s.index("BOOTSTRAP EVIDENCE") < s.index("bash /opt/boot/src/pipeline/build_image.sh")


# --------------------------------------------------------------------------- #
# OS base and Python dependency gates
# --------------------------------------------------------------------------- #
AUDIT = ROOT / "pipeline" / "audit_python_deps.sh"


def test_base_is_trixie_not_bookworm():
    """bookworm carried 3 CRITICAL + 5 HIGH in perl-base with NO FIX AVAILABLE,
    and perl-base is Essential so it cannot be removed. A newer base was the
    only remediation that did not mean waiving a CRITICAL."""
    s = DOCKERFILE.read_text()
    m = re.search(r"^FROM (\S+)@sha256:([0-9a-f]{64})\s*$", s, re.M)
    assert m, "base must still be pinned by digest"
    # the FROM line itself, not the comment that explains why bookworm was dropped
    assert "trixie" in m.group(1), f"expected a trixie base, got {m.group(1)}"
    assert "bookworm" not in m.group(1)


def test_pip_is_upgraded_before_installing():
    """The base ships a pip with 6 advisories against it."""
    s = DOCKERFILE.read_text()
    assert re.search(r'RUN pip install --no-cache-dir "pip==\d', s)
    assert s.index("pip==") < s.index("RUN pip install -r requirements.txt")


def test_python_dependency_audit_runs_at_build():
    s = DOCKERFILE.read_text()
    assert "RUN bash pipeline/audit_python_deps.sh" in s
    # before the verify gate, so the gate proves the env survived the cleanup
    assert s.index("audit_python_deps.sh") < s.index("MODE=verify")


def test_audit_script_is_valid_and_fails_closed():
    r = subprocess.run(["bash", "-n", str(AUDIT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    s = AUDIT.read_text()
    assert "--strict" in s, "pip-audit must fail on findings, not just report them"
    assert "exit 51" in s
    assert "Do not waive" in s
    assert 'PIP_AUDIT_VERSION="${PIP_AUDIT_VERSION:-' in s, "the audit tool must be pinned"


def test_audit_never_mutates_the_target_environment():
    """Removing audit packages by NAME does not undo a version UPGRADE to a
    package that was already installed, and pip check validates compatibility
    rather than exact versions -- so the image could ship a dependency set
    different from the pinned one it was verified as. pip-audit therefore runs
    from its own venv against an exported freeze, touching nothing.
    """
    s = AUDIT.read_text()
    # check CODE throughout: the comments deliberately NAME the things that must
    # be absent, so scanning the whole file matches its own explanations
    code = "\n".join(l for l in s.splitlines() if not l.lstrip().startswith("#"))
    assert 'python -m venv "$AUDIT_VENV"' in code
    assert "--system-site-packages" not in code, "the audit venv must NOT see the target env"
    assert '-r "$FREEZE_BEFORE"' in code, "audit the exported freeze, not the live env"
    assert "--no-deps" in code
    # pip-audit must be installed into the audit venv only
    assert '"$AUDIT_VENV/bin/pip" install -q "pip-audit==' in code
    assert "\npip install" not in code, "nothing may be installed into the target env"


def test_audit_proves_the_freeze_is_byte_identical():
    """The isolation means nothing can be mutated; this proves it was not."""
    s = AUDIT.read_text()
    assert 'diff -q "$FREEZE_BEFORE" "$FREEZE_AFTER"' in s
    assert "FREEZE UNCHANGED" in s
    assert "exit 52" in s, "a changed environment must fail the build"
    assert "pip freeze --all" in s, "--all so pip/setuptools/wheel are covered too"


def test_audit_refuses_an_unauditable_freeze():
    """Editable or local installs cannot be audited by pin, and auditing less
    than the whole set silently is worse than failing."""
    s = AUDIT.read_text()
    assert "editable or local installs" in s
    assert "@ file://" in s


def test_audit_is_not_in_the_runtime_path():
    """pip-audit needs the network; the trainer deliberately has no guarantee of
    it, so an audit at container start would fail closed on a healthy host."""
    entry = (ROOT / "pipeline" / "container_entrypoint.sh").read_text()
    assert "audit_python_deps" not in entry
    boot = (ROOT / "pipeline" / "bootstrap_trainer.sh").read_text()
    assert "pip-audit" not in boot
