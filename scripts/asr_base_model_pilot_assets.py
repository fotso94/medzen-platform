#!/usr/bin/env python3
"""Deterministically select and stage the exact offline-pilot inputs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tarfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Protocol


PROSPECTIVE_VERSIONS = {"aaf-test-v1", "cv17-test-v1", "fleurs-v1", "soreva-v1"}
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
META_ASSETS = {
    "omniASR-CTC-1B-v2.pt": {
        "url": "https://dl.fbaipublicfiles.com/mms/omniASR-CTC-1B-v2.pt",
        "bytes": 3_902_956_068,
        "sha256": "354f981756aa8f41591ea363e45b9c4eba1ec5144c2273af82e747efbb08919c",
    },
    "omniASR-LLM-1B-v2.pt": {
        "url": "https://dl.fbaipublicfiles.com/mms/omniASR-LLM-1B-v2.pt",
        "bytes": 9_118_733_852,
        "sha256": "cceb4d9ebac3d168a6af6b26c62ce11bafc562b38976c6bfa87e7d60422c6da5",
    },
    "omniASR_tokenizer_written_v2.model": {
        "url": "https://dl.fbaipublicfiles.com/mms/omniASR_tokenizer_written_v2.model",
        "bytes": None,
        "sha256": "8aa11a1092142ef472537476ef6e76541123e2f0d789b79f3ebd119008240b1e",
    },
}
WHISPER_PREFIX = "b6a/asr/v0/5adf77568813513bc3697a1501ba354c04c7b93ea374fc5407cf4f6402f7431e/"
WHISPER_TREE = "5adf77568813513bc3697a1501ba354c04c7b93ea374fc5407cf4f6402f7431e"
MAX_CREATE_ONLY_PART_BYTES = 4 * 1024 * 1024 * 1024


class AssetRefusal(RuntimeError):
    pass


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def selected_manifests(root: Path) -> list[Path]:
    result = []
    for directory in sorted(path for path in root.glob("*/asr/*") if path.is_dir()):
        original = directory / "manifest.jsonl"
        corrected = directory / "manifest.r2.jsonl"
        if corrected.exists() and not original.exists():
            raise AssetRefusal("orphan manifest.r2.jsonl")
        if original.exists() and directory.name in PROSPECTIVE_VERSIONS:
            result.append(corrected if corrected.exists() else original)
    if not result:
        raise AssetRefusal("prospective manifests are absent")
    return result


def pilot_bundle_identity(selection_sha256: str) -> dict[str, Any]:
    if SHA_RE.fullmatch(selection_sha256) is None:
        raise AssetRefusal("selection hash is malformed")
    identity = {
        "schema_version": 1,
        "selection_sha256": selection_sha256,
        "meta_assets": {
            name: {"sha256": value["sha256"], "bytes": value["bytes"]}
            for name, value in sorted(META_ASSETS.items())
        },
        "whisper_tree_sha256": WHISPER_TREE,
        "classification": "PUBLIC_RESEARCH_NO_PHI",
    }
    return {**identity, "sha256": hashlib.sha256(canonical_json(identity)).hexdigest()}


def _validated_language_candidates(
    root: Path,
) -> tuple[list[Path], dict[str, list[dict[str, Any]]]]:
    """Collect every boundary-validated candidate row, keyed by language.

    Shared by the pilot and suite selections; each language's candidates are
    returned checksum-sorted so both selections are deterministic slices of
    the same ordering."""
    manifests = selected_manifests(root)
    by_language: dict[str, list[dict[str, Any]]] = {}
    for path in manifests:
        relative = path.relative_to(root).as_posix()
        language = relative.split("/", 1)[0]
        candidates = []
        for number, line in enumerate(path.read_bytes().splitlines(), 1):
            try:
                row = json.loads(line)
            except Exception as exc:
                raise AssetRefusal(f"malformed manifest row: {relative}:{number}") from exc
            checksum = row.get("audio_checksum_sha256")
            uses = row.get("allowed_use")
            if (
                not isinstance(checksum, str)
                or SHA_RE.fullmatch(checksum) is None
                or row.get("primary_language") != language
                or row.get("split") != "test"
                or not isinstance(uses, list)
                or "asr_eval" not in uses
                or "asr_train" in uses
                or row.get("license_tier") is None
            ):
                raise AssetRefusal(f"row is outside the frozen evaluation boundary: {relative}:{number}")
            audio_uri = row.get("audio_filepath")
            reference = row.get("text_normalized")
            duration = row.get("duration_s")
            if (
                not isinstance(audio_uri, str)
                or not audio_uri.startswith("s3://medzen-speech/")
                or not isinstance(reference, str)
                or not reference.strip()
                or isinstance(duration, bool)
                or not isinstance(duration, (int, float))
                or not 0 < float(duration) <= 30
            ):
                raise AssetRefusal(f"row payload differs: {relative}:{number}")
            candidates.append({
                "manifest": f"eval/{relative}",
                "manifest_line": number,
                "language": language,
                "source_id": str(row["source_id"]),
                "audio_s3_uri": audio_uri,
                "audio_checksum_sha256": checksum,
                "duration_s": float(duration),
                "reference": reference,
                "reference_sha256": hashlib.sha256(reference.encode()).hexdigest(),
            })
        by_language.setdefault(language, []).extend(candidates)
    for language in by_language:
        by_language[language].sort(key=lambda value: value["audio_checksum_sha256"])
    return manifests, by_language


def _finalize_selection(
    selected: list[dict[str, Any]],
    *,
    manifest_count: int,
    status: str,
) -> dict[str, Any]:
    identities = {row["audio_checksum_sha256"] for row in selected}
    if len(identities) != len(selected):
        raise AssetRefusal("selection has duplicate audio identity")
    public_rows = [
        {key: value for key, value in row.items() if key != "reference"}
        for row in selected
    ]
    public_sha = hashlib.sha256(canonical_json(public_rows)).hexdigest()
    return {
        "schema_version": 1,
        "status": status,
        "classification": "PUBLIC_RESEARCH_NO_PHI",
        "manifest_count": manifest_count,
        "rows": selected,
        "public_row_list_sha256": public_sha,
    }


def select_pilot_rows(root: Path) -> dict[str, Any]:
    manifests, by_language = _validated_language_candidates(root)
    selected: list[dict[str, Any]] = []
    for path in manifests:
        relative = path.relative_to(root).as_posix()
        language = relative.split("/", 1)[0]
        manifest_key = f"eval/{relative}"
        manifest_rows = [
            row
            for row in by_language[language]
            if row["manifest"] == manifest_key
        ]
        for ordinal, row in enumerate(manifest_rows[:10], 1):
            selected.append({**row, "selection_ordinal": ordinal})
    if not 1 <= len(selected) <= 540:
        raise AssetRefusal("pilot selection is outside the 540-row maximum")
    return _finalize_selection(
        selected,
        manifest_count=len(manifests),
        status="PASS_DETERMINISTIC_PILOT_SELECTION",
    )


SUITE_MAXIMUM_ROWS = 6000


def select_suite_rows(root: Path, units: list[dict[str, Any]]) -> dict[str, Any]:
    """Deterministic shard selection: checksum-sorted per-language slices.

    Each unit is {"language", "row_start", "row_end"} from the committed
    shard manifest; ranges index the language's checksum-sorted validated
    candidates across ALL of its prospective manifests."""
    manifests, by_language = _validated_language_candidates(root)
    selected: list[dict[str, Any]] = []
    for unit in sorted(units, key=lambda value: (value["language"], value["row_start"])):
        language = unit["language"]
        start, end = int(unit["row_start"]), int(unit["row_end"])
        rows = by_language.get(language)
        if rows is None:
            raise AssetRefusal(f"suite unit references an absent language: {language}")
        if not 0 <= start < end <= len(rows):
            raise AssetRefusal(
                f"suite unit range differs for {language}: "
                f"[{start}:{end}] over {len(rows)} candidates"
            )
        for ordinal, row in enumerate(rows[start:end], start + 1):
            selected.append({**row, "selection_ordinal": ordinal})
    if not 1 <= len(selected) <= SUITE_MAXIMUM_ROWS:
        raise AssetRefusal("suite selection is outside the shard maximum")
    return _finalize_selection(
        selected,
        manifest_count=len(manifests),
        status="PASS_DETERMINISTIC_SUITE_SELECTION",
    )


class ObjectStore(Protocol):
    def download(self, bucket: str, key: str, destination: Path) -> None: ...
    def upload_create_only(self, source: Path, bucket: str, key: str, sha256: str) -> str: ...


def parse_s3(uri: str) -> tuple[str, str]:
    if not uri.startswith("s3://"):
        raise AssetRefusal("S3 URI is malformed")
    bucket, separator, key = uri[5:].partition("/")
    if not separator or bucket != "medzen-speech" or not key or ".." in PurePosixPath(key).parts:
        raise AssetRefusal("S3 URI leaves the exact bucket boundary")
    return bucket, key


def download_https(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "MedZen-ASR-offline-eval/1"})
    destination.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(request, timeout=120) as response, destination.open("xb") as output:
        shutil.copyfileobj(response, output, length=8 * 1024 * 1024)


def _upload_parts(
    source: Path, store: ObjectStore, prefix: str, logical_name: str, workdir: Path
) -> tuple[list[dict[str, Any]], str, int]:
    digest, size = sha256_file(source)
    records = []
    with source.open("rb") as stream:
        part_number = 0
        while stream.tell() < size:
            part_path = workdir / "upload-parts" / logical_name / f"part-{part_number:04d}"
            part_path.parent.mkdir(parents=True, exist_ok=True)
            remaining = min(MAX_CREATE_ONLY_PART_BYTES, size - stream.tell())
            # A model smaller than the logical-object ceiling can be uploaded
            # directly. Larger models retain the historical 4-GiB logical
            # object layout, but temporary split files are removed as soon as
            # their independently managed multipart transfer completes.
            direct = stream.tell() == 0 and remaining == size
            upload_path = source if direct else part_path
            if not direct:
                with part_path.open("xb") as output:
                    while remaining:
                        block = stream.read(min(8 * 1024 * 1024, remaining))
                        if not block:
                            raise AssetRefusal(f"unexpected EOF splitting asset: {logical_name}")
                        output.write(block)
                        remaining -= len(block)
            else:
                stream.seek(size)
            try:
                part_sha, part_size = sha256_file(upload_path)
                key = prefix + f"bundles/{logical_name}.parts/part-{part_number:04d}"
                version = store.upload_create_only(upload_path, "medzen-speech", key, part_sha)
                records.append({"key": key, "sha256": part_sha, "bytes": part_size, "version_id": version})
            finally:
                if not direct and part_path.exists():
                    try:
                        part_path.unlink()
                    except OSError:
                        # A local temporary-file cleanup must never replace an
                        # upload/hash failure. The external evidence workdir is
                        # removed after the run even when this unlink refuses.
                        pass
            part_number += 1
    if not records or sum(value["bytes"] for value in records) != size:
        raise AssetRefusal(f"asset split differs: {logical_name}")
    return records, digest, size


def stage_assets(
    selection: dict[str, Any],
    store: ObjectStore,
    workdir: Path,
    prefix: str,
    *,
    model_cache: Path | None = None,
    audio_cache: Path | None = None,
) -> dict[str, Any]:
    if selection.get("status") != "PASS_DETERMINISTIC_PILOT_SELECTION":
        raise AssetRefusal("pilot selection is not PASS")
    if not prefix.startswith("research/asr-base-model/pilot/") or not prefix.endswith("/"):
        raise AssetRefusal("research staging prefix differs")
    if workdir.exists():
        raise AssetRefusal("asset staging directory already exists")
    workdir.mkdir(parents=True)
    objects: list[dict[str, Any]] = []
    assemblies: dict[str, dict[str, Any]] = {}

    for name, expected in META_ASSETS.items():
        path = workdir / "models" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        cached = model_cache / name if model_cache is not None else None
        if cached is not None and cached.is_file():
            try:
                os.link(cached, path)
            except OSError:
                shutil.copyfile(cached, path)
        else:
            download_https(expected["url"], path)
        digest, size = sha256_file(path)
        if digest != expected["sha256"] or (expected["bytes"] is not None and size != expected["bytes"]):
            raise AssetRefusal(f"Meta asset identity differs: {name}")
        part_records, split_digest, split_size = _upload_parts(path, store, prefix, name, workdir)
        if split_digest != digest or split_size != size:
            raise AssetRefusal(f"Meta asset split differs: {name}")
        objects.extend(part_records)
        assemblies[name] = {
            "sha256": digest,
            "bytes": size,
            "parts": part_records,
            "destination": f"models/{name}",
            "archive": False,
        }

    whisper_manifest = workdir / "whisper-MANIFEST.json"
    store.download("medzen-speech", WHISPER_PREFIX + "MANIFEST.json", whisper_manifest)
    manifest = json.loads(whisper_manifest.read_bytes())
    if manifest.get("artifact", {}).get("tree_sha256") != WHISPER_TREE:
        raise AssetRefusal("Whisper manifest tree differs")
    whisper_files = manifest["artifact"]["files"]
    model_bindings = {
        "schema_version": 1,
        "whisper_tree_sha256": WHISPER_TREE,
        "whisper_files": whisper_files,
        "meta_files": {
            name: {"sha256": expected["sha256"], "bytes": expected["bytes"]}
            for name, expected in META_ASSETS.items()
        },
    }
    for relative, expected in sorted(whisper_files.items()):
        path = workdir / "models/whisper-large-v3-ct2" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        store.download("medzen-speech", WHISPER_PREFIX + relative, path)
        digest, size = sha256_file(path)
        if digest != expected["sha256"] or size != expected["bytes"]:
            raise AssetRefusal(f"Whisper file identity differs: {relative}")

    runtime_rows = []
    for row in selection["rows"]:
        source_bucket, source_key = parse_s3(row["audio_s3_uri"])
        suffix = Path(source_key).suffix.lower()
        if suffix not in {".wav", ".flac", ".mp3", ".ogg", ".opus", ".m4a"}:
            suffix = ".audio"
        filename = row["audio_checksum_sha256"] + suffix
        path = workdir / "audio" / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        cached_audio = audio_cache / filename if audio_cache is not None else None
        if cached_audio is not None and cached_audio.is_file():
            try:
                os.link(cached_audio, path)
            except OSError:
                shutil.copyfile(cached_audio, path)
        else:
            store.download(source_bucket, source_key, path)
        digest, size = sha256_file(path)
        if digest != row["audio_checksum_sha256"]:
            raise AssetRefusal("audio identity differs after download")
        runtime_rows.append({
            "manifest": row["manifest"],
            "language": row["language"],
            "source_id": row["source_id"],
            "audio_local_path": "/input/audio/" + filename,
            "audio_checksum_sha256": digest,
            "duration_s": row["duration_s"],
            "reference": row["reference"],
            "reference_sha256": row["reference_sha256"],
            "selection_ordinal": row["selection_ordinal"],
        })

    audio_archive = workdir / "audio-bundle.tar"
    with tarfile.open(audio_archive, "x") as archive:
        for path in sorted((workdir / "audio").iterdir(), key=lambda value: value.name):
            info = tarfile.TarInfo(name=f"audio/{path.name}")
            info.size = path.stat().st_size
            info.mode = 0o444
            info.uid = info.gid = 10001
            info.uname = info.gname = "medzen"
            info.mtime = 0
            with path.open("rb") as source:
                archive.addfile(info, source)
    audio_parts, audio_sha, audio_bytes = _upload_parts(audio_archive, store, prefix, "audio-bundle.tar", workdir)
    objects.extend(audio_parts)
    assemblies["audio-bundle.tar"] = {
        "sha256": audio_sha,
        "bytes": audio_bytes,
        "parts": audio_parts,
        "destination": "audio-bundle.tar",
        "archive": True,
        "extract_to": ".",
        "files": len(runtime_rows),
    }

    metadata = {
        "runtime-rows.json": {"schema_version": 1, "classification": "PUBLIC_RESEARCH_NO_PHI", "rows": runtime_rows},
        "model-bindings.json": model_bindings,
    }
    for name, value in metadata.items():
        path = workdir / name
        path.write_bytes(canonical_json(value))
        digest, size = sha256_file(path)
        key = prefix + name
        version = store.upload_create_only(path, "medzen-speech", key, digest)
        objects.append({"key": key, "sha256": digest, "bytes": size, "version_id": version})

    bundle = {
        "schema_version": 1,
        "classification": "PUBLIC_RESEARCH_NO_PHI",
        "selection_sha256": selection["public_row_list_sha256"],
        "objects": sorted(objects, key=lambda value: value["key"]),
        "assemblies": assemblies,
        "whisper": {"read_only_source_prefix": WHISPER_PREFIX, "tree_sha256": WHISPER_TREE},
    }
    bundle["bundle_identity"] = pilot_bundle_identity(selection["public_row_list_sha256"])
    bundle["receipt_sha256"] = hashlib.sha256(canonical_json(bundle)).hexdigest()
    return bundle
