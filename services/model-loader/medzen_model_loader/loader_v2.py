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
import os
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
    # round 4: serving needs the alias -> omnilingual language-id map;
    # when the manifest carries one it must cover exactly the languages
    language_ids = manifest.get("language_ids")
    if language_ids is not None:
        if (not isinstance(language_ids, Mapping)
                or set(language_ids) != languages
                or not all(isinstance(v, str) and v
                           for v in language_ids.values())):
            raise LoaderV2Refusal(
                "language_ids must map EVERY manifest language to its "
                "omnilingual id")
    if classification == CLASSIFICATION_PROD:
        _verify_committed_promotion(manifest, digest)
    return {"digest": digest, "version": expected_version,
            "classification": classification,
            "languages": sorted(languages),
            "language_ids": dict(language_ids) if language_ids else None}


def _verify_committed_promotion(manifest: Mapping[str, Any],
                                digest: str) -> None:
    """Codex serving review (reproduced FABRICATED_PRODUCTION_APPROVAL_
    ACCEPTED): the manifest's own fields cannot vouch for the manifest.
    Production standing binds an AUTHORITATIVE promotion record COMMITTED
    at git HEAD whose bytes hash to the declared sha, whose protocol
    matches the CURRENT-PROMOTION-PROTOCOL pointer, whose decision is
    APPROVED, and that names THIS exact artifact digest."""
    import subprocess
    approval = manifest.get("promotion_approval")
    if not isinstance(approval, Mapping):
        raise LoaderV2Refusal(
            "PRODUCTION requires a promotion_approval binding")
    rel = str(approval.get("record") or "")
    # Round 3 (Codex): "anywhere under platform/" let ANY committed JSON
    # — an evidence file, a fixture — play promotion record. Promotion
    # records live in exactly one reviewed directory.
    if (not rel.startswith("platform/decisions/promotions/")
            or ".." in rel or ":" in rel or rel.count("/") != 3
            or not rel.endswith(".json")):
        raise LoaderV2Refusal(
            "promotion records live under platform/decisions/promotions/ "
            "only — no other committed path can vouch for production")
    root = os.environ.get("MEDZEN_REPO_ROOT")
    git = (["git", "-C", root] if root else ["git"])

    def _at_head(path: str) -> bytes:
        shown = subprocess.run(git + ["show", f"HEAD:{path}"],
                               capture_output=True)
        if shown.returncode != 0:
            raise LoaderV2Refusal(
                f"{path} is not committed at HEAD — a manifest cannot "
                "self-certify production")
        return shown.stdout

    body = _at_head(rel)
    if hashlib.sha256(body).hexdigest() != approval.get("record_sha256"):
        raise LoaderV2Refusal(
            "promotion_approval.record_sha256 does not match the "
            "committed record bytes")
    record = json.loads(body)
    if record.get("decision") != "APPROVED":
        raise LoaderV2Refusal("committed promotion record is not APPROVED")
    # Round 3 (Codex): a prefix match ("PROMOTION-PROTOCOL-*") accepted
    # any invented or superseded protocol id. The record must name the
    # protocol the CURRENT-PROMOTION-PROTOCOL pointer designates at
    # HEAD, and the pointed protocol bytes must hash to the pointer's
    # recorded sha — the same supersession discipline every other
    # consumer follows (Codex review #21).
    pointer = json.loads(
        _at_head("platform/decisions/CURRENT-PROMOTION-PROTOCOL.json"))
    protocol_bytes = _at_head(str(pointer["file"]))
    if hashlib.sha256(protocol_bytes).hexdigest() != pointer.get("sha256"):
        raise LoaderV2Refusal(
            "the committed promotion protocol does not match the "
            "pointer's recorded hash — the protocol chain is broken")
    if record.get("protocol") != pointer.get("record"):
        raise LoaderV2Refusal(
            f"promotion record cites protocol "
            f"{record.get('protocol')!r}; the current pointer requires "
            f"{pointer.get('record')!r}")
    # Round 4 (Codex): a record carrying ONLY decision/protocol/digest is
    # an assertion, not a promotion. It must bind the evidence the
    # protocol demands: the committed gate report (hash-verified at
    # HEAD), the independent review identity, and the owner's
    # authorization. Semantics of those documents are the protocol's
    # job; their EXISTENCE and binding are this gate's job.
    gate = record.get("gate_report")
    if (not isinstance(gate, Mapping)
            or not str(gate.get("record") or "").startswith("platform/")
            or ".." in str(gate.get("record"))):
        raise LoaderV2Refusal(
            "promotion record must bind its committed gate report")
    gate_bytes = _at_head(str(gate["record"]))
    if hashlib.sha256(gate_bytes).hexdigest() != gate.get("record_sha256"):
        raise LoaderV2Refusal(
            "gate_report.record_sha256 does not match the committed bytes")
    for field in ("independent_review", "owner_authorization"):
        if not str(record.get(field) or "").strip():
            raise LoaderV2Refusal(
                f"promotion record must carry a non-empty {field}")
    bound = str(record.get("artifact_sha256")
                or record.get("promoted_artifact_sha256") or "")
    if bound != digest:
        raise LoaderV2Refusal(
            f"the committed promotion record promotes {bound[:12]}…, not "
            f"this artifact {digest[:12]}… — refusing")


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


ASSET_CARD = "medzen_omniASR_CTC_1B_v2"


def write_ready_marker_v2(manifest_path: Path, artifact_path: Path,
                          model_dir: Path) -> Path:
    """B6v2 round 4 (Codex): NOTHING wrote the marker the serving backend
    requires — loader_v2 only printed identity data, so the OmniASR path
    could never come up. This verifies (digest before deserialization,
    same as load_artifact_v2) and then writes .medzen-ready-v2.json
    ATOMICALLY (tmp file + os.replace) so a crashed loader can never
    leave a half-written attestation a runtime would trust."""
    manifest = json.loads(manifest_path.read_text())
    identity = load_artifact_v2(manifest, artifact_path)
    if not identity["language_ids"]:
        raise LoaderV2Refusal(
            "serving requires the manifest's language_ids map — the "
            "runtime cannot guess omnilingual language codes")
    marker = {
        "schema_version": 3,
        "artifact_verified": True,
        "classification": identity["classification"],
        "model_version": identity["version"],
        "artifact_sha256": identity["digest"],
        "manifest_sha256": hashlib.sha256(
            manifest_path.read_bytes()).hexdigest(),
        "language_ids": identity["language_ids"],
        "checkpoint_path": str(artifact_path),
        "asset_card": ASSET_CARD,
    }
    destination = model_dir / ".medzen-ready-v2.json"
    temporary = model_dir / ".medzen-ready-v2.json.tmp"
    temporary.write_text(json.dumps(marker, indent=1, sort_keys=True))
    os.replace(temporary, destination)
    return destination


if __name__ == "__main__":
    import sys
    if "--write-marker" in sys.argv:
        flag = sys.argv.index("--write-marker")
        destination = write_ready_marker_v2(
            Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[flag + 1]))
        print(json.dumps({"marker": str(destination)}))
    else:
        manifest = json.loads(Path(sys.argv[1]).read_text())
        print(json.dumps(load_artifact_v2(manifest, Path(sys.argv[2]))))
