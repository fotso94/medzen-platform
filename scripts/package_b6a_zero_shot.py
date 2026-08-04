#!/usr/bin/env python3
"""Convert and content-address the exact B6A zero-shot Whisper revision."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any


BASE_REVISION = "06f233fe06e710322aca913c1bc4249a0d71fce1"
REQUIRED_SOURCE_FILES = {
    "config.json", "model.safetensors", "preprocessor_config.json",
    "tokenizer.json",
}
REQUIRED_ARTIFACT_FILES = {
    "config.json", "model.bin", "preprocessor_config.json", "tokenizer.json",
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
IMAGE_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
GIT_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


class PackagingRefusal(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def bind_tree(root: Path) -> tuple[dict[str, dict[str, Any]], str, int]:
    files: dict[str, dict[str, Any]] = {}
    total = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = PurePosixPath(path.relative_to(root).as_posix())
        if rel.is_absolute() or ".." in rel.parts:
            raise PackagingRefusal(f"unsafe path in tree: {rel}")
        digest, size = sha256_file(path)
        files[rel.as_posix()] = {"sha256": digest, "bytes": size}
        total += size
    if not files:
        raise PackagingRefusal(f"tree is empty: {root}")
    tree = hashlib.sha256(canonical(files)).hexdigest()
    return files, tree, total


def _utc(value: str) -> str:
    if not value.endswith("Z"):
        raise PackagingRefusal("converted-at-utc must end in Z")
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise PackagingRefusal("converted-at-utc is malformed") from exc
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise PackagingRefusal("converted-at-utc must be UTC")
    return value


def package(source: Path, output_root: Path, *, git_commit: str,
            converter_image_digest: str, converted_at_utc: str,
            runner=subprocess.run) -> dict[str, Any]:
    source = source.resolve(strict=True)
    output_root = output_root.resolve()
    if not GIT_COMMIT_RE.fullmatch(git_commit):
        raise PackagingRefusal("exact 40-character Git commit is required")
    if not IMAGE_DIGEST_RE.fullmatch(converter_image_digest):
        raise PackagingRefusal("exact converter image digest is required")
    converted_at_utc = _utc(converted_at_utc)
    source_names = {p.name for p in source.iterdir() if p.is_file()}
    missing = sorted(REQUIRED_SOURCE_FILES - source_names)
    if missing:
        raise PackagingRefusal("source snapshot is incomplete: " + ", ".join(missing))
    source_files, source_tree, source_bytes = bind_tree(source)

    output_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="b6a-ct2-", dir=output_root) as raw:
        staging = Path(raw) / "artifact"
        completed = runner([
            "ct2-transformers-converter", "--model", str(source),
            "--output_dir", str(staging), "--quantization", "float16",
            "--copy_files", "tokenizer.json", "preprocessor_config.json",
        ], check=False)
        if completed.returncode != 0:
            raise PackagingRefusal(
                f"CTranslate2 converter exited {completed.returncode}")
        artifact_names = {p.name for p in staging.iterdir() if p.is_file()}
        missing = sorted(REQUIRED_ARTIFACT_FILES - artifact_names)
        if missing:
            raise PackagingRefusal(
                "converted artifact is incomplete: " + ", ".join(missing))
        files, tree, artifact_bytes = bind_tree(staging)
        destination = output_root / tree
        if destination.exists():
            raise PackagingRefusal(
                f"content-addressed destination already exists: {destination}")
        s3_prefix = f"s3://medzen-speech/b6a/asr/v0/{tree}/"
        manifest = {
            "schema_version": 1,
            "classification": "PLATFORM_PROOF_ONLY",
            "serving_label": "v0",
            "artifact": {
                "s3_prefix": s3_prefix,
                "tree_sha256": tree,
                "files": files,
                "model_id": "openai/whisper-large-v3",
                "base_model_revision": BASE_REVISION,
                "tokenizer_revision": BASE_REVISION,
                "processor_revision": BASE_REVISION,
                "precision": "CTranslate2_float16",
                "fine_tuned": False,
                "adapter_sha256": None,
            },
            "decode_configuration": {
                "purpose": "B6A_PLATFORM_TEST_ONLY_NOT_PROMOTION_GRADE",
                "task": "transcribe",
                "beam_size": 1,
                "best_of": 1,
                "temperature": 0.0,
                "condition_on_previous_text": False,
                "word_timestamps": False,
            },
            "quality_disclosure": {
                "production_approved": False,
                "quality_gate_outcome": "FAIL",
                "absolute_wer_max": 0.2,
                "zero_shot_base_wer": {
                    "lingala": 0.9207,
                    "luganda": 1.0659,
                    "oromo": 1.1749,
                },
            },
            "provenance": {
                "git_commit": git_commit,
                "container_image_digest": converter_image_digest,
                "converter": "ct2-transformers-converter@4.8.1",
                "converted_at_utc": converted_at_utc,
                "source_tree_sha256": source_tree,
                "source_bytes": source_bytes,
                "source_files": source_files,
            },
        }
        manifest_bytes = canonical(manifest) + b"\n"
        manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
        (staging / "MANIFEST.json").write_bytes(manifest_bytes)
        shutil.move(str(staging), destination)
    return {
        "artifact_directory": str(destination),
        "artifact_tree_sha256": tree,
        "artifact_bytes": artifact_bytes,
        "manifest_sha256": manifest_sha,
        "manifest_s3_uri": s3_prefix + "MANIFEST.json",
        "source_tree_sha256": source_tree,
        "source_bytes": source_bytes,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--git-commit", required=True)
    parser.add_argument("--converter-image-digest", required=True)
    parser.add_argument("--converted-at-utc", required=True)
    args = parser.parse_args()
    try:
        receipt = package(
            args.source, args.output_root, git_commit=args.git_commit,
            converter_image_digest=args.converter_image_digest,
            converted_at_utc=args.converted_at_utc)
    except Exception as exc:
        print(json.dumps({"status": "REFUSED", "error": type(exc).__name__}))
        return 1
    print(json.dumps({"status": "COMPLETE", **receipt}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
