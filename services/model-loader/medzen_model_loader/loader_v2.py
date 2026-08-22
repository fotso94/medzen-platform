"""B6v2 model loader — the LONG-TERM multilingual ASR runtime contract.

The v0/v1 loader (loader.py, untouched) is the closed synthetic proof: it
accepts EXACTLY zero-shot Whisper large-v3 in CTranslate2 form and is
structurally unable to load what B5 actually produces. This module
defines the runtime the platform runs on going forward, per
ARCH-2026-001: ONE multilingual OmniASR artifact, fairseq2 checkpoint
format, one digest across every language.

DELIBERATE NON-BINDING (Codex serving review): this loader validates
manifests GENERICALLY. It carries no artifact digest, no job name, and
refuses any manifest that claims production standing without a
promotion-gate approval record. Arm 1's artifact may only be bound
AFTER its evaluation passes the promotion protocol.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = 2
MODEL_FAMILY = "omniasr_ctc_1b"
ARTIFACT_FORMAT = "fairseq2_pt"
CLASSIFICATION_NONPROD = "NONPROD_REAL_PROVIDER_V2"
CLASSIFICATION_PROD = "PRODUCTION"
MANDATORY_LANGUAGES = frozenset({
    "english", "ewe", "french", "kinyarwanda", "lingala", "pidgin",
    "swahili"})


class LoaderV2Refusal(RuntimeError):
    pass


def validate_manifest_v2(manifest: Mapping[str, Any]) -> dict[str, Any]:
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise LoaderV2Refusal("v2 loader requires schema_version 2")
    classification = manifest.get("classification")
    if classification not in (CLASSIFICATION_NONPROD, CLASSIFICATION_PROD):
        raise LoaderV2Refusal(
            f"unknown classification {classification!r}")
    if manifest.get("model_family") != MODEL_FAMILY:
        raise LoaderV2Refusal(
            "v2 serves ONE multilingual OmniASR family (ARCH-2026-001); "
            f"got {manifest.get('model_family')!r}")
    artifact = manifest.get("artifact")
    if not isinstance(artifact, Mapping):
        raise LoaderV2Refusal("artifact binding is missing")
    if artifact.get("format") != ARTIFACT_FORMAT:
        raise LoaderV2Refusal(
            f"artifact format must be {ARTIFACT_FORMAT} (the B5 trainer's "
            "merged full-mode export) — CTranslate2/whisper artifacts "
            "belong to the closed v0 proof, not this runtime")
    digest = str(artifact.get("sha256") or "")
    if len(digest) != 64 or not all(c in "0123456789abcdef"
                                     for c in digest.lower()):
        raise LoaderV2Refusal("artifact sha256 must be 64 hex chars")
    languages = set(manifest.get("languages") or [])
    if not MANDATORY_LANGUAGES <= languages:
        raise LoaderV2Refusal(
            "one artifact serves EVERY mandatory language "
            f"(missing: {sorted(MANDATORY_LANGUAGES - languages)})")
    version = manifest.get("model_version")
    expected_version = f"{MODEL_FAMILY}:{digest[:12]}"
    if version != expected_version:
        raise LoaderV2Refusal(
            f"model_version must be {expected_version!r} — the identity "
            "IS the digest; free-text versions drift")
    if classification == CLASSIFICATION_PROD:
        approval = manifest.get("promotion_approval")
        if not (isinstance(approval, Mapping)
                and str(approval.get("protocol", "")).startswith(
                    "PROMOTION-PROTOCOL-")
                and approval.get("decision") == "APPROVED"
                and len(str(approval.get("gate_report_sha256", ""))) == 64):
            raise LoaderV2Refusal(
                "PRODUCTION classification requires a promotion-gate "
                "approval record (protocol id + APPROVED + gate report "
                "sha) — no artifact ships on training success alone")
    return {"digest": digest, "version": expected_version,
            "classification": classification,
            "languages": sorted(languages)}


def load_artifact_v2(manifest: Mapping[str, Any],
                     artifact_path: Path) -> dict[str, Any]:
    """Digest-verify the downloaded bytes BEFORE anything deserializes
    them; a mismatched artifact never reaches torch.load."""
    identity = validate_manifest_v2(manifest)
    h = hashlib.sha256()
    with open(artifact_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    actual = h.hexdigest()
    if actual != identity["digest"]:
        raise LoaderV2Refusal(
            f"artifact bytes {actual[:12]}… do not match the manifest "
            f"digest {identity['digest'][:12]}…")
    return identity


if __name__ == "__main__":
    import sys
    manifest = json.loads(Path(sys.argv[1]).read_text())
    print(json.dumps(load_artifact_v2(manifest, Path(sys.argv[2]))))
