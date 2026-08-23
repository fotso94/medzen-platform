#!/usr/bin/env python3
"""Sign a promotion document with the dedicated KMS key (rounds 11-12).

  python scripts/sign_promotion_document.py <document> [<output>.sig]

Round 12 (Codex): before signing, the script VALIDATES its authority —
the ambient credentials must be in the MedZen account, the alias must
resolve to the expected key, and KMS's public key must match the
COMMITTED pem byte-for-byte. A wrong profile or a substituted key
refuses. Signing belongs in the owner-protected admission environment;
kms:Sign is granted to no pod role.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parents[1]
KEY_ALIAS = "alias/medzen-promotion-signing"
EXPECTED_ACCOUNT = "558069890522"
COMMITTED_PUBKEY = ROOT / "platform/decisions/PROMOTION-SIGNING-PUBLIC-KEY.pem"


def _validate_authority(kms) -> str:
    account = boto3.client("sts").get_caller_identity()["Account"]
    if account != EXPECTED_ACCOUNT:
        raise SystemExit(
            f"REFUSED: ambient credentials are account {account}, not the "
            f"MedZen account {EXPECTED_ACCOUNT} — wrong profile?")
    described = kms.describe_key(KeyId=KEY_ALIAS)
    key_arn = described["KeyMetadata"]["Arn"]
    if f":{EXPECTED_ACCOUNT}:" not in key_arn:
        raise SystemExit("REFUSED: alias resolves outside the MedZen account")
    der = kms.get_public_key(KeyId=KEY_ALIAS)["PublicKey"]
    from cryptography.hazmat.primitives.serialization import (
        Encoding, PublicFormat, load_der_public_key)
    pem = load_der_public_key(der).public_bytes(
        Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    if pem != COMMITTED_PUBKEY.read_bytes():
        raise SystemExit(
            "REFUSED: the KMS public key does not match the COMMITTED "
            "pem — a substituted key cannot sign promotion evidence")
    return key_arn


def main() -> int:
    document = Path(sys.argv[1])
    output = Path(sys.argv[2]) if len(sys.argv) > 2 else document.with_suffix(
        document.suffix + ".sig")
    kms = boto3.client("kms", region_name="eu-central-1")
    key_arn = _validate_authority(kms)
    digest = hashlib.sha256(document.read_bytes()).digest()
    response = kms.sign(
        KeyId=KEY_ALIAS, Message=digest, MessageType="DIGEST",
        SigningAlgorithm="ECDSA_SHA_256")
    output.write_bytes(response["Signature"])
    print(f"signed {document.name} -> {output.name} with {key_arn}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
