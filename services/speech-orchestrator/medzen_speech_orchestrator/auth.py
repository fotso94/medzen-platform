from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class AuthRefusal(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class ClientIdentity:
    client_id: str


class LocalKeyStore:
    """Hash-only client-key fixture for the B6.3 local boundary."""

    def __init__(self, path: Path):
        try:
            value: Any = json.loads(path.read_bytes())
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise AuthRefusal(
                "AUTH_STORE_UNAVAILABLE", "client key store is unavailable", 503
            ) from exc
        if not isinstance(value, dict) or set(value) != {
            "schema_version", "classification", "clients"
        }:
            raise AuthRefusal(
                "AUTH_STORE_UNAVAILABLE", "client key store is malformed", 503
            )
        if (
            value["schema_version"] != 1
            or value["classification"] != "B6_3_LOCAL_SYNTHETIC_ONLY"
            or not isinstance(value["clients"], list)
            or not value["clients"]
        ):
            raise AuthRefusal(
                "AUTH_STORE_UNAVAILABLE", "client key store is not local-only", 503
            )
        clients: list[tuple[str, str]] = []
        identities: set[str] = set()
        hashes: set[str] = set()
        for entry in value["clients"]:
            if not isinstance(entry, dict) or set(entry) != {
                "client_id", "key_sha256", "enabled"
            }:
                raise AuthRefusal(
                    "AUTH_STORE_UNAVAILABLE", "client key entry is malformed", 503
                )
            client_id = entry["client_id"]
            digest = entry["key_sha256"]
            if (
                not isinstance(client_id, str)
                or not client_id
                or client_id in identities
                or not isinstance(digest, str)
                or SHA256_RE.fullmatch(digest) is None
                or digest in hashes
                or not isinstance(entry["enabled"], bool)
            ):
                raise AuthRefusal(
                    "AUTH_STORE_UNAVAILABLE", "client key identity is ambiguous", 503
                )
            identities.add(client_id)
            hashes.add(digest)
            if entry["enabled"]:
                clients.append((client_id, digest))
        if not clients:
            raise AuthRefusal(
                "AUTH_STORE_UNAVAILABLE", "no local client key is enabled", 503
            )
        self._clients = tuple(clients)

    def authenticate(self, authorization: str | None) -> ClientIdentity:
        if authorization is None:
            raise AuthRefusal("AUTH_REQUIRED", "bearer authentication is required", 401)
        if not authorization.startswith("Bearer "):
            raise AuthRefusal("AUTH_INVALID", "bearer authentication is invalid", 403)
        token = authorization[7:]
        if not token or token.strip() != token or any(char.isspace() for char in token):
            raise AuthRefusal("AUTH_INVALID", "bearer authentication is invalid", 403)
        supplied = hashlib.sha256(token.encode("utf-8")).hexdigest()
        matched: str | None = None
        for client_id, expected in self._clients:
            if hmac.compare_digest(supplied, expected):
                matched = client_id
        if matched is None:
            raise AuthRefusal("AUTH_INVALID", "bearer authentication is invalid", 403)
        return ClientIdentity(matched)
