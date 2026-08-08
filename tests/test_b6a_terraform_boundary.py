from __future__ import annotations

import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "scripts" / "terraform_medzen.sh"


def _fake_tools(tmp_path: Path) -> Path:
    tools = tmp_path / "bin"
    tools.mkdir(parents=True)
    aws = tools / "aws"
    aws.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"$*\" == *\"--query Account\"* ]]; then\n"
        "  printf '%s\\n' \"${FAKE_ACCOUNT:?}\"\n"
        "else\n"
        "  printf '%s\\n' \"${FAKE_CALLER:?}\"\n"
        "fi\n"
    )
    terraform = tools / "terraform"
    terraform.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" > \"${FAKE_TERRAFORM_MARKER:?}\"\n"
    )
    aws.chmod(0o755)
    terraform.chmod(0o755)
    return tools


def _run(tmp_path: Path, *, profile: str | None, account: str, caller: str):
    marker = tmp_path / "terraform-called"
    env = {
        **os.environ,
        "PATH": f"{_fake_tools(tmp_path)}:{os.environ['PATH']}",
        "FAKE_ACCOUNT": account,
        "FAKE_CALLER": caller,
        "FAKE_TERRAFORM_MARKER": str(marker),
    }
    if profile is None:
        env.pop("AWS_PROFILE", None)
    else:
        env["AWS_PROFILE"] = profile
    result = subprocess.run(
        ["bash", str(WRAPPER), "plan", "-out=test.tfplan"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, marker


def test_local_terraform_requires_explicit_medzen_profile(tmp_path):
    result, marker = _run(
        tmp_path,
        profile=None,
        account="558069890522",
        caller="arn:aws:iam::558069890522:user/s.fotso",
    )
    assert result.returncode == 2
    assert "set AWS_PROFILE=medzen explicitly" in result.stderr
    assert not marker.exists()


def test_local_terraform_refuses_wrong_account_or_caller(tmp_path):
    wrong_account, account_marker = _run(
        tmp_path / "account",
        profile="medzen",
        account="894565489253",
        caller="arn:aws:iam::894565489253:user/ai_user",
    )
    assert wrong_account.returncode == 2
    assert "expected 558069890522" in wrong_account.stderr
    assert not account_marker.exists()

    wrong_caller, caller_marker = _run(
        tmp_path / "caller",
        profile="medzen",
        account="558069890522",
        caller="arn:aws:iam::558069890522:role/unapproved",
    )
    assert wrong_caller.returncode == 2
    assert "expected arn:aws:iam::558069890522:user/s.fotso" in wrong_caller.stderr
    assert not caller_marker.exists()


def test_local_terraform_runs_only_for_exact_identity(tmp_path):
    result, marker = _run(
        tmp_path,
        profile="medzen",
        account="558069890522",
        caller="arn:aws:iam::558069890522:user/s.fotso",
    )
    assert result.returncode == 0
    invocation = marker.read_text()
    assert f"-chdir={ROOT / 'infra'}" in invocation
    assert "plan -out=test.tfplan" in invocation
