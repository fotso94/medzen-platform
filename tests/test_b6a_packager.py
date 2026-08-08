from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.package_b6a_zero_shot import PackagingRefusal, package


GIT = "a" * 40
IMAGE = "sha256:" + "b" * 64
WHEN = "2026-08-04T02:00:00Z"


def source(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    root.mkdir()
    for name in ("config.json", "model.safetensors",
                 "preprocessor_config.json", "tokenizer.json"):
        (root / name).write_bytes((name + "\n").encode())
    return root


def runner(command, check):
    assert check is False
    output = Path(command[command.index("--output_dir") + 1])
    output.mkdir()
    for name in ("config.json", "model.bin", "preprocessor_config.json",
                 "tokenizer.json", "vocabulary.json"):
        (output / name).write_bytes((name + "\n").encode())
    return type("Completed", (), {"returncode": 0})()


def test_packager_writes_content_addressed_manifest_with_failure_disclosure(tmp_path):
    receipt = package(source(tmp_path), tmp_path / "out", git_commit=GIT,
                      converter_image_digest=IMAGE, converted_at_utc=WHEN,
                      runner=runner)
    destination = Path(receipt["artifact_directory"])
    manifest_bytes = (destination / "MANIFEST.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    assert destination.name == receipt["artifact_tree_sha256"]
    assert receipt["manifest_sha256"] == hashlib.sha256(manifest_bytes).hexdigest()
    assert manifest["artifact"]["s3_prefix"].endswith(destination.name + "/")
    assert manifest["quality_disclosure"] == {
        "production_approved": False,
        "quality_gate_outcome": "FAIL",
        "absolute_wer_max": 0.2,
        "zero_shot_base_wer": {
            "lingala": 0.9207, "luganda": 1.0659, "oromo": 1.1749},
    }
    assert manifest["provenance"]["container_image_digest"] == IMAGE
    assert manifest["provenance"]["source_tree_sha256"] == receipt[
        "source_tree_sha256"]


def test_packager_is_deterministic_for_identical_inputs(tmp_path):
    src = source(tmp_path)
    first = package(src, tmp_path / "one", git_commit=GIT,
                    converter_image_digest=IMAGE, converted_at_utc=WHEN,
                    runner=runner)
    second = package(src, tmp_path / "two", git_commit=GIT,
                     converter_image_digest=IMAGE, converted_at_utc=WHEN,
                     runner=runner)
    assert first["artifact_tree_sha256"] == second["artifact_tree_sha256"]
    assert first["manifest_sha256"] == second["manifest_sha256"]
    assert (Path(first["artifact_directory"]) / "MANIFEST.json").read_bytes() == (
        Path(second["artifact_directory"]) / "MANIFEST.json").read_bytes()


@pytest.mark.parametrize("field,value,reason", [
    ("git_commit", "short", "Git commit"),
    ("converter_image_digest", "b" * 64, "image digest"),
    ("converted_at_utc", "2026-08-04", "end in Z"),
])
def test_packager_refuses_missing_provenance(tmp_path, field, value, reason):
    kwargs = {"git_commit": GIT, "converter_image_digest": IMAGE,
              "converted_at_utc": WHEN}
    kwargs[field] = value
    with pytest.raises(PackagingRefusal, match=reason):
        package(source(tmp_path), tmp_path / "out", runner=runner, **kwargs)


def test_packager_refuses_incomplete_source_or_converter_failure(tmp_path):
    src = source(tmp_path)
    (src / "model.safetensors").unlink()
    with pytest.raises(PackagingRefusal, match="source snapshot is incomplete"):
        package(src, tmp_path / "out", git_commit=GIT,
                converter_image_digest=IMAGE, converted_at_utc=WHEN,
                runner=runner)

    shutil.rmtree(src)
    src = source(tmp_path)

    def failed(command, check):
        return type("Completed", (), {"returncode": 7})()

    with pytest.raises(PackagingRefusal, match="exited 7"):
        package(src, tmp_path / "out2", git_commit=GIT,
                converter_image_digest=IMAGE, converted_at_utc=WHEN,
                runner=failed)
