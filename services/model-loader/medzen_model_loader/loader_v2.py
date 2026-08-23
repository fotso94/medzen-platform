"""B6v2 model loader — the LONG-TERM multilingual ASR runtime contract.

The v0/v1 loader (loader.py, untouched) is the closed synthetic proof: it
accepts EXACTLY zero-shot Whisper large-v3 in CTranslate2 form and is
structurally unable to load what B5 actually produces. This module
defines the runtime the platform runs on going forward, per
ARCH-2026-001: ONE multilingual OmniASR artifact, fairseq2 checkpoint
format, one digest across every language.

Round 5 (Codex serving review): this is now a COMPLETE init path, not a
validator with no caller — run_b6v2_init downloads the version-pinned
manifest, checkpoint AND tokenizer from S3, digest-verifies each, stages
them at the EXACT asset-card destinations fairseq2 later deserializes,
re-verifies the staged bytes, and writes the ready marker LAST
(atomically). Production standing verifies an immutable PROMOTION BUNDLE
pinned by the reviewed deployment — no git in the runtime image.

DELIBERATE NON-BINDING: this loader validates manifests GENERICALLY. It
carries no artifact digest and no job name. Arm 1's artifact may only be
bound AFTER its evaluation passes the promotion protocol.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from .languages_v2 import canonical_language_ids, marker_language_ids

SCHEMA_VERSION = 2
MODEL_FAMILY = "omniasr_ctc_1b"
ARTIFACT_FORMAT = "fairseq2_pt"
CLASSIFICATION_NONPROD = "NONPROD_REAL_PROVIDER_V2"
CLASSIFICATION_PROD = "PRODUCTION"
MANDATORY_LANGUAGES = frozenset({
    "english", "ewe", "french", "kinyarwanda", "lingala", "pidgin",
    "swahili"})
ASSET_CARD = "medzen_omniASR_CTC_1B_v2"
# the EXACT destinations the fairseq2 asset card deserializes
# (services/asr-eval-runtime/assets/models.yaml — a test pins agreement)
CHECKPOINT_FILENAME = "omniASR-CTC-1B-v2.pt"
TOKENIZER_FILENAME = "omniASR_tokenizer_written_v2.model"
SHA_HEX = frozenset("0123456789abcdef")


class LoaderV2Refusal(RuntimeError):
    pass


def _sha256_ok(value: Any) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(c in SHA_HEX for c in value.lower()))


def artifact_tree_sha256(checkpoint_sha256: str, tokenizer_sha256: str) -> str:
    """B6v2 round 6 (Codex): ONE digest names the WHOLE served artifact —
    checkpoint AND tokenizer. The model_version derives from THIS, the
    registry binds THIS, and the runtime reports a version derived from
    THIS — so the registry can never approve a different artifact than
    the runtime serves."""
    material = json.dumps(
        {"checkpoint_sha256": checkpoint_sha256,
         "tokenizer_sha256": tokenizer_sha256},
        sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(material).hexdigest()


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
    if not _sha256_ok(digest):
        raise LoaderV2Refusal("artifact sha256 must be 64 hex chars")
    languages = set(manifest.get("languages") or [])
    if not MANDATORY_LANGUAGES <= languages:
        raise LoaderV2Refusal(
            "one artifact serves EVERY mandatory language "
            f"(missing: {sorted(MANDATORY_LANGUAGES - languages)})")
    # Round 6 (Codex): the tokenizer is a REQUIRED part of the served
    # artifact — a v2 manifest without it names half an artifact.
    tokenizer = manifest.get("tokenizer")
    if not isinstance(tokenizer, Mapping) or not _sha256_ok(
        tokenizer.get("sha256")
    ):
        raise LoaderV2Refusal(
            "tokenizer binding must carry a sha256 of the exact "
            "tokenizer bytes — the asset card deserializes both")
    tokenizer_sha256 = str(tokenizer["sha256"])
    # Round 6 (Codex): ONE tree digest over checkpoint+tokenizer is the
    # identity everything else derives from. The manifest must DECLARE
    # it (so reviews see it) and it must recompute exactly.
    tree = artifact_tree_sha256(digest, tokenizer_sha256)
    if manifest.get("artifact_tree_sha256") != tree:
        raise LoaderV2Refusal(
            "artifact_tree_sha256 must equal the tree digest over "
            "checkpoint+tokenizer — the declared identity must recompute")
    version = manifest.get("model_version")
    expected_version = f"{MODEL_FAMILY}:{tree[:12]}"
    if version != expected_version:
        raise LoaderV2Refusal(
            f"model_version must be {expected_version!r} — the identity "
            "IS the tree digest; free-text versions drift")
    # Round 5 (Codex): three different language keys with no translation
    # broke routing before inference. A serving manifest must declare
    # EXACTLY the canonical alias -> omnilingual map — no drift, no
    # free-form ids.
    language_ids = manifest.get("language_ids")
    if language_ids is not None and dict(language_ids) != canonical_language_ids():
        raise LoaderV2Refusal(
            "language_ids must equal the canonical serving map "
            "(medzen_model_loader.languages_v2.SERVING_LANGUAGES_V1)")
    if classification == CLASSIFICATION_PROD:
        _verify_promotion_bundle(tree)
    return {"digest": digest, "version": expected_version,
            "classification": classification,
            "languages": sorted(languages),
            "language_ids": (dict(language_ids) if language_ids else None),
            "tokenizer_sha256": tokenizer_sha256,
            "artifact_tree_sha256": tree}


def _verify_promotion_bundle(digest: str) -> None:
    """Round 5 (Codex): the git-based gate could not run in the runtime
    image (no git, no history) and accepted assertion strings. Production
    standing now verifies an IMMUTABLE PROMOTION BUNDLE shipped with the
    deployment: the reviewed deployment pins the bundle index's sha256 in
    the environment; the index pins every document; the documents must
    SEMANTICALLY satisfy the promotion protocol — every active gate PASS
    for every mandatory language, atomically, plus a PASS independent
    review and an owner authorization bound to THIS artifact digest.
    The loader enforces structure and binding; the human meaning of the
    documents is what Codex and the owner reviewed when the deployment
    pin was created."""
    bundle_dir = os.environ.get("MEDZEN_PROMOTION_BUNDLE_DIR")
    pinned = os.environ.get("MEDZEN_PROMOTION_BUNDLE_SHA256", "")
    if not bundle_dir or not _sha256_ok(pinned):
        raise LoaderV2Refusal(
            "PRODUCTION requires the reviewed promotion bundle "
            "(MEDZEN_PROMOTION_BUNDLE_DIR + MEDZEN_PROMOTION_BUNDLE_SHA256)")
    root = Path(bundle_dir)
    index_path = root / "bundle.json"
    try:
        index_bytes = index_path.read_bytes()
    except OSError as exc:
        raise LoaderV2Refusal("promotion bundle index is unreadable") from exc
    if hashlib.sha256(index_bytes).hexdigest() != pinned.lower():
        raise LoaderV2Refusal(
            "promotion bundle index does not match the deployment pin")
    index = json.loads(index_bytes)
    files = index.get("files")
    if not isinstance(files, Mapping) or not files:
        raise LoaderV2Refusal("promotion bundle lists no files")

    loaded: dict[str, Any] = {}
    for name, expected in files.items():
        if "/" in str(name) or ".." in str(name) or not _sha256_ok(expected):
            raise LoaderV2Refusal("promotion bundle entry is unsafe")
        body = (root / str(name)).read_bytes()
        if hashlib.sha256(body).hexdigest() != str(expected):
            raise LoaderV2Refusal(
                f"promotion bundle file {name} does not match its pin")
        # .json documents parse; .jsonl row evidence stays raw bytes for
        # the recomputation gate
        if str(name).endswith(".json"):
            loaded[str(name)] = json.loads(body)

    def _bundled(reference: Any, label: str) -> Any:
        if (not isinstance(reference, Mapping)
                or reference.get("record") not in loaded
                or files[reference["record"]] != reference.get("record_sha256")):
            raise LoaderV2Refusal(
                f"promotion record must bind its bundled {label}")
        return loaded[reference["record"]]

    if "CURRENT-PROMOTION-PROTOCOL.json" not in loaded:
        raise LoaderV2Refusal("promotion bundle omits the protocol pointer")
    pointer = loaded["CURRENT-PROMOTION-PROTOCOL.json"]
    protocol_name = str(pointer.get("file", "")).rsplit("/", 1)[-1]
    if protocol_name not in loaded:
        raise LoaderV2Refusal("promotion bundle omits the pointed protocol")
    protocol_bytes = (root / protocol_name).read_bytes()
    if hashlib.sha256(protocol_bytes).hexdigest() != pointer.get("sha256"):
        raise LoaderV2Refusal(
            "bundled protocol does not match the pointer's recorded hash")
    protocol = loaded[protocol_name]

    record_name = index.get("record")
    if record_name not in loaded:
        raise LoaderV2Refusal("promotion bundle names no record")
    record = loaded[record_name]
    if record.get("decision") != "APPROVED":
        raise LoaderV2Refusal("bundled promotion record is not APPROVED")
    if record.get("protocol") != pointer.get("record"):
        raise LoaderV2Refusal(
            f"promotion record cites protocol {record.get('protocol')!r}; "
            f"the current pointer requires {pointer.get('record')!r}")
    if str(record.get("artifact_sha256") or "") != digest:
        raise LoaderV2Refusal(
            "the bundled promotion record promotes a different artifact")

    # Round 6 (Codex, FABRICATED_DETAILED_PASS_BUNDLE_ACCEPTED): the
    # bundle now consumes the AUTHORITATIVE gate-report format and runs
    # the SAME shared semantics + statistical RECOMPUTATION as
    # scripts/b7_model_promotion_check.py — evidence is per-row data
    # that must reproduce the claimed clustered-bootstrap verdicts, not
    # PASS labels. Holdout authority is the bundled, index-pinned
    # HOLDOUT-BINDINGS document (the deployment pin is the trust anchor).
    from .promotion_check import (
        PromotionCheckRefusal,
        promotable_languages,
        recompute_code_switch,
        recompute_statistics,
        require_candidate_packet,
        require_holdout_grades,
        require_operational_receipt,
        require_protocol_evidence,
        require_sealed_row_identity,
        validate_report_structure,
    )

    report = _bundled(record.get("gate_report"), "gate report")
    if "HOLDOUT-BINDINGS.json" not in loaded:
        raise LoaderV2Refusal(
            "promotion bundle omits the sealed-holdout bindings")
    # round 7: bindings carry GRADES too — {language: [{sha256, grade}]}
    holdouts: dict[str, set[str]] = {}
    grades: dict[str, str] = {}
    for language, entries in loaded["HOLDOUT-BINDINGS.json"].items():
        for binding in entries:
            holdouts.setdefault(language, set()).add(str(binding["sha256"]))
            grades[str(binding["sha256"])] = str(binding.get("grade", ""))
    if str(report.get("candidate_digest") or "") != f"sha256:{digest}":
        raise LoaderV2Refusal(
            "gate report candidate_digest does not name this artifact tree")
    # round 7 (Codex): the PREDECLARED candidate packet is mandatory —
    # thresholds, alpha, method, seed and iterations are fixed before
    # any sealed observation, never selected after the results
    packet = _bundled(record.get("candidate_packet"), "candidate packet")
    mandatory = list(protocol.get("mandatory_languages", []))
    if not mandatory:
        raise LoaderV2Refusal("bundled protocol declares no mandatory set")

    def rows_bytes(language: str) -> bytes | None:
        name = f"{language}.rows.jsonl"
        if name not in files:
            return None
        return (root / name).read_bytes()

    def manifest_bytes(language: str) -> bytes | None:
        name = f"{language}.holdout-manifest.json"
        if name not in files:
            return None
        return (root / name).read_bytes()

    try:
        validate_report_structure(report)
        promotable_languages(report, mandatory)
        require_candidate_packet(report, candidate_packet=packet)
        require_protocol_evidence(
            report, mandatory, protocol=protocol,
            holdouts_by_language=holdouts)
        require_holdout_grades(
            report, grades_by_holdout=grades, mandatory=mandatory)
        recompute_statistics(
            report, mandatory, mandatory=mandatory, rows_bytes=rows_bytes)
        require_sealed_row_identity(
            report, mandatory, rows_bytes=rows_bytes,
            manifest_bytes=manifest_bytes)
        recompute_code_switch(report, rows_bytes=rows_bytes)
        require_operational_receipt(
            report, candidate_packet=packet, artifact_tree_sha256=digest)
    except PromotionCheckRefusal as exc:
        raise LoaderV2Refusal(f"promotion gate refused: {exc}") from exc

    review = _bundled(record.get("independent_review"), "independent review")
    if (review.get("status") != "PASS"
            or review.get("findings") != 0
            or not str(review.get("reviewer") or "").strip()):
        raise LoaderV2Refusal(
            "bundled independent review is not a zero-findings PASS")

    authorization = record.get("owner_authorization")
    if (not isinstance(authorization, Mapping)
            or not str(authorization.get("statement") or "").strip()
            or digest[:12] not in str(authorization.get("statement"))):
        raise LoaderV2Refusal(
            "owner authorization must be a statement bound to THIS "
            "artifact digest")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_artifact_v2(manifest: Mapping[str, Any],
                     artifact_path: Path) -> dict[str, Any]:
    """Digest-verify the downloaded bytes BEFORE anything deserializes
    them; a mismatched artifact never reaches torch.load."""
    identity = validate_manifest_v2(manifest)
    actual = _sha256_file(artifact_path)
    if actual != identity["digest"]:
        raise LoaderV2Refusal(
            f"artifact bytes {actual[:12]}… do not match the manifest "
            f"digest {identity['digest'][:12]}…")
    return identity


def write_ready_marker_v2(manifest: Mapping[str, Any], *,
                          manifest_sha256: str,
                          checkpoint_path: Path,
                          tokenizer_path: Path,
                          model_dir: Path) -> Path:
    """Verify EVERYTHING the runtime will trust, then attest ATOMICALLY.

    Round 5 (Codex): the marker now binds the tokenizer too, carries the
    canonical alias+wire-code language table, and is only ever written
    AFTER the staged bytes at the final destinations re-verify — a
    crashed or lying loader can never leave a marker a runtime would
    trust. tmp file + os.replace keeps the write atomic."""
    identity = load_artifact_v2(manifest, checkpoint_path)
    if identity["language_ids"] is None:
        raise LoaderV2Refusal(
            "serving requires the manifest's canonical language_ids map")
    if identity["tokenizer_sha256"] is None:
        raise LoaderV2Refusal(
            "serving requires the manifest's tokenizer binding — the "
            "asset card deserializes tokenizer bytes too")
    actual_tokenizer = _sha256_file(tokenizer_path)
    if actual_tokenizer != identity["tokenizer_sha256"]:
        raise LoaderV2Refusal(
            "staged tokenizer bytes do not match the manifest binding")
    marker = {
        "schema_version": 3,
        "artifact_verified": True,
        "classification": identity["classification"],
        "model_version": identity["version"],
        "artifact_sha256": identity["digest"],
        "tokenizer_sha256": identity["tokenizer_sha256"],
        # round 6: the ONE identity the registry binds and the version
        # derives from — checkpoint AND tokenizer under a single digest
        "artifact_tree_sha256": identity["artifact_tree_sha256"],
        "manifest_sha256": manifest_sha256,
        "language_ids": marker_language_ids(),
        "checkpoint_path": str(checkpoint_path),
        "tokenizer_path": str(tokenizer_path),
        "asset_card": ASSET_CARD,
    }
    destination = model_dir / ".medzen-ready-v2.json"
    temporary = model_dir / ".medzen-ready-v2.json.tmp"
    temporary.write_text(json.dumps(marker, indent=1, sort_keys=True))
    os.replace(temporary, destination)
    return destination


def _stream_to_verified(s3_client: Any, bucket: str, key: str,
                        destination: Path, expected_sha256: str) -> None:
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    h = hashlib.sha256()
    body = s3_client.get_object(Bucket=bucket, Key=key)["Body"]
    with open(temporary, "wb") as f:
        for chunk in iter(lambda: body.read(1 << 20), b""):
            h.update(chunk)
            f.write(chunk)
    if h.hexdigest() != expected_sha256:
        temporary.unlink(missing_ok=True)
        raise LoaderV2Refusal(
            f"downloaded {key} does not match its manifest digest")
    os.replace(temporary, destination)


def run_b6v2_init(s3_client: Any | None = None) -> dict[str, Any]:
    """The container init path (Codex round 5: nothing invoked the v2
    verifier). Download the version-PINNED manifest, then checkpoint and
    tokenizer, verify every digest, stage at the exact asset-card
    destinations, re-verify the staged bytes, marker LAST."""
    manifest_uri = os.environ["MEDZEN_B6V2_MANIFEST_URI"]
    pinned = os.environ["MEDZEN_B6V2_MANIFEST_SHA256"]
    if not _sha256_ok(pinned):
        raise LoaderV2Refusal(
            "MEDZEN_B6V2_MANIFEST_SHA256 must pin the manifest bytes")
    destination = Path(os.environ.get("MODEL_DESTINATION", "/models"))
    if not manifest_uri.startswith("s3://"):
        raise LoaderV2Refusal("manifest URI must be an exact s3:// URI")
    without_scheme = manifest_uri[len("s3://"):]
    bucket, _, key = without_scheme.partition("/")
    if not bucket or not key:
        raise LoaderV2Refusal("manifest URI must be an exact s3:// URI")
    if s3_client is None:
        import boto3
        s3_client = boto3.client(
            "s3", region_name=os.environ.get("AWS_REGION", "eu-central-1"))
    raw = s3_client.get_object(Bucket=bucket, Key=key)["Body"].read()
    if hashlib.sha256(raw).hexdigest() != pinned.lower():
        raise LoaderV2Refusal(
            "downloaded manifest does not match the deployment pin")
    manifest = json.loads(raw)
    identity = validate_manifest_v2(manifest)
    if identity["language_ids"] is None:
        raise LoaderV2Refusal(
            "a serving manifest must bind the canonical language_ids map")
    # Round 6 (Codex): the fairseq2 asset card reads the LITERAL /models
    # paths — a loader that staged anywhere else attested files the
    # runtime would never read. The override exists ONLY for tests.
    if (str(destination) != "/models"
            and os.environ.get("MEDZEN_B6V2_DESTINATION_OVERRIDE") != "1"):
        raise LoaderV2Refusal(
            "the asset card deserializes /models — staging elsewhere "
            "attests files the runtime never reads")
    prefix = key.rsplit("/", 1)[0]
    artifact_key = f"{prefix}/{manifest['artifact'].get('s3_filename', 'model.pt')}"
    tokenizer_key = (
        f"{prefix}/{manifest['tokenizer'].get('s3_filename', 'tokenizer.model')}")
    checkpoint_path = destination / CHECKPOINT_FILENAME
    tokenizer_path = destination / TOKENIZER_FILENAME
    # Round 6 (Codex): TWO-PHASE, MARKER-COMMITTED FAIL-CLOSED publish —
    # both files download and verify in a staging directory first, then
    # move to their final paths, then the marker commits the set. The
    # two os.replace calls are not literally one atomic operation
    # (round 7): a crash between them can leave a partial file set, but
    # nothing serves without the marker, which is written LAST and only
    # after BOTH staged files verified — the marker IS the commit point.
    staging = destination / ".medzen-staging"
    staging.mkdir(exist_ok=True)
    staged_checkpoint = staging / CHECKPOINT_FILENAME
    staged_tokenizer = staging / TOKENIZER_FILENAME
    try:
        _stream_to_verified(
            s3_client, bucket, artifact_key, staged_checkpoint,
            identity["digest"])
        _stream_to_verified(
            s3_client, bucket, tokenizer_key, staged_tokenizer,
            identity["tokenizer_sha256"])
        os.replace(staged_checkpoint, checkpoint_path)
        os.replace(staged_tokenizer, tokenizer_path)
    finally:
        staged_checkpoint.unlink(missing_ok=True)
        staged_tokenizer.unlink(missing_ok=True)
        try:
            staging.rmdir()
        except OSError:
            pass
    marker = write_ready_marker_v2(
        manifest,
        manifest_sha256=pinned.lower(),
        checkpoint_path=checkpoint_path,
        tokenizer_path=tokenizer_path,
        model_dir=destination,
    )
    return {"marker": str(marker), "model_version": identity["version"],
            "classification": identity["classification"]}


if __name__ == "__main__":
    import sys
    manifest = json.loads(Path(sys.argv[1]).read_text())
    print(json.dumps(load_artifact_v2(manifest, Path(sys.argv[2]))))
