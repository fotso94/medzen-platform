import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "services" / "asr-eval-runtime"
EVIDENCE = (
    ROOT
    / "platform"
    / "evidence"
    / "B6-ASR-EVAL-RUNTIME-LOCAL-QUALIFICATION-2026-001.json"
)
EVIDENCE_V2 = (
    ROOT
    / "platform"
    / "evidence"
    / "B6-ASR-EVAL-RUNTIME-LOCAL-QUALIFICATION-2026-002.json"
)
sys.path.insert(0, str(PACKAGE))

from medzen_asr_eval.harness import (  # noqa: E402
    EvaluationRefusal,
    canonical_json,
    select_rows,
    validate_mode,
    write_once,
)
from medzen_asr_eval.identity import CANDIDATES, SOURCE_IDENTITY  # noqa: E402


def row(checksum: str, reference: str = "synthetic reference") -> dict:
    return {
        "audio_filepath": f"s3://medzen-speech/eval/x/{checksum}.wav",
        "audio_checksum_sha256": checksum,
        "duration_s": 1.0,
        "text_normalized": reference,
        "source_id": "synthetic",
    }


def test_candidate_identity_is_exact() -> None:
    assert set(CANDIDATES) == {
        "whisper-large-v3",
        "omniASR_CTC_1B_v2",
        "omniASR_LLM_1B_v2",
    }
    assert SOURCE_IDENTITY["omnilingual_release_tag"] == "0.2.0"
    assert SOURCE_IDENTITY["omnilingual_internal_version"] == "0.1.0"


def test_selection_is_sorted_capped_and_reference_hash_only() -> None:
    rows = [row(f"{number:064x}", f"reference {number}") for number in range(12, 0, -1)]
    selected = select_rows([("eval/lingala/asr/fleurs-v1/manifest.r2.jsonl", rows)])
    assert len(selected) == 10
    assert [item["selection_ordinal"] for item in selected] == list(range(1, 11))
    assert [item["audio_checksum_sha256"] for item in selected] == [
        f"{number:064x}" for number in range(1, 11)
    ]
    assert all("text_normalized" not in item for item in selected)
    assert all(len(item["reference_sha256"]) == 64 for item in selected)


def test_selection_refuses_global_duplicate() -> None:
    checksum = "a" * 64
    with pytest.raises(EvaluationRefusal, match="duplicate audio checksum"):
        select_rows(
            [
                ("eval/lingala/asr/fleurs-v1/manifest.jsonl", [row(checksum)]),
                ("eval/oromo/asr/fleurs-v1/manifest.jsonl", [row(checksum)]),
            ]
        )


def test_historical_versions_are_not_selected() -> None:
    selected = select_rows(
        [("eval/lingala/asr/v1/manifest.r2.jsonl", [row("b" * 64)])]
    )
    assert selected == []


def test_conditioning_fails_closed() -> None:
    validate_mode("whisper-large-v3", "unconditioned", None)
    validate_mode("omniASR_LLM_1B_v2", "conditioned", "eng_Latn")
    with pytest.raises(EvaluationRefusal, match="NOT_APPLICABLE"):
        validate_mode("omniASR_CTC_1B_v2", "conditioned", "eng_Latn")
    with pytest.raises(EvaluationRefusal, match="requires an exact reviewed"):
        validate_mode("omniASR_LLM_1B_v2", "conditioned", None)
    with pytest.raises(EvaluationRefusal, match="forbids a language"):
        validate_mode("whisper-large-v3", "unconditioned", "en")
    with pytest.raises(EvaluationRefusal, match="unknown candidate"):
        validate_mode("invented", "unconditioned", None)


def test_receipt_is_fsynced_write_once(tmp_path: Path) -> None:
    receipt = tmp_path / "receipts" / "row.json"
    value = {"status": "PASS", "audio_sha256": "c" * 64}
    digest = write_once(receipt, value)
    assert receipt.read_bytes() == canonical_json(value)
    assert len(digest) == 64
    with pytest.raises(FileExistsError):
        write_once(receipt, value)


def test_assets_are_local_and_never_remote() -> None:
    value = (PACKAGE / "assets" / "models.yaml").read_text()
    assert "https://" not in value
    assert "s3://" not in value
    assert "/models/omniASR-CTC-1B-v2.pt" in value
    assert "/models/omniASR-LLM-1B-v2.pt" in value


def test_runtime_source_has_no_implicit_download_path() -> None:
    value = (PACKAGE / "medzen_asr_eval" / "backends.py").read_text()
    assert "https://" not in value
    assert "snapshot_download" not in value
    assert "from_pretrained" not in value
    assert "beam_size=1" in value
    assert "condition_on_previous_text=False" in value


def test_runtime_pins_supported_meta_torch_and_remediated_arrow() -> None:
    dockerfile = (PACKAGE / "Dockerfile").read_text()
    requirements = (PACKAGE / "requirements.txt").read_text().splitlines()
    assert '"torch==2.8.0" "torchaudio==2.8.0"' in dockerfile
    assert "pyarrow==23.0.1" in requirements
    assert "pyarrow==20.0.0" not in requirements


def test_local_qualification_evidence_refuses_the_scan_without_a_waiver() -> None:
    record = json.loads(EVIDENCE.read_text())
    assert record["status"] == "BLOCKED_LOCAL_IMAGE_SCAN"
    assert record["qualification"]["status"] == (
        "PASS_LOCAL_RUNTIME_IMPORT_QUALIFICATION"
    )
    assert record["local_scan"]["critical"] == 0
    assert record["local_scan"]["high"] == 4
    assert record["local_scan"]["waiver_used"] is False
    assert record["image"]["source_worktree_clean_at_build"] is False
    assert record["image"]["publication_eligible"] is False
    assert {item["package"] for item in record["local_scan"]["findings"]} == {
        "torch"
    }
    assert record["remediation"]["torch_upgrade_attempt"] == (
        "REFUSED_BY_UPSTREAM_RUNTIME"
    )
    assert record["remediation"]["unsupported_override_used"] is False
    assert record["execution"]["aws_mutations"] == 0
    assert record["execution"]["ecr_push_attempted"] is False
    assert record["execution"]["pilot_permitted"] is False


def test_clean_source_qualification_preserves_the_exact_four_findings() -> None:
    record = json.loads(EVIDENCE_V2.read_text())
    assert record["status"] == (
        "PASS_LOCAL_RUNTIME_WITH_OWNER_ACCEPTANCE_REQUIRED_FOR_FOUR_HIGHS"
    )
    assert record["image"]["source_worktree_clean_at_build"] is True
    assert record["image"]["classification_label"] == "offline-evaluation-only"
    assert record["local_scan"]["critical"] == 0
    assert record["local_scan"]["high"] == 4
    assert {item["id"] for item in record["local_scan"]["findings"]} == {
        "CVE-2026-24747",
        "CVE-2026-4538",
        "CVE-2025-55552",
        "CVE-2025-55551",
    }
    assert set(record["reachable_source_inspection"]["prohibited_api_occurrences"].values()) == {0}
    assert record["execution"]["aws_mutations"] == 0
    assert record["execution"]["pilot_permitted_without_reviewed_risk_acceptance_and_packet_approval"] is False
