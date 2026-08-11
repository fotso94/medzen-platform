import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "audit_asr_base_model_eval_inputs",
    ROOT / "scripts" / "audit_asr_base_model_eval_inputs.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
AuditRefusal = MODULE.AuditRefusal
audit = MODULE.audit
canonical_json = MODULE.canonical_json


COMMIT = "a" * 40
INVENTORY_SHA = "b" * 64
CORRECTION_SHA = "c" * 64
ADDENDUM_SHA = "d" * 64


def row(language: str, version: str, checksum: str, **overrides: object) -> dict:
    value = {
        "audio_filepath": (
            f"s3://medzen-speech/eval/{language}/asr/{version}/audio/{checksum[:8]}.wav"
        ),
        "audio_checksum_sha256": checksum,
        "duration_s": 2.5,
        "text_normalized": "safe synthetic reference",
        "primary_language": language,
        "split": "test",
        "allowed_use": ["asr_eval"],
        "source_id": "synthetic",
        "dataset_release": "synthetic@1",
        "license_tier": "eval_only",
    }
    value.update(overrides)
    return value


def manifest(
    root: Path,
    language: str,
    version: str,
    rows: list[dict],
    filename: str = "manifest.jsonl",
) -> Path:
    path = root / language / "asr" / version / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(canonical_json(value) for value in rows))
    return path


def run(root: Path) -> dict:
    return audit(
        root,
        data_commit=COMMIT,
        source_inventory_sha256=INVENTORY_SHA,
        correction_record_sha256=CORRECTION_SHA,
        correction_addendum_sha256=ADDENDUM_SHA,
        recorded_utc="2026-08-11T20:00:00Z",
    )


def test_clean_independent_suite_passes_and_is_deterministic(tmp_path: Path) -> None:
    path = manifest(tmp_path, "lingala", "fleurs-v1", [row("lingala", "fleurs-v1", "1" * 64)])
    first = run(tmp_path)
    second = run(tmp_path)
    assert first == second
    assert first["status"] == "PASS_INPUT_FREEZE"
    assert first["execution"]["required_next_state"] == (
        "QUALIFY_EVALUATION_RUNTIME_THEN_REVIEW_PILOT_PACKET"
    )
    assert first["data_source"]["correction_addendum_sha256"] == ADDENDUM_SHA
    assert first["inventory"] == {
        "manifests": 1,
        "rows": 1,
        "hours": round(2.5 / 3600, 6),
        "languages": 1,
        "language_aliases": ["lingala"],
        "selected_manifest_generations": {
            "original": 1,
            "r2": 0,
            "preference": "manifest.r2.jsonl when present; otherwise manifest.jsonl",
        },
    }
    assert first["manifests"][0]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_duplicate_audio_refuses_without_silent_deduplication(tmp_path: Path) -> None:
    checksum = "2" * 64
    manifest(
        tmp_path,
        "ewondo",
        "soreva-v1",
        [row("ewondo", "soreva-v1", checksum), row("ewondo", "soreva-v1", checksum)],
    )
    result = run(tmp_path)
    assert result["status"] == "REFUSED_INPUT_FREEZE"
    assert result["execution"]["required_next_state"] == (
        "CORRECT_DATA_METADATA_AND_DUPLICATES_THEN_REAUDIT"
    )
    assert result["hard_findings"] == ["DUPLICATE_AUDIO_SHA256"]
    assert result["inventory"]["rows"] == 2
    assert len(result["duplicates"]) == 1


def test_eval_row_advertising_training_use_refuses(tmp_path: Path) -> None:
    manifest(
        tmp_path,
        "french",
        "aaf-test-v1",
        [
            row(
                "french",
                "aaf-test-v1",
                "3" * 64,
                allowed_use=["asr_eval", "asr_train"],
            )
        ],
    )
    result = run(tmp_path)
    assert "EVAL_ROWS_ADVERTISE_TRAINING_USE" in result["hard_findings"]
    assert result["policy_findings"]["asr_train_rows_in_eval"] == 1


def test_missing_license_tier_refuses(tmp_path: Path) -> None:
    manifest(
        tmp_path,
        "akan",
        "v1",
        [row("akan", "v1", "4" * 64, license_tier=None)],
    )
    result = run(tmp_path)
    assert "EVAL_ROWS_MISSING_LICENSE_TIER" in result["hard_findings"]
    assert result["prospective_independent_suite"]["manifests"] == 0


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"allowed_use": ["asr_train"]}, "ASR evaluation use is absent"),
        ({"audio_checksum_sha256": "bad"}, "audio SHA-256 is malformed"),
        ({"duration_s": 31}, "duration is outside"),
        ({"text_normalized": ""}, "normalized reference is empty"),
    ],
)
def test_malformed_or_unsafe_rows_fail_closed(
    tmp_path: Path, overrides: dict, message: str
) -> None:
    manifest(
        tmp_path,
        "lingala",
        "fleurs-v1",
        [row("lingala", "fleurs-v1", "5" * 64, **overrides)],
    )
    with pytest.raises(AuditRefusal, match=message):
        run(tmp_path)


def test_non_test_split_refuses_the_freeze_without_hiding_rows(tmp_path: Path) -> None:
    manifest(
        tmp_path,
        "lingala",
        "v2-holdout",
        [row("lingala", "v2-holdout", "7" * 64, split="train")],
    )
    result = run(tmp_path)
    assert "EVAL_MANIFEST_CONTAINS_NON_TEST_SPLIT" in result["hard_findings"]
    assert result["policy_findings"]["non_test_split_rows"] == 1


def test_audio_layout_does_not_define_the_eval_boundary(tmp_path: Path) -> None:
    manifest(
        tmp_path,
        "lingala",
        "fleurs-v1",
        [
            row(
                "lingala",
                "fleurs-v1",
                "6" * 64,
                audio_filepath="s3://medzen-speech/curated/lingala/asr/x.wav",
            )
        ],
    )
    result = run(tmp_path)
    assert result["status"] == "PASS_INPUT_FREEZE"
    assert result["evaluation_boundary"] == {
        "rule": "eval/<language>/**",
        "applies_to": "manifest namespace",
        "audio_object_location_is_not_scope_signal": True,
        "audio_object_requirement": "s3://medzen-speech/** plus bound SHA-256",
        "leakage_control": "full adopted train/eval audio SHA-256 disjointness",
    }


def test_audio_object_must_remain_in_the_medzen_bucket(tmp_path: Path) -> None:
    manifest(
        tmp_path,
        "lingala",
        "fleurs-v1",
        [
            row(
                "lingala",
                "fleurs-v1",
                "8" * 64,
                audio_filepath="s3://another-bucket/audio.wav",
            )
        ],
    )
    with pytest.raises(AuditRefusal, match="audio path leaves the MedZen bucket"):
        run(tmp_path)


def test_r2_is_preferred_without_counting_the_frozen_original(tmp_path: Path) -> None:
    manifest(
        tmp_path,
        "lingala",
        "fleurs-v1",
        [
            row(
                "lingala",
                "fleurs-v1",
                "9" * 64,
                allowed_use=["asr_eval", "asr_train"],
            )
        ],
    )
    manifest(
        tmp_path,
        "lingala",
        "fleurs-v1",
        [row("lingala", "fleurs-v1", "9" * 64)],
        "manifest.r2.jsonl",
    )
    result = run(tmp_path)
    assert result["status"] == "PASS_INPUT_FREEZE"
    assert result["inventory"]["rows"] == 1
    assert result["inventory"]["selected_manifest_generations"]["r2"] == 1
    assert result["manifests"][0]["path"].endswith("manifest.r2.jsonl")


def test_orphan_r2_refuses(tmp_path: Path) -> None:
    manifest(
        tmp_path,
        "lingala",
        "fleurs-v1",
        [row("lingala", "fleurs-v1", "a" * 64)],
        "manifest.r2.jsonl",
    )
    with pytest.raises(AuditRefusal, match="r2 exists without frozen original"):
        run(tmp_path)
