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
    s = BUILD.read_text()
    assert 'BUNDLE_BASE="s3://medzen-speech/candidates/bootstrap/$GIT_SHA"' in s, \
        "a shared path lets the builder fetch different bytes than were verified"
    assert '"$BUNDLE_BASE/medzen_code.tgz"' in s
    assert '"$BUNDLE_BASE/BUNDLE.json"' in s


def test_bundle_verification_covers_set_sizes_and_hashes():
    s = BUILD.read_text()
    # both directions of the set comparison
    assert "declared - on_disk" in s and "on_disk - declared" in s, \
        "an unexpected extra file in the image must fail the build"
    assert "FILE SET MISMATCH" in s
    assert 'meta["bytes"]' in s and 'meta["sha256"]' in s
    assert 'man["tar_sha256"]' in s, "the archive bytes themselves must be checked"
    assert "BUNDLE VERIFIED" in s


def test_verification_runs_before_the_build():
    s = BUILD.read_text()
    assert s.index("BUNDLE VERIFIED") < s.index("docker build"), \
        "verifying after building defeats the purpose"


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
    assert "SCAN THRESHOLD EXCEEDED" in s
    assert "exit 34" in s, "exceeding the threshold must fail the build"
    assert '"adoptable": scan_rc == 0' in s, "image.json must record adoptability"


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
