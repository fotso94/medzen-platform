#!/usr/bin/env python3
"""Verify the exact newline-terminated synthetic B6.6 bearer token file."""
from __future__ import annotations

import hashlib
import json
import stat
import sys
from pathlib import Path


TOKEN_PATH = Path("/private/tmp/medzen-b6-6-client-token")
BEARER_SHA256 = "fe83e1a29619c5b05b83b1d77d820dde850d35e6a75102947881e6d152d68be6"


class TokenBindingRefusal(RuntimeError):
    pass


def verify_bytes(value: bytes, expected_sha256: str = BEARER_SHA256) -> dict[str, object]:
    if len(value) != 44:
        raise TokenBindingRefusal("TOKEN_FILE_LENGTH_DIFFERS")
    if not value.endswith(b"\n") or value.endswith(b"\r\n"):
        raise TokenBindingRefusal("TOKEN_LINE_ENDING_DIFFERS")
    bearer = value[:-1]
    if len(bearer) != 43 or b"\n" in bearer or b"\r" in bearer:
        raise TokenBindingRefusal("TOKEN_BEARER_ENCODING_DIFFERS")
    if hashlib.sha256(bearer).hexdigest() != expected_sha256:
        raise TokenBindingRefusal("TOKEN_BEARER_HASH_DIFFERS")
    return {
        "bearer_bytes": 43,
        "bearer_sha256_verified": True,
        "file_bytes": 44,
        "line_ending": "LF",
    }


def verify_file(path: Path, *, require_exact_path: bool = True) -> dict[str, object]:
    if require_exact_path and path != TOKEN_PATH:
        raise TokenBindingRefusal("TOKEN_PATH_DIFFERS")
    try:
        metadata = path.stat()
        value = path.read_bytes()
    except OSError as exc:
        raise TokenBindingRefusal("TOKEN_FILE_UNREADABLE") from exc
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        raise TokenBindingRefusal("TOKEN_MODE_DIFFERS")
    return verify_bytes(value)


def main() -> int:
    if len(sys.argv) != 2:
        print(json.dumps({"reason_code": "TOKEN_ARGUMENTS_DIFFER"}, sort_keys=True))
        return 2
    try:
        result = verify_file(Path(sys.argv[1]))
        print(json.dumps(result, sort_keys=True))
        return 0
    except TokenBindingRefusal as exc:
        print(json.dumps({"reason_code": str(exc)}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
