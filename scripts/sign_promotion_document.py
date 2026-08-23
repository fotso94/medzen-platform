#!/usr/bin/env python3
"""Sign a promotion document with the dedicated KMS key (round 11).

  python scripts/sign_promotion_document.py <document> [<output>.sig]

Signing happens ONLY here, at admission time, under owner-reviewed
credentials — kms:Sign is granted to no pod role. The runtime verifies
offline against the committed public key.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import boto3

KEY_ALIAS = "alias/medzen-promotion-signing"


def main() -> int:
    document = Path(sys.argv[1])
    output = Path(sys.argv[2]) if len(sys.argv) > 2 else document.with_suffix(
        document.suffix + ".sig")
    digest = hashlib.sha256(document.read_bytes()).digest()
    client = boto3.client("kms", region_name="eu-central-1")
    response = client.sign(
        KeyId=KEY_ALIAS, Message=digest, MessageType="DIGEST",
        SigningAlgorithm="ECDSA_SHA_256")
    output.write_bytes(response["Signature"])
    print(f"signed {document.name} -> {output.name} "
          f"({len(response['Signature'])} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
