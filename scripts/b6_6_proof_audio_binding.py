#!/usr/bin/env python3
"""Single source and fail-closed projection audit for the B6 proof audio."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
PROOF_AUDIO_PATH = ROOT / "platform/testdata/b6a-003c-b-synthetic.wav"
PROOF_AUDIO_SHA256 = "3e7b78cbf65b5b857d0bd2ea6b2568ce74c523be2b319ade8930c9ac6a7630c3"
PROOF_AUDIO_SHA256_ENV = "MEDZEN_B6_PROOF_AUDIO_SHA256"
ATTEMPT_EVIDENCE = (
    ROOT
    / "platform/evidence/"
    / "B6-PACKET-2026-030-ATTEMPT-1-REFUSED-PROBE-AUDIO-BINDING.json"
)
MANIFEST = ROOT / "platform/k8s/b6-6/integration-window.yaml"
PACKET = ROOT / "platform/decisions/B6-AWS-CHANGE-PACKET-2026-030A-proof-audio-binding.md"
PROBE = ROOT / "scripts/b6_6_probe.py"
OPERATIONS = ROOT / "scripts/b6_6_operations.sh"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PACKET_BINDING_RE = re.compile(r"Proof-audio SHA-256: `([0-9a-f]{64})`")


class ProofAudioBindingRefusal(RuntimeError):
    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


def evaluate_projection_hashes(projections: dict[str, str]) -> dict[str, Any]:
    expected_names = {"binding_module", "manifest_configmap", "packet"}
    if set(projections) != expected_names:
        raise ProofAudioBindingRefusal("PROOF_AUDIO_PROJECTION_SET_DIFFERS")
    if any(SHA256_RE.fullmatch(value) is None for value in projections.values()):
        raise ProofAudioBindingRefusal("PROOF_AUDIO_PROJECTION_MALFORMED")
    if set(projections.values()) != {PROOF_AUDIO_SHA256}:
        raise ProofAudioBindingRefusal("PROOF_AUDIO_PROJECTION_HASH_DRIFT")
    return {
        "status": "PASS_PROOF_AUDIO_SINGLE_SOURCE",
        "proof_audio_sha256": PROOF_AUDIO_SHA256,
        "projection_count": len(projections),
        "projections": dict(sorted(projections.items())),
    }


def _manifest_hash() -> str:
    documents = [item for item in yaml.safe_load_all(MANIFEST.read_text()) if item]
    matches = [
        item for item in documents
        if item.get("kind") == "ConfigMap"
        and item.get("metadata", {}).get("name") == "speech-orchestrator-config"
    ]
    if len(matches) != 1:
        raise ProofAudioBindingRefusal("PROOF_AUDIO_CONFIGMAP_MISSING")
    value = matches[0].get("data", {}).get(PROOF_AUDIO_SHA256_ENV)
    if not isinstance(value, str):
        raise ProofAudioBindingRefusal("PROOF_AUDIO_CONFIGMAP_BINDING_MISSING")
    return value


def _packet_hash() -> str:
    try:
        matches = PACKET_BINDING_RE.findall(PACKET.read_text())
    except OSError as exc:
        raise ProofAudioBindingRefusal("PROOF_AUDIO_PACKET_MISSING") from exc
    if len(matches) != 1:
        raise ProofAudioBindingRefusal("PROOF_AUDIO_PACKET_BINDING_CARDINALITY_DIFFERS")
    return matches[0]


def _superseded_hash_from_immutable_evidence() -> str:
    try:
        value = json.loads(ATTEMPT_EVIDENCE.read_bytes())
        result = value["diagnosis"]["probe_private_literal_sha256"]
    except Exception as exc:
        raise ProofAudioBindingRefusal(
            "PROOF_AUDIO_PREDECESSOR_EVIDENCE_INVALID"
        ) from exc
    if SHA256_RE.fullmatch(str(result)) is None or result == PROOF_AUDIO_SHA256:
        raise ProofAudioBindingRefusal("PROOF_AUDIO_PREDECESSOR_HASH_INVALID")
    return result


def audit() -> dict[str, Any]:
    actual_sha256 = hashlib.sha256(PROOF_AUDIO_PATH.read_bytes()).hexdigest()
    if actual_sha256 != PROOF_AUDIO_SHA256:
        raise ProofAudioBindingRefusal("PROOF_AUDIO_FILE_HASH_DIFFERS")
    probe = PROBE.read_text()
    operations = OPERATIONS.read_text()
    superseded_sha256 = _superseded_hash_from_immutable_evidence()
    if (
        PROOF_AUDIO_SHA256 in probe
        or superseded_sha256 in probe
        or "os.environ.get(PROOF_AUDIO_SHA256_ENV)" not in probe
        or "from scripts.b6_6_proof_audio_binding import PROOF_AUDIO_SHA256_ENV" not in probe
    ):
        raise ProofAudioBindingRefusal("PROBE_RETAINS_PRIVATE_AUDIO_HASH_OR_ENV_IS_UNWIRED")
    if (
        PROOF_AUDIO_SHA256 in operations
        or superseded_sha256 in operations
        or "from scripts.b6_6_proof_audio_binding import PROOF_AUDIO_SHA256" not in operations
        or f'{PROOF_AUDIO_SHA256_ENV}="$proof_audio_sha256"' not in operations
    ):
        raise ProofAudioBindingRefusal("OPERATIONS_PROOF_AUDIO_BINDING_DIFFERS")
    aligned = evaluate_projection_hashes({
        "binding_module": PROOF_AUDIO_SHA256,
        "manifest_configmap": _manifest_hash(),
        "packet": _packet_hash(),
    })
    return {
        **aligned,
        "proof_audio_path": str(PROOF_AUDIO_PATH.relative_to(ROOT)),
        "probe_expected_hash_source": "ENV_ONLY_NO_PRIVATE_LITERAL",
        "operations_expected_hash_source": "BINDING_MODULE_TO_ENV",
        "superseded_tone_hash_in_probe": False,
        "real_aws_calls": 0,
        "real_kubectl_calls": 0,
        "mutations": 0,
    }


def rehearsal() -> dict[str, Any]:
    passing = audit()
    refusals: list[dict[str, str]] = []
    for changed in ("binding_module", "manifest_configmap", "packet"):
        projections = {
            "binding_module": PROOF_AUDIO_SHA256,
            "manifest_configmap": PROOF_AUDIO_SHA256,
            "packet": PROOF_AUDIO_SHA256,
        }
        projections[changed] = "0" * 64
        try:
            evaluate_projection_hashes(projections)
        except ProofAudioBindingRefusal as exc:
            refusals.append({
                "changed_projection": changed,
                "outcome": "REFUSED",
                "reason_code": exc.reason_code,
            })
        else:
            raise AssertionError(f"{changed} proof-audio drift did not refuse")
    return {
        "status": "PASS",
        "aligned_pass": passing,
        "drift_injections": refusals,
        "injected_failures": len(refusals),
        "real_aws_calls": 0,
        "real_kubectl_calls": 0,
        "mutations": 0,
    }


if __name__ == "__main__":
    try:
        print(json.dumps(rehearsal(), sort_keys=True, separators=(",", ":")))
    except ProofAudioBindingRefusal as exc:
        print(json.dumps({"status": "REFUSED", "reason_code": exc.reason_code}))
        raise SystemExit(2)
