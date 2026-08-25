"""Arm-2 SEALED IDENTITY producer (final activation patch) — the identity-only
extraction from the pinned sealed manifests, proven offline with fixtures. AWS
is impossible in the test path; the live path fails closed without the dedicated
role. Transcript/reference fields never survive.
"""
from __future__ import annotations

import builtins
import hashlib
import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "scripts"))

from build_arm2_sealed_identity_index import (  # noqa: E402
    SealedProducerRefusal, build_sealed_authorities, produce_live, main)
from mint_arm2_nomination_split import _agg  # noqa: E402


def _ck(t, i):
    return hashlib.sha256(f"{t}:{i}".encode()).hexdigest()


def _world(n=3, pools=("a", "b")):
    sources, manifests = [], {}
    for name in pools:
        key = f"eval/{name}/sealed/manifest.jsonl"
        rows = [{"audio_checksum_sha256": _ck(key, i),
                 "text": "SECRET TRANSCRIPT", "score": 0.9} for i in range(n)]
        body = "".join(json.dumps(r) + "\n" for r in rows).encode()
        sources.append({"key": key, "class": "SEALED", "rows": n,
                        "sha256": hashlib.sha256(body).hexdigest(),
                        "s3_version_id": f"v-{key}"})
        manifests[key] = body
    return {"pinned_sources": sources}, manifests


def test_produces_identity_only_authorities():
    index, manifests = _world()
    out = build_sealed_authorities(manifests, index)
    assert len(out["authorities"]) == 2
    for a in out["authorities"]:
        assert set().union(*[{"key", "identities"}]) == {"key", "identities"}
        for c in a["identities"]:
            assert len(c) == 64
    # NOTHING but identities survives
    assert "SECRET" not in json.dumps(out) and "score" not in json.dumps(out)
    # the ledger aggregates reproduce
    for key, agg in out["ledger_aggregates"].items():
        ids = next(a["identities"] for a in out["authorities"] if a["key"] == key)
        assert agg["identity_aggregate_sha256"] == _agg(ids)[1]
        assert agg["identity_unique"] == len(ids)


def test_wrong_manifest_hash_refuses():
    index, manifests = _world()
    k = index["pinned_sources"][0]["key"]
    manifests[k] = manifests[k] + b'{"audio_checksum_sha256":"x"}\n'
    with pytest.raises(SealedProducerRefusal, match="unpinned sealed bytes"):
        build_sealed_authorities(manifests, index)


def test_wrong_row_count_refuses():
    index, manifests = _world(n=3)
    index["pinned_sources"][0]["rows"] = 5     # pin says 5, manifest has 3
    # recompute the sha so ONLY the count check can catch it
    index["pinned_sources"][0]["sha256"] = hashlib.sha256(
        manifests[index["pinned_sources"][0]["key"]]).hexdigest()
    with pytest.raises(SealedProducerRefusal, match="pin declares"):
        build_sealed_authorities(manifests, index)


def test_malformed_identity_refuses():
    index, manifests = _world()
    k = index["pinned_sources"][0]["key"]
    body = b'{"audio_checksum_sha256":"NOT-HEX"}\n' * 3
    manifests[k] = body
    index["pinned_sources"][0]["sha256"] = hashlib.sha256(body).hexdigest()
    with pytest.raises(SealedProducerRefusal, match="malformed"):
        build_sealed_authorities(manifests, index)


def test_incomplete_or_unpinned_pools_refuse():
    index, manifests = _world()
    manifests.pop(index["pinned_sources"][0]["key"])
    with pytest.raises(SealedProducerRefusal, match="ALL pinned sealed pools"):
        build_sealed_authorities(manifests, index)
    index2, m2 = _world()
    index2["pinned_sources"][0]["s3_version_id"] = None      # unpinned
    with pytest.raises(SealedProducerRefusal, match="unpinned SEALED pools"):
        build_sealed_authorities(m2, index2)


def test_produce_live_uses_the_injected_reader():
    index, manifests = _world()
    def get_object(key, version):
        assert version == f"v-{key}"
        return manifests[key]
    out = produce_live(get_object, index)
    assert len(out["authorities"]) == 2


def test_producer_touches_no_aws_sdk(monkeypatch):
    real = builtins.__import__
    def no_aws(name, *a, **k):
        if name.split(".")[0] in ("boto3", "botocore"):
            raise AssertionError("AWS must be impossible in test mode")
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", no_aws)
    index, manifests = _world()
    assert build_sealed_authorities(manifests, index)["authorities"]


def test_cli_offline_produces_nothing():
    with pytest.raises(SystemExit, match="produces nothing"):
        main([])
