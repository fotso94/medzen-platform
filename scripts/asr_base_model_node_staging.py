#!/usr/bin/env python3
"""Canonical, account-name-independent node-local staging commands."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from typing import Any, Iterable


STAGING_UID = 10001
STAGING_GID = 10001
STAGING_SSM_TIMEOUT_SECONDS = 1800
STAGING_PRESIGNED_URL_SECONDS = 3600
STAGING_URL_SAFETY_MARGIN_SECONDS = 600

NUMERIC_IDENTITY_PREFIX = (
    "/usr/bin/sudo",
    "/usr/sbin/chroot",
    f"--userspec={STAGING_UID}:{STAGING_GID}",
    "/",
    "/usr/bin/env",
    "-i",
    "HOME=/tmp",
    "PATH=/usr/local/bin:/usr/bin:/bin",
    "/bin/sh",
    "-ec",
)

REQUIRED_NODE_EXECUTABLES = (
    "/bin/bash",
    "/bin/sh",
    "/usr/bin/base64",
    "/usr/bin/cat",
    "/usr/bin/chmod",
    "/usr/bin/chown",
    "/usr/bin/curl",
    "/usr/bin/cut",
    "/usr/bin/env",
    "/usr/bin/find",
    "/usr/bin/grep",
    "/usr/bin/id",
    "/usr/bin/install",
    "/usr/bin/printf",
    "/usr/bin/rm",
    "/usr/bin/sha256sum",
    "/usr/bin/sleep",
    "/usr/bin/stat",
    "/usr/bin/sudo",
    "/usr/bin/tar",
    "/usr/bin/tee",
    "/usr/bin/wc",
    "/usr/sbin/chroot",
)


class NodeStagingRefusal(RuntimeError):
    def __init__(self, reason_code: str, detail: str):
        super().__init__(detail)
        self.reason_code = reason_code
        self.detail = detail


def numeric_identity_command(script: str) -> str:
    """Execute a shell fragment as numeric UID/GID with no inherited env.

    GNU ``chroot --userspec`` accepts numeric identities without consulting
    passwd/group databases. This is intentionally not ``sudo -u '#10001'``:
    Amazon Linux sudo treats that spelling as an account name.
    """

    if not isinstance(script, str) or not script.strip() or "\x00" in script:
        raise NodeStagingRefusal(
            "NODE_STAGING_SCRIPT_MALFORMED",
            "numeric staging script is absent or contains NUL",
        )
    return shlex.join((*NUMERIC_IDENTITY_PREFIX, script))


def root_command(*argv: str) -> str:
    if not argv or any(not isinstance(value, str) or not value for value in argv):
        raise NodeStagingRefusal(
            "NODE_STAGING_ROOT_ARGV_MALFORMED",
            "root staging argv is absent or malformed",
        )
    return shlex.join(argv)


def staging_prelude(attempt: int) -> tuple[list[str], str]:
    if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
        raise NodeStagingRefusal(
            "NODE_STAGING_ATTEMPT_MALFORMED", "staging attempt is malformed"
        )
    base = f"/var/lib/medzen-asr-eval/attempt-{attempt}"
    executable_tests = " ".join(shlex.quote(value) for value in REQUIRED_NODE_EXECUTABLES)
    commands = [
        "#!/bin/bash",
        "set -euo pipefail",
        "export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "unset CDPATH ENV BASH_ENV USER LOGNAME",
        f"for executable in {executable_tests}; do /usr/bin/test -x \"$executable\"; done",
        "/usr/bin/test \"$(/usr/bin/id -u)\" = 0",
        root_command("/usr/bin/sudo", "/usr/bin/rm", "-rf", "--", base),
        root_command(
            "/usr/bin/sudo",
            "/usr/bin/install",
            "-d",
            "-o",
            str(STAGING_UID),
            "-g",
            str(STAGING_GID),
            f"{base}/input",
            f"{base}/input/audio",
            f"{base}/input/models/whisper-large-v3-ct2",
            f"{base}/output",
        ),
    ]
    return commands, base


def install_directory(path: str) -> str:
    return root_command(
        "/usr/bin/sudo",
        "/usr/bin/install",
        "-d",
        "-o",
        str(STAGING_UID),
        "-g",
        str(STAGING_GID),
        path,
    )


def download_file(url: str, destination: str) -> str:
    curl = root_command(
        "/usr/bin/curl",
        "--fail",
        "--silent",
        "--show-error",
        "--location",
        "--connect-timeout",
        "15",
        "--max-time",
        "300",
        "--proto",
        "=https",
        "--tlsv1.2",
        url,
        "-o",
        destination,
    )
    # curl 6 = DNS, 28 = timeout, 56 = receive/reset.  HTTP failures (22),
    # authentication, policy and all other errors fail immediately.  A partial
    # destination is removed before the exact idempotent read is repeated.
    inner = "\n".join(
        (
            "attempt=1",
            "while /usr/bin/test \"$attempt\" -le 3; do",
            "  set +e",
            f"  {curl}",
            "  code=$?",
            "  set -e",
            "  if /usr/bin/test \"$code\" = 0; then exit 0; fi",
            "  case \"$code\" in",
            "    6) category=DNS_BLIP ;;",
            "    28) category=TIMEOUT ;;",
            "    56) category=CONNECTION_RESET ;;",
            "    *) exit \"$code\" ;;",
            "  esac",
            f"  /usr/bin/rm -f -- {shlex.quote(destination)}",
            "  if /usr/bin/test \"$attempt\" = 3; then",
            "    /usr/bin/printf 'MEDZEN_TRANSIENT_S3_READ_EXHAUSTED category=%s attempts=3\\n' \"$category\" >&2",
            "    exit 86",
            "  fi",
            "  if /usr/bin/test \"$attempt\" = 1; then delay=1; else delay=2; fi",
            "  /usr/bin/sleep \"$delay\"",
            "  attempt=$((attempt + 1))",
            "done",
            "exit 86",
        )
    )
    return numeric_identity_command(inner)


def concatenate_files(parts: Iterable[str], destination: str) -> str:
    normalized = tuple(parts)
    if not normalized:
        raise NodeStagingRefusal(
            "NODE_STAGING_PARTS_ABSENT", "staging assembly has no parts"
        )
    inner = (
        root_command("/usr/bin/cat", *normalized)
        + " > "
        + shlex.quote(destination)
    )
    return numeric_identity_command(inner)


def extract_archive(archive: str, directory: str) -> str:
    return numeric_identity_command(
        root_command(
            "/usr/bin/tar",
            "--extract",
            "--file",
            archive,
            "--directory",
            directory,
            "--no-same-owner",
            "--no-same-permissions",
        )
    )


def write_base64(value: str, destination: str) -> str:
    inner = (
        root_command("/usr/bin/printf", "%s", value)
        + " | /usr/bin/base64 -d | "
        + root_command("/usr/bin/tee", destination)
        + " >/dev/null"
    )
    return numeric_identity_command(inner)


def verify_sha256(path: str, expected: str) -> str:
    if re.fullmatch(r"[0-9a-f]{64}", expected) is None:
        raise NodeStagingRefusal(
            "NODE_STAGING_SHA256_MALFORMED", "expected staging SHA-256 is malformed"
        )
    return (
        "/usr/bin/test \"$(/usr/bin/sha256sum "
        + shlex.quote(path)
        + " | /usr/bin/cut -d' ' -f1)\" = "
        + shlex.quote(expected)
    )


def verify_size(path: str, expected: int) -> str:
    if not isinstance(expected, int) or isinstance(expected, bool) or expected < 0:
        raise NodeStagingRefusal(
            "NODE_STAGING_SIZE_MALFORMED", "expected staging byte size is malformed"
        )
    return (
        "/usr/bin/test \"$(/usr/bin/stat -c %s "
        + shlex.quote(path)
        + ")\" = "
        + str(expected)
    )


def audit_staging_commands(commands: list[str]) -> dict[str, Any]:
    """Fail closed on account lookup, ambient env or relative-tool assumptions."""

    if not isinstance(commands, list) or not commands:
        raise NodeStagingRefusal(
            "NODE_STAGING_COMMANDS_ABSENT", "node staging commands are absent"
        )
    body = "\n".join(commands)
    prohibited = {
        "sudo_user_selector": r"(?:^|\s)(?:sudo|/usr/bin/sudo)\s+(?:-u|--user)",
        "account_switch_tool": r"(?:^|\s)(?:su|runuser)(?:\s|$)",
        "passwd_lookup": r"(?:getent\s+passwd|pwd\.getpw|/etc/passwd)",
        "ambient_home": r"(?:\$HOME|\$\{HOME\}|~/)",
        "ambient_user": r"(?:\$USER|\$LOGNAME|\$\{LOGNAME\})",
    }
    violations = [name for name, pattern in prohibited.items() if re.search(pattern, body)]
    if violations:
        raise NodeStagingRefusal(
            "NODE_STAGING_ENVIRONMENT_ASSUMPTION",
            f"node staging contains prohibited assumptions: {','.join(violations)}",
        )
    relative_tools = sorted(
        {
            match.group(1)
            for match in re.finditer(
                r"(?<![/A-Za-z0-9_.-])"
                r"(base64|cat|chmod|chown|chroot|curl|cut|env|find|id|install|"
                r"printf|rm|sha256sum|sleep|stat|sudo|tar|tee|test|wc)"
                r"(?=\s|$)",
                body,
            )
        }
    )
    if relative_tools:
        raise NodeStagingRefusal(
            "NODE_STAGING_RELATIVE_EXECUTABLE",
            "node staging contains relative executable names: "
            + ",".join(relative_tools),
        )
    numeric = [
        command
        for command in commands
        if command.startswith("/usr/bin/sudo /usr/sbin/chroot ")
    ]
    if not numeric or any(
        f"--userspec={STAGING_UID}:{STAGING_GID}" not in command
        or "/usr/bin/env -i" not in command
        or "HOME=/tmp" not in command
        for command in numeric
    ):
        raise NodeStagingRefusal(
            "NODE_STAGING_NUMERIC_IDENTITY_DIFFERS",
            "numeric staging commands do not share the canonical empty-environment wrapper",
        )
    if commands[0] != "#!/bin/bash" or commands[1] != "set -euo pipefail":
        raise NodeStagingRefusal(
            "NODE_STAGING_SHELL_BOUNDARY_DIFFERS",
            "staging does not select bounded bash semantics explicitly",
        )
    if STAGING_PRESIGNED_URL_SECONDS < (
        STAGING_SSM_TIMEOUT_SECONDS + STAGING_URL_SAFETY_MARGIN_SECONDS
    ):
        raise NodeStagingRefusal(
            "NODE_STAGING_URL_WINDOW_INSUFFICIENT",
            "presigned URL lifetime does not cover SSM staging plus its safety margin",
        )
    return {
        "status": "PASS_NODE_STAGING_ASSUMPTION_AUDIT",
        "command_count": len(commands),
        "numeric_identity_command_count": len(numeric),
        "uid": STAGING_UID,
        "gid": STAGING_GID,
        "inherited_environment_values": 0,
        "passwd_or_group_name_lookups": 0,
        "relative_staging_executables": 0,
        "ssm_timeout_seconds": STAGING_SSM_TIMEOUT_SECONDS,
        "presigned_url_seconds": STAGING_PRESIGNED_URL_SECONDS,
        "url_safety_margin_seconds": STAGING_URL_SAFETY_MARGIN_SECONDS,
        "idempotent_s3_read_retry": {
            "maximum_attempts": 3,
            "backoff_seconds": [1, 2],
            "curl_transient_exit_codes": {
                "6": "DNS_BLIP",
                "28": "TIMEOUT",
                "56": "CONNECTION_RESET",
            },
            "per_attempt_max_seconds": 300,
            "hard_cap_seconds": 903,
            "other_exit_codes_retryable": False,
            "verification_failures_retryable": False,
        },
        "command_bundle_sha256": hashlib.sha256(
            json.dumps(commands, sort_keys=True, separators=(",", ":")).encode()
            + b"\n"
        ).hexdigest(),
    }
