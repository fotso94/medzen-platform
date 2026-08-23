"""B6v2 round 11 (Codex): the promotion trust boundary.

The grade authority and the admission receipt are signed at ADMISSION
time with the dedicated KMS key (alias/medzen-promotion-signing,
ECC_NIST_P256). The RUNTIME verifies those signatures OFFLINE against
the public key committed in the repository and baked into the loader
image — a boundary the bundle author cannot rewrite: rewriting it means
rewriting reviewed source and rebuilding the image through CI.
"""
from __future__ import annotations

import os
from pathlib import Path


class SignatureRefusal(RuntimeError):
    pass


def _public_key_bytes() -> bytes:
    """Resolution order: explicit env (tests), the image-baked path,
    then the repository-relative committed file."""
    candidates = [
        os.environ.get("MEDZEN_PROMOTION_PUBKEY_PATH"),
        "/opt/medzen/PROMOTION-SIGNING-PUBLIC-KEY.pem",
        str(Path(__file__).resolve().parents[3]
            / "platform/decisions/PROMOTION-SIGNING-PUBLIC-KEY.pem"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate).read_bytes()
    raise SignatureRefusal(
        "promotion signing public key is absent — the trust boundary "
        "cannot be established")


def verify_signature(document: bytes, signature: bytes) -> None:
    """ECDSA P-256 / SHA-256, DER signature — exactly what
    kms:Sign(ECDSA_SHA_256) produces for the committed public key."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import (
        load_pem_public_key,
    )

    public_key = load_pem_public_key(_public_key_bytes())
    try:
        public_key.verify(signature, document,
                           ec.ECDSA(hashes.SHA256()))
    except InvalidSignature as exc:
        raise SignatureRefusal(
            "signature does not verify against the committed promotion "
            "signing key — the document was not produced by the "
            "admission authority") from exc
