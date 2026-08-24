"""B6v2 rounds 11-12 (Codex): the promotion trust boundary.

The grade authority and the evidence ROOT are signed at ADMISSION time
with the dedicated KMS key (alias/medzen-promotion-signing). The RUNTIME
verifies OFFLINE against the public key committed in the repository and
baked into the loader image.

Round 12 (Codex, ENV_OVERRIDDEN_TRUST_ANCHOR_ACCEPTED): the environment
override is GONE — an operator env var is not a trust boundary. Test
keys enter ONLY through monkeypatching _public_key_bytes in tests;
production resolution is the image-baked path, then the repo-committed
file, nothing else.
"""
from __future__ import annotations

from pathlib import Path


class SignatureRefusal(RuntimeError):
    pass


def _public_key_bytes() -> bytes:
    candidates = [
        "/opt/medzen/PROMOTION-SIGNING-PUBLIC-KEY.pem",
        str(Path(__file__).resolve().parents[3]
            / "platform/decisions/PROMOTION-SIGNING-PUBLIC-KEY.pem"),
    ]
    for candidate in candidates:
        if Path(candidate).is_file():
            return Path(candidate).read_bytes()
    raise SignatureRefusal(
        "promotion signing public key is absent — the trust boundary "
        "cannot be established")


def _verify(document: bytes, signature: bytes, key_bytes: bytes,
            label: str) -> None:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import (
        load_pem_public_key,
    )
    public_key = load_pem_public_key(key_bytes)
    try:
        public_key.verify(signature, document, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature as exc:
        raise SignatureRefusal(label) from exc


def verify_signature(document: bytes, signature: bytes) -> None:
    """ECDSA P-256 / SHA-256, DER signature — exactly what
    kms:Sign(ECDSA_SHA_256) produces for the committed public key."""
    _verify(document, signature, _public_key_bytes(),
            "signature does not verify against the committed promotion "
            "signing key — the document was not produced by the "
            "admission authority")
