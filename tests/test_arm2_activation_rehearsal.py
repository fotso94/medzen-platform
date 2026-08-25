"""Arm-2 nomination-mint END-TO-END CLI-COMPOSITION rehearsal (final activation
patch, owner item #6). Proves the ACTUAL composition — the real training-index
producer core AND the real sealed-identity producer core feeding the real
live_mint — reaches status=FROZEN, reading no sealed object, under an APPROVED
review binding the full trust manifest. Plus the caller workflow composition
(produce -> seal -> mint) is asserted statically.

Offline: AWS is impossible; every committed read is a monkeypatched fixture;
the S3 reader is injected.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "scripts"))

import mint_arm2_nomination_split as mint_mod  # noqa: E402
from mint_arm2_nomination_split import (  # noqa: E402
    NOMINATION_LANGUAGES, SEALED_EXCLUSION_LEDGER, TRAINING_INDEX_LEDGER,
    TRAINING_INDEX_RECORD, TRAINING_SOURCE_RECORDS, _agg, _canon, live_mint,
    read_pool_keys)
from build_arm2_sealed_identity_index import build_sealed_authorities  # noqa: E402
from build_arm2_training_identity_index import (  # noqa: E402
    build_training_identity_index, enumerate_corpus)

_ACCOUNT = "558069890522"
_MINT_ROLE = "medzen-arm2-nomination-mint-role"
_KMS = "arn:aws:kms:eu-central-1:558069890522:key/synthetic"


def _ck(t, i):
    return hashlib.sha256(f"{t}:{i}".encode()).hexdigest()


def _sha(b):
    return hashlib.sha256(b).hexdigest()


def _jsonl(entries):
    lines = []
    for i, e in enumerate(entries):
        e = dict(e, entry=i + 1)
        if i > 0:
            e["prev_sha256"] = _sha(lines[-1].encode())
        lines.append(_canon(e).decode())
    return ("\n".join(lines) + "\n").encode()


def _approval(rid, authorizes, field, value):
    return _canon({"record": rid, "authorizes": authorizes, field: value,
                   "owner_verbatim": "owner approves (rehearsal)"})


def test_end_to_end_cli_composition_reaches_frozen(monkeypatch):
    committed = {}
    n = 3

    # ---- synthetic exposure index: 4 nomination dev pools + candidate + sealed
    sources, store = [], {}

    def add(key, cls, role, lang, rows_n):
        rows = [{"audio_checksum_sha256": _ck(key, i), "language": lang}
                for i in range(rows_n)]
        body = "".join(json.dumps(r) + "\n" for r in rows).encode()
        sources.append({"key": key, "class": cls, "role": role, "language": lang,
                        "rows": rows_n, "sha256": _sha(body),
                        "s3_version_id": f"v-{key}"})
        store[key] = body

    for lang in NOMINATION_LANGUAGES:
        add(f"eval/{lang}/dev/manifest.jsonl", "BASE_EXPOSED", "eval_dev_half", lang, n)
    add("eval/kw/candidate/manifest.jsonl", "CANDIDATE_EXPOSED", "kinyarwanda_eval",
        "kinyarwanda", n)
    add("eval/english/sealed/manifest.jsonl", "SEALED", "sealed_holdout_half",
        "english", n)
    index = {"pinned_sources": sources}
    index_bytes = _canon(index)
    index_sha = _sha(index_bytes)

    # ---- REAL training producer: fixture corpora hash-chained to fixture
    # adoption digests (served through the committed fake) ----
    corpus_store, committed_digests = {}, {}
    for dataset in ("gb3", "gb8", "gb9"):
        manifests = {}
        for label in (f"l{dataset}/asr/c1",):
            key = f"curated/{label}/{dataset}/manifest.jsonl"
            rows = [{"audio_checksum_sha256": _ck(key, i), "text": "T"}
                    for i in range(4)]
            body = "".join(json.dumps(r) + "\n" for r in rows).encode()
            corpus_store[key] = body
            manifests[label] = {"sha256": _sha(body)}
        listing = json.dumps({"manifests": manifests}, sort_keys=True).encode()
        corpus_store[f"curated/_versions/{dataset}/COMPLETE.json"] = listing
        committed_digests[dataset] = _sha(listing)
        # the committed adoption record the mint's committed_training_source_digests reads
        committed[TRAINING_SOURCE_RECORDS[dataset]] = _canon(
            {"complete_raw_sha256": committed_digests[dataset]})

    docs = {d: enumerate_corpus(lambda k: corpus_store[k], d,
                                committed=committed_digests)
            for d in committed_digests}
    training_artifact = build_training_identity_index(docs, committed=committed_digests)
    training_bytes = _canon(training_artifact)
    tdig = _sha(training_bytes)

    # ---- REAL sealed producer: fixture sealed manifest for the one sealed pool
    sealed_manifests = {"eval/english/sealed/manifest.jsonl":
                        store["eval/english/sealed/manifest.jsonl"]}
    sealed_out = build_sealed_authorities(sealed_manifests, index)
    sealed_authorities = sealed_out["authorities"]
    sealed_agg = sealed_out["ledger_aggregates"]["eval/english/sealed/manifest.jsonl"]

    # ---- ledgers admitting BOTH producers' actual outputs ----
    committed["platform/decisions/RH-TRAIN.json"] = _approval(
        "RH-TRAIN", "ADMIT_TRAINING_INDEX", "artifact_sha256", tdig)
    train_ledger = _jsonl([
        {"event": "LEDGER_OPENED_PENDING", "record": "t"},
        {"event": "ADMITTED", "artifact_sha256": tdig,
         "source_records": {d: {"source_record": TRAINING_SOURCE_RECORDS[d],
                                "complete_raw_sha256": committed_digests[d]}
                            for d in committed_digests},
         "unique_count": training_artifact["unique_count"],
         "identity_aggregate_sha256": training_artifact["aggregate_sha256"],
         "exposure_index_sha256": index_sha, "producer_role": "p",
         "workflow_run_ref": "t", "commit_sha": "t",
         "approval_record": {"path": "platform/decisions/RH-TRAIN.json",
                             "sha256": _sha(committed["platform/decisions/RH-TRAIN.json"]),
                             "record_id": "RH-TRAIN"}}])
    committed[TRAINING_INDEX_LEDGER] = train_ledger

    skey = "eval/english/sealed/manifest.jsonl"
    spin = next(s for s in sources if s["key"] == skey)
    committed["platform/decisions/RH-SEAL.json"] = _approval(
        "RH-SEAL", "ADMIT_SEALED_EXCLUSION", "key", skey)
    sealed_ledger = _jsonl([
        {"event": "LEDGER_OPENED_PENDING", "record": "s"},
        {"event": "ADMITTED", "key": skey, "class": "SEALED", "language": "english",
         "rows": n, "sha256": spin["sha256"], "s3_version_id": spin["s3_version_id"],
         "disposition": "CLEARED_FOR_EXCLUSION",
         "identity_unique": sealed_agg["identity_unique"],
         "identity_aggregate_sha256": sealed_agg["identity_aggregate_sha256"],
         "exposure_index_sha256": index_sha, "producer_role": "o",
         "workflow_run_ref": "t", "commit_sha": "t",
         "approval_record": {"path": "platform/decisions/RH-SEAL.json",
                             "sha256": _sha(committed["platform/decisions/RH-SEAL.json"]),
                             "record_id": "RH-SEAL"}}])
    committed[SEALED_EXCLUSION_LEDGER] = sealed_ledger

    packet = {"aws": {"account": _ACCOUNT, "kms_key": _KMS, "bucket": "medzen-speech"},
              "minimal_read_role": {"role_name": _MINT_ROLE},
              "exposure_index_sha256": index_sha,
              "training_index_ledger_sha256": _sha(train_ledger),
              "sealed_exclusion_ledger_sha256": _sha(sealed_ledger),
              "independent_review_record": {"path": "platform/decisions/RH-REVIEW.json",
                                            "record_id": "RH-REVIEW"},
              "pinned_objects": [
                  {"key": s["key"], "class": s["class"], "role": s["role"],
                   "language": s["language"], "rows": s["rows"],
                   "sha256": s["sha256"], "s3_version_id": s["s3_version_id"]}
                  for s in sources if s["key"] in read_pool_keys(index)]}

    real = mint_mod._read_committed

    def fake(relpath, *, allowed_prefixes, repo_root=mint_mod.ROOT):
        if relpath in committed:
            return committed[relpath]
        try:
            return real(relpath, allowed_prefixes=allowed_prefixes, repo_root=repo_root)
        except mint_mod.MintRefusal:
            # trust-manifest files added THIS round are not yet at HEAD; read the
            # working tree so the rehearsal runs pre-commit (prod reads at HEAD)
            return (repo_root / relpath).read_bytes()

    monkeypatch.setattr(mint_mod, "_read_committed", fake)
    with mint_mod._trust_oid(mint_mod._resolve_head_oid()):
        tm_sha = mint_mod.build_trust_manifest()[1]
    committed["platform/decisions/RH-REVIEW.json"] = _canon(
        {"record": "RH-REVIEW", "authorizes": "APPROVE_LIVE_MINT", "status": "APPROVED",
         "owner_verbatim": "owner approves this mint", "trust_manifest_sha256": tm_sha})

    calls = []

    def reader(key, s3_version_id):
        calls.append(key)
        return {"body": store[key], "version_id": f"v-{key}", "kms_key_arn": _KMS}

    caller = {"Account": _ACCOUNT,
              "Arn": f"arn:aws:sts::{_ACCOUNT}:assumed-role/{_MINT_ROLE}/run"}

    # ---- the actual composition reaches FROZEN ----
    result = live_mint(packet, index_bytes=index_bytes, s3_reader=reader,
                       caller_identity=caller, training_index_bytes=training_bytes,
                       sealed_authorities=sealed_authorities)
    assert result["manifest"]["status"] == "FROZEN"
    assert result["manifest"]["aggregate_overlap_counts"] == {
        "candidate_exposed": 0, "sealed": 0, "veto_surface": 0, "training_exposed": 0}
    # the mint NEVER read the sealed object (sealed identities came from the producer)
    assert not [k for k in calls if "sealed" in k]
    sd = result["manifest"]["exclusion_provenance"]["sealed_disjointness"]
    assert sd["method"] == "IDENTITY_ANTI_JOIN_ALL_POOLS"
    assert result["provenance"]["trust_manifest_sha256"] == tm_sha
    assert len(result["result_sha256"] if "result_sha256" in result
               else result["payload_sha256"]) == 64


def test_caller_workflow_composes_produce_seal_mint():
    caller = (_REPO / ".github/workflows/arm2-nomination-mint.yml").read_text()
    assert "arm2-nomination-mint-producer-exec.yml" in caller
    assert "arm2-nomination-mint-sealed-exec.yml" in caller
    assert "arm2-nomination-mint-mint-exec.yml" in caller
    assert "needs: [produce, seal]" in caller
    assert "needs.seal.outputs.authorities_sha256" in caller
    mint = (_REPO / ".github/workflows/arm2-nomination-mint-mint-exec.yml").read_text()
    assert "--sealed-authorities sealed-authorities.json" in mint
    assert "attest-build-provenance" in mint
