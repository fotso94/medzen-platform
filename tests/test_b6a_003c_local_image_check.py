from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "scripts/check_b6a_003c_local_image.sh"


def test_local_image_check_fails_closed_on_tag_commit_and_identity():
    text = CHECK.read_text()
    assert "set -euo pipefail" in text
    assert "^medzen-asr-runtime:b6a-003c-" in text
    assert "exact 40-character source commit" in text
    assert "{{.Architecture}}" in text
    assert "{{.Config.User}}" in text
    assert "org.opencontainers.image.revision" in text
    assert '"$source_commit" == "$expected_commit"' in text


def test_local_image_check_uses_installed_status_and_excludes_build_tools():
    text = CHECK.read_text()
    assert "dpkg-query -s" in text
    assert "dpkg-query -W" not in text
    for forbidden in (
        "python3-pip-whl",
        "python3-setuptools-whl",
        "python3.12-venv",
        "/opt/venv/bin/pip",
        "/opt/venv/bin/pip3",
        "python -m pip --version",
    ):
        assert forbidden in text


def test_local_image_check_runs_runtime_smoke_and_security_gate():
    text = CHECK.read_text()
    for required in (
        "ctranslate2",
        "faster_whisper",
        "libcudart.so.12",
        "libcublas.so.12",
        "libcudnn.so.9",
        "docker scout cves --exit-code --only-severity critical,high",
        "PASS_B6A_003C_LOCAL_IMAGE",
    ):
        assert required in text
