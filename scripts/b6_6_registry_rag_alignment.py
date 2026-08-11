#!/usr/bin/env python3
"""Fail closed unless the B6 proof input, registry and deployed RAG identity align."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
IDENTITY_EVIDENCE = ROOT / "platform/evidence/B6-RAG-IMAGE-INDEX-IDENTITY-2026-001.json"
REGISTRY_SOURCE = ROOT / "registry/deployment/b6-v0-synthetic.json"
RAG_ROOT = ROOT / "platform/testdata/rag-index"
WINDOW_MANIFEST = ROOT / "platform/k8s/b6-6/integration-window.yaml"
B6A_TRANSCRIPTION = ROOT / "platform/evidence/receipts/B6A-2026-003C-F-LIVE/transcription.json"
PROOF_AUDIO = ROOT / "platform/testdata/b6a-003c-b-synthetic.wav"
PROOF_AUDIO_SHA256 = "3e7b78cbf65b5b857d0bd2ea6b2568ce74c523be2b319ade8930c9ac6a7630c3"
PROOF_TRANSCRIPT = "This is a synthetic MedZen platform test. No patient data is present."
PROOF_TRANSCRIPT_SHA256 = "4c0a11f2c67286a5de444f776a927da784fde10f80fd8f9140c4e907285c9d19"
RAG_INDEX_SHA256 = "6dc2a9217b44a8cd9523ee051f19a7e20d1cab447ad0029a42796c5211797160"
REGISTRY_ROOT = "/medzen/registry/test/b6/d4f9696d288e0ea6c1d139f496e00eaf097b77ea8b3a4f5a26a6470286adfe81"


class AlignmentRefusal(RuntimeError):
    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _config_map() -> dict[str, str]:
    documents = [item for item in yaml.safe_load_all(WINDOW_MANIFEST.read_text()) if item]
    matches = [
        item for item in documents
        if item.get("kind") == "ConfigMap"
        and item.get("metadata", {}).get("name") == "speech-orchestrator-config"
    ]
    if len(matches) != 1 or not isinstance(matches[0].get("data"), dict):
        raise AlignmentRefusal("WINDOW_ALIGNMENT_CONFIGMAP_MISSING")
    return matches[0]["data"]


def evaluate_contract(*, expected_index_sha256: str, observed_index_sha256: str,
                      citation_count: int) -> dict[str, Any]:
    if observed_index_sha256 != expected_index_sha256:
        raise AlignmentRefusal("RAG_INDEX_IDENTITY_MISMATCH")
    if citation_count != 3:
        raise AlignmentRefusal("RAG_PROOF_CITATION_COUNT_MISMATCH")
    return {
        "status": "PASS_ALIGNED_RAG_PROOF_PATH",
        "rag_index_sha256": observed_index_sha256,
        "citation_count": citation_count,
    }


def audit(root: Path = ROOT) -> dict[str, Any]:
    del root  # all paths are immutable module bindings rooted at this repository
    identity = json.loads(IDENTITY_EVIDENCE.read_bytes())
    registry = json.loads(REGISTRY_SOURCE.read_bytes())
    transcription = json.loads(B6A_TRANSCRIPTION.read_bytes())
    route = registry.get("routes", {}).get("english", {})
    rag = route.get("rag", {})
    alias_path = RAG_ROOT / "aliases/current.json"
    alias = json.loads(alias_path.read_bytes())
    manifest_path = (RAG_ROOT / alias["manifest_path"]).resolve()
    try:
        manifest_path.relative_to(RAG_ROOT.resolve())
    except ValueError as exc:
        raise AlignmentRefusal("RAG_ALIAS_ESCAPES_ROOT") from exc
    manifest_sha256 = _sha256(manifest_path)
    config = _config_map()
    transcript_sha256 = hashlib.sha256(PROOF_TRANSCRIPT.encode()).hexdigest()
    if identity.get("status") != "VERIFIED_ALIGNED_IDENTITY":
        raise AlignmentRefusal("RAG_IMAGE_IDENTITY_EVIDENCE_INVALID")
    if identity.get("image", {}).get("child_manifest_digest") != (
        "sha256:fe4663812f88bd35d520fee3e80450981347c970f2a561eb8163b14183b7194c"
    ):
        raise AlignmentRefusal("RAG_IMAGE_DIGEST_DIFFERS")
    if (
        identity.get("embedded_index", {}).get("manifest_file_sha256")
        != RAG_INDEX_SHA256
        or identity.get("comparison", {}).get("exact_match") is not True
        or identity.get("comparison", {}).get("alias_match") is not True
    ):
        raise AlignmentRefusal("RAG_EXTRACTED_IDENTITY_DIFFERS")
    if (
        alias.get("alias") != "current"
        or alias.get("manifest_sha256") != RAG_INDEX_SHA256
        or manifest_sha256 != RAG_INDEX_SHA256
        or rag != {
            "alias": "current",
            "snapshot_sha256": RAG_INDEX_SHA256,
            "query_language": "en",
        }
    ):
        raise AlignmentRefusal("REGISTRY_RAG_IDENTITY_DIFFERS")
    if (
        _sha256(PROOF_AUDIO) != PROOF_AUDIO_SHA256
        or transcript_sha256 != PROOF_TRANSCRIPT_SHA256
        or transcription.get("status") != "PASS"
        or transcription.get("payload", {}).get("audio_sha256") != PROOF_AUDIO_SHA256
        or transcription.get("payload", {}).get("transcript_normalized_sha256")
        != PROOF_TRANSCRIPT_SHA256
    ):
        raise AlignmentRefusal("PROVEN_SYNTHETIC_PROOF_INPUT_DIFFERS")
    if config != {
        "AWS_REGION": "eu-central-1",
        "MEDZEN_ORCHESTRATOR_MODE": "deployed_http_ssm",
        "MEDZEN_CLIENT_KEYS_SECRET_ID": "medzen/client-api-keys",
        "MEDZEN_REGISTRY_ROOT": REGISTRY_ROOT,
        "MEDZEN_B6_PROOF_AUDIO_SHA256": PROOF_AUDIO_SHA256,
        "MEDZEN_B6_RAG_INDEX_SHA256": RAG_INDEX_SHA256,
    }:
        raise AlignmentRefusal("WINDOW_ALIGNMENT_CONFIGMAP_DIFFERS")

    import sys
    service_root = ROOT / "services/rag-index"
    if str(service_root) not in sys.path:
        sys.path.insert(0, str(service_root))
    from medzen_rag_index.index import IndexRepository

    citations = IndexRepository(RAG_ROOT).search(
        PROOF_TRANSCRIPT, language="en", top_k=3
    )
    aligned = evaluate_contract(
        expected_index_sha256=rag["snapshot_sha256"],
        observed_index_sha256=manifest_sha256,
        citation_count=len(citations),
    )
    return {
        **aligned,
        "registry_root": REGISTRY_ROOT,
        "proof_audio_sha256": PROOF_AUDIO_SHA256,
        "proof_transcript_sha256": PROOF_TRANSCRIPT_SHA256,
        "citation_document_ids": [item["document_id"] for item in citations],
        "image_identity_evidence_sha256": _sha256(IDENTITY_EVIDENCE),
        "prior_live_transcription_receipt_sha256": _sha256(B6A_TRANSCRIPTION),
        "aws_calls": 0,
        "kubernetes_calls": 0,
        "mutations": 0,
    }


def rehearsal() -> dict[str, Any]:
    passing = audit()
    try:
        evaluate_contract(
            expected_index_sha256=RAG_INDEX_SHA256,
            observed_index_sha256="0" * 64,
            citation_count=3,
        )
    except AlignmentRefusal as exc:
        mismatch_reason = exc.reason_code
    else:
        raise AssertionError("injected RAG identity mismatch did not refuse")
    return {
        "status": "PASS",
        "aligned_pass": passing,
        "mismatch_injection": {
            "outcome": "REFUSED",
            "reason_code": mismatch_reason,
        },
        "real_aws_calls": 0,
        "real_kubectl_calls": 0,
        "mutations": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("audit", "rehearsal"))
    args = parser.parse_args()
    try:
        result = audit() if args.mode == "audit" else rehearsal()
    except (AlignmentRefusal, OSError, ValueError, json.JSONDecodeError) as exc:
        reason = exc.reason_code if isinstance(exc, AlignmentRefusal) else type(exc).__name__
        print(json.dumps({"status": "REFUSED", "reason_code": reason}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
