"""Arm-2 training-identity-index PRODUCER (Codex round 35 findings 1/5) —
the hash-chained derivation from the exact pinned gb9/gb8/gb3 sources, proven
offline with fixture corpora and injected digests. AWS is impossible in the
test path; the live path fails closed without the dedicated role.
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

from build_arm2_training_identity_index import (  # noqa: E402
    ProducerRefusal, build_training_identity_index, enumerate_corpus,
    main, produce_live)
from mint_arm2_nomination_split import (  # noqa: E402
    MintRefusal, _canon, _validate_training_structure,
    committed_training_source_digests, mint_phase_a_split)


def _ck(tag: str, i: int) -> str:
    return hashlib.sha256(f"{tag}:{i}".encode()).hexdigest()


def _fixture_world(rows_per_manifest: int = 3):
    """A synthetic three-corpus world whose COMPLETE listings are HASH-CHAINED
    exactly like the real one: adoption digest -> listing bytes -> per-manifest
    sha -> rows. The injected `committed` map plays the adoption records."""
    store: dict[str, bytes] = {}
    committed: dict[str, str] = {}
    for dataset in ("gb3", "gb8", "gb9"):
        manifests = {}
        for label in (f"lang{dataset}/asr/cfg1", f"lang{dataset}/asr/cfg2"):
            key = f"curated/{label}/{dataset}/manifest.jsonl"
            rows = [{"audio_checksum_sha256": _ck(key, i),
                     "text": "TRANSCRIPT MUST NEVER SURVIVE"}
                    for i in range(rows_per_manifest)]
            body = "".join(json.dumps(r) + "\n" for r in rows).encode()
            store[key] = body
            manifests[label] = {"sha256": hashlib.sha256(body).hexdigest()}
        listing = json.dumps({"manifests": manifests},
                             sort_keys=True).encode()
        comp_key = f"curated/_versions/{dataset}/COMPLETE.json"
        store[comp_key] = listing
        committed[dataset] = hashlib.sha256(listing).hexdigest()
    return store, committed


def _get_object(store):
    def get_object(key: str) -> bytes:
        return store[key]
    return get_object


def test_hash_chained_enumeration_builds_a_validating_artifact():
    store, committed = _fixture_world()
    documents = {d: enumerate_corpus(_get_object(store), d, committed=committed)
                 for d in sorted(committed)}
    artifact = build_training_identity_index(documents, committed=committed)
    # the artifact is structurally valid (authentication is the ledger's job)
    identities = _validate_training_structure(artifact)
    assert len(identities) == 18            # 3 corpora x 2 manifests x 3 rows
    assert artifact["row_count"] == 18
    assert artifact["unique_count"] == 18


def test_transcript_text_never_reaches_the_artifact():
    store, committed = _fixture_world()
    documents = {d: enumerate_corpus(_get_object(store), d, committed=committed)
                 for d in sorted(committed)}
    # the enumeration itself already strips to identity-only rows
    for doc in documents.values():
        for rows in doc["manifest_rows"].values():
            for row in rows:
                assert set(row) == {"audio_checksum_sha256"}
    artifact = build_training_identity_index(documents, committed=committed)
    assert "TRANSCRIPT" not in json.dumps(artifact)


def test_listing_hash_mismatch_refuses():
    store, committed = _fixture_world()
    committed["gb9"] = "0" * 64
    with pytest.raises(ProducerRefusal, match="unpinned corpus listing"):
        enumerate_corpus(_get_object(store), "gb9", committed=committed)


def test_manifest_sha_mismatch_refuses():
    store, committed = _fixture_world()
    key = next(k for k in store if k.endswith("manifest.jsonl"))
    store[key] = store[key] + b'{"audio_checksum_sha256": "extra"}\n'
    dataset = key.split("/")[4]
    with pytest.raises(ProducerRefusal, match="completion record declares"):
        enumerate_corpus(_get_object(store), dataset, committed=committed)


def test_missing_corpus_refuses():
    store, committed = _fixture_world()
    documents = {d: enumerate_corpus(_get_object(store), d, committed=committed)
                 for d in ("gb3", "gb8")}
    with pytest.raises(ProducerRefusal, match="ALL pinned corpora"):
        build_training_identity_index(documents, committed=committed)


def test_empty_manifest_and_malformed_identity_refuse():
    store, committed = _fixture_world()
    good = {d: enumerate_corpus(_get_object(store), d, committed=committed)
            for d in sorted(committed)}
    empty = {d: dict(doc) for d, doc in good.items()}
    empty["gb9"] = dict(good["gb9"], manifest_rows={})
    with pytest.raises(ProducerRefusal, match="no manifest rows"):
        build_training_identity_index(empty, committed=committed)
    bad = {d: {"complete_raw_bytes": doc["complete_raw_bytes"],
               "manifest_rows": {k: list(v) for k, v in
                                 doc["manifest_rows"].items()}}
           for d, doc in good.items()}
    first = next(iter(bad["gb9"]["manifest_rows"]))
    bad["gb9"]["manifest_rows"][first].append(
        {"audio_checksum_sha256": "NOT-HEX"})
    with pytest.raises(ProducerRefusal, match="malformed"):
        build_training_identity_index(bad, committed=committed)


def test_unknown_dataset_and_bad_label_refuse():
    store, committed = _fixture_world()
    with pytest.raises(ProducerRefusal, match="not a pinned training corpus"):
        enumerate_corpus(_get_object(store), "gb999", committed=committed)
    listing = json.dumps({"manifests": {"not-a-triple": {"sha256": "0" * 64}}},
                         sort_keys=True).encode()
    store["curated/_versions/gb9/COMPLETE.json"] = listing
    committed["gb9"] = hashlib.sha256(listing).hexdigest()
    with pytest.raises(ProducerRefusal, match="lang/task/cfg"):
        enumerate_corpus(_get_object(store), "gb9", committed=committed)


def test_produce_live_composes_all_corpora_against_the_real_records():
    """produce_live uses the REAL committed digests — fixture bytes cannot
    match them, so the hash chain refuses (proving the binding is to the
    committed adoption records, not the fixture)."""
    store, _ = _fixture_world()
    with pytest.raises(ProducerRefusal, match="adoption record pins"):
        produce_live(_get_object(store))


def test_artifact_feeds_the_mint_as_the_training_exclusion():
    """End-to-end: the produced artifact is exactly what the mint's structured
    validation consumes (with the same injected committed map)."""
    store, committed = _fixture_world()
    documents = {d: enumerate_corpus(_get_object(store), d, committed=committed)
                 for d in sorted(committed)}
    artifact = build_training_identity_index(documents, committed=committed)
    # structural validity holds; the source binding to the REAL committed
    # adoption records is enforced by the producer (test_produce_live_*) and by
    # the consumer's ledger admission (authenticate_training_index), not by the
    # structural validator. The artifact must round-trip through the ONE
    # canonical serializer (no admission/governance field).
    assert _canon(artifact)
    assert "admission" not in artifact and "governance" not in artifact
    assert _validate_training_structure(artifact)


def test_producer_touches_no_aws_sdk(monkeypatch):
    real_import = builtins.__import__

    def no_aws(name, *a, **k):
        if name.split(".")[0] in ("boto3", "botocore"):
            raise AssertionError("AWS must be impossible in test mode")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_aws)
    store, committed = _fixture_world()
    documents = {d: enumerate_corpus(_get_object(store), d, committed=committed)
                 for d in sorted(committed)}
    artifact = build_training_identity_index(documents, committed=committed)
    assert artifact["unique_count"] == 18


def test_cli_offline_produces_nothing():
    with pytest.raises(SystemExit, match="produces nothing"):
        main([])


def test_cli_live_fails_closed_without_aws_sdk(monkeypatch):
    real_import = builtins.__import__

    def no_aws(name, *a, **k):
        if name.split(".")[0] in ("boto3", "botocore"):
            raise ImportError("no AWS in tests")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_aws)
    with pytest.raises(SystemExit, match="protected workflow"):
        main(["--live"])


def test_producer_cli_reads_the_real_packet_aws_and_fails_closed(monkeypatch):
    """Codex round 38 #1: drive the producer --live CLI against the ACTUAL
    committed packet — it must read aws.account + the producer role (the packet
    regeneration had dropped the aws block) and then fail closed, not error on a
    missing contract."""
    import sys, types
    from pathlib import Path
    repo = Path(__file__).resolve().parents[1]
    pkt = json.loads((repo / "platform/decisions/"
                      "B5-UNIVERSAL-ARM2-NOMINATION-LIVE-MINT-PACKET-2026-001.json"
                      ).read_bytes())
    account = pkt["aws"]["account"]
    role = pkt["training_identity_index"]["producer_role"]["role_name"]
    arn = f"arn:aws:sts::{account}:assumed-role/{role}/run"

    class _STS:
        def get_caller_identity(self):
            return {"Account": account, "Arn": arn}

    class _S3:
        def get_object(self, **kw):
            return {"Body": types.SimpleNamespace(read=lambda: b"{}")}

    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(
        client=lambda svc: _STS() if svc == "sts" else _S3()))
    from build_arm2_training_identity_index import main as producer_main
    with pytest.raises((SystemExit, ProducerRefusal)):
        producer_main(["--live"])   # gets past aws/identity, fails on the corpus hash
