from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.asr_base_model_pilot_live import normalize_container_termination
from scripts.asr_base_model_proven_commands import (
    B6A_PROVEN_NVIDIA_SMI_ARGV,
    canonical_argv_sha256,
)


def pod_with_termination(*, reason: str, exit_code: int, signal: int) -> dict:
    return {
        "status": {
            "phase": "Failed",
            "containerStatuses": [
                {
                    "name": "offline-evaluator",
                    "ready": False,
                    "restartCount": 0,
                    "state": {
                        "terminated": {
                            "exitCode": exit_code,
                            "reason": reason,
                            "signal": signal,
                            "startedAt": "2026-08-14T23:26:47Z",
                            "finishedAt": "2026-08-14T23:30:21Z",
                            "message": "token=redact-me safe terminal detail",
                        }
                    },
                }
            ],
        }
    }


def test_termination_facts_are_normalized_before_sanitized_shape_retention() -> None:
    value = normalize_container_termination(
        pod_with_termination(reason="Error", exit_code=86, signal=0)
    )
    assert value == {
        "status": "TERMINATED",
        "container_name": "offline-evaluator",
        "restart_count": 0,
        "ready": False,
        "exit_code": 86,
        "reason": "Error",
        "signal": 0,
        "oom_killed": False,
        "started_at": "2026-08-14T23:26:47Z",
        "finished_at": "2026-08-14T23:30:21Z",
        "message_sanitized": "token=<REDACTED> safe terminal detail",
    }


def test_oomkilled_is_a_distinct_normalized_fact() -> None:
    value = normalize_container_termination(
        pod_with_termination(reason="OOMKilled", exit_code=137, signal=9)
    )
    assert value["oom_killed"] is True
    assert value["exit_code"] == 137
    assert value["signal"] == 9


def test_runtime_vram_path_remains_bound_to_proven_b6a_argv() -> None:
    source = (ROOT / "scripts/asr_base_model_pilot_live.py").read_text()
    assert "B6A_PROVEN_NVIDIA_SMI_ARGV" in source
    assert canonical_argv_sha256(B6A_PROVEN_NVIDIA_SMI_ARGV) == (
        "04e6d317a48f3602402b011289224cb686ab7313aab6726051d2f089ac5bd426"
    )


def test_asset_cards_resolve_only_under_models_mount() -> None:
    from scripts.asr_base_model_pilot_k8s import asset_card_file_paths

    paths = asset_card_file_paths()
    assert paths, "asset cards must declare absolute checkpoint/tokenizer paths"
    assert all(path.startswith("/models/") for path in paths), paths


def test_card_mount_coverage_refuses_without_models_mount() -> None:
    import pytest

    from scripts.asr_base_model_pilot_k8s import validate_asset_card_mount_coverage

    pod_without = {
        "containers": [
            {
                "volumeMounts": [
                    {"name": "input", "mountPath": "/input", "readOnly": True},
                    {"name": "output", "mountPath": "/output"},
                ]
            }
        ]
    }
    with pytest.raises(ValueError, match="not covered by pod mounts"):
        validate_asset_card_mount_coverage(pod_without)
    pod_with = {
        "containers": [
            {
                "volumeMounts": [
                    {"name": "input", "mountPath": "/input", "readOnly": True},
                    {"name": "input", "mountPath": "/models", "subPath": "models", "readOnly": True},
                ]
            }
        ]
    }
    value = validate_asset_card_mount_coverage(pod_with)
    assert value["status"] == "PASS_ASSET_CARD_MOUNT_COVERAGE"


def test_pilot_driver_model_root_matches_card_layout() -> None:
    from scripts.asr_base_model_pilot_workload import _PILOT_DRIVER_PROGRAM

    assert 'model_root=pathlib.Path("/models")' in _PILOT_DRIVER_PROGRAM
    assert "/input/models" not in _PILOT_DRIVER_PROGRAM


def test_whisper_token_budget_leaves_prompt_headroom() -> None:
    import re

    source = (
        ROOT / "services/asr-eval-runtime/medzen_asr_eval/backends.py"
    ).read_text()
    match = re.search(r"WHISPER_MAX_NEW_TOKENS = (\d+)", source)
    assert match, "Whisper token budget must be a named constant"
    budget = int(match.group(1))
    assert budget <= 448 - 4, "budget must leave >=4 tokens of prompt headroom"
    assert "max_new_tokens=448" not in source
    assert "max_new_tokens=WHISPER_MAX_NEW_TOKENS" in source
    assert "tokens < WHISPER_MAX_NEW_TOKENS" in source
    assert "tokens >= WHISPER_MAX_NEW_TOKENS" in source


def test_chunked_readback_stays_under_the_ssm_output_cap() -> None:
    from scripts.asr_base_model_pilot_live import SSM_READBACK_RAW_CHUNK_BYTES

    encoded_chars = (SSM_READBACK_RAW_CHUNK_BYTES + 2) // 3 * 4
    assert encoded_chars <= 24000 - 2000, "base64 chunk must clear the cap with margin"


def test_aggregate_readback_no_longer_uses_one_shot_cat() -> None:
    source = (ROOT / "scripts/asr_base_model_pilot_live.py").read_text()
    assert "_ssm_read_file_chunked" in source
    assert 'f"cat {state[\'staging_path\']}/output/aggregate.json"' not in source


def test_fake_models_the_ssm_output_truncation() -> None:
    source = (ROOT / "scripts/asr_base_model_pilot_fake.py").read_text()
    assert "[:24000]" in source, "fake must model the StandardOutputContent cap"


def _extracted_eval_manifests(tmp_path: Path) -> Path:
    import tarfile

    root = tmp_path / "eval"
    root.mkdir()
    with tarfile.open(
        ROOT / "tests/fixtures/asr_base_model_pilot/eval-manifests-2026-08-11.tar.gz"
    ) as archive:
        archive.extractall(root)
    inner = list(root.iterdir())
    return inner[0] if len(inner) == 1 and inner[0].is_dir() else root


def test_suite_selection_first_ten_equals_pilot_selection(tmp_path: Path) -> None:
    from scripts.asr_base_model_pilot_assets import (
        _validated_language_candidates,
        select_pilot_rows,
        select_suite_rows,
    )

    root = _extracted_eval_manifests(tmp_path)
    pilot = select_pilot_rows(root)
    _, by_language = _validated_language_candidates(root)
    units = [
        {"language": language, "row_start": 0, "row_end": min(10, len(rows))}
        for language, rows in sorted(by_language.items())
    ]
    suite = select_suite_rows(root, units)
    pilot_ids = {row["audio_checksum_sha256"] for row in pilot["rows"]}
    suite_ids = {row["audio_checksum_sha256"] for row in suite["rows"]}
    assert pilot_ids <= suite_ids or suite_ids <= pilot_ids


def test_suite_selection_refuses_out_of_range_units(tmp_path: Path) -> None:
    import pytest

    from scripts.asr_base_model_pilot_assets import AssetRefusal, select_suite_rows

    root = _extracted_eval_manifests(tmp_path)
    with pytest.raises(AssetRefusal, match="range differs"):
        select_suite_rows(root, [{"language": "yemba", "row_start": 0, "row_end": 10**6}])
    with pytest.raises(AssetRefusal, match="absent language"):
        select_suite_rows(root, [{"language": "no-such-language", "row_start": 0, "row_end": 1}])
