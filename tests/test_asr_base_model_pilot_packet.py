import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKET = (
    ROOT
    / "platform"
    / "decisions"
    / "ASR-BASE-MODEL-AWS-CHANGE-PACKET-2026-001-pilot.md"
)
SOURCE_EVIDENCE = (
    ROOT
    / "platform"
    / "evidence"
    / "B6-ASR-BASE-MODEL-SOURCES-2026-001.json"
)


def packet() -> str:
    return PACKET.read_text(encoding="utf-8")


def compact_packet() -> str:
    return " ".join(packet().split())


def sources() -> dict:
    return json.loads(SOURCE_EVIDENCE.read_text(encoding="utf-8"))


def test_packet_is_explicitly_non_executable() -> None:
    value = packet()
    assert "INPUT FREEZE PASSED" in value
    assert "BLOCKED_LOCAL_IMAGE_SCAN" in value
    assert "NOT EXECUTABLE" in value
    assert "NO APPROVAL REQUESTED" in value
    assert "approval phrase is intentionally unavailable" in value
    assert "Stage 0 remains mandatory" in value


def test_packet_binds_the_reproduced_passed_freeze() -> None:
    value = packet()
    for expected in (
        "f59692a7ab5da0a9b257792e04813ec2c4c2317ffb1d68d7e5586789afa9a0ad",
        "c5d4353b179b58d4f5c8f8770c04475ed7e2e45ef5b9518123973dc241ff930a",
        "14 r2 and 50 original manifests",
        "zero duplicate identities",
    ):
        assert expected in value
    assert "zero `asr_train`" in value


def test_packet_applies_r2_preference_and_owner_boundary() -> None:
    value = compact_packet()
    assert "select `manifest.r2.jsonl`" in value
    assert "must never count both" in value
    assert "manifest namespace `eval/<language>/**`" in value
    assert "audio object layout is not a leakage signal" in value
    assert "PASS_INPUT_FREEZE` records" in value


def test_source_record_binds_exact_candidate_bytes() -> None:
    record = sources()
    assert record["status"] == "SOURCE_IDENTITIES_MEASURED_NO_COMPUTE_AUTHORIZED"
    assert record["execution_boundary"]["pilot_permitted"] is False
    by_name = {item["candidate"]: item for item in record["candidates"]}
    assert by_name["omniASR_CTC_1B_v2"]["content_length_bytes"] == 3902956068
    assert by_name["omniASR_CTC_1B_v2"]["sha256"] == (
        "354f981756aa8f41591ea363e45b9c4eba1ec5144c2273af82e747efbb08919c"
    )
    assert by_name["omniASR_LLM_1B_v2"]["content_length_bytes"] == 9118733852
    assert by_name["omniASR_LLM_1B_v2"]["sha256"] == (
        "cceb4d9ebac3d168a6af6b26c62ce11bafc562b38976c6bfa87e7d60422c6da5"
    )
    assert record["shared_tokenizer"]["sha256"] == (
        "8aa11a1092142ef472537476ef6e76541123e2f0d789b79f3ebd119008240b1e"
    )
    assert record["method"]["aws_mutations"] == 0
    assert record["method"]["inference_started"] is False


def test_packet_binds_source_evidence_and_model_trio() -> None:
    value = packet()
    digest = hashlib.sha256(SOURCE_EVIDENCE.read_bytes()).hexdigest()
    assert digest == "34baae05d5bc74601a2228002fe6c2d86999fddfe1e152e49b4febf62e2817eb"
    assert digest in value
    for expected in (
        "Whisper large-v3",
        "Meta CTC-1B-v2",
        "Meta LLM-1B-v2",
        "145a12a668aace6c1d0d290128c1225571fc1955",
        "5adf77568813513bc3697a1501ba354c04c7b93ea374fc5407cf4f6402f7431e",
    ):
        assert expected in value


def test_pilot_selection_and_modes_are_deterministic() -> None:
    value = packet()
    assert "select the first 10" in value
    assert "hard maximum is 540 distinct rows" in value
    assert "primary mode: unconditioned/audio-only" in value
    assert "Meta CTC conditioned mode: `NOT_APPLICABLE`" in value
    assert "no prompts, context examples, proxy IDs" in value
    assert "outcome-informed decode change" in value


def test_packet_requires_local_qualification_and_authoritative_scan() -> None:
    value = packet()
    assert "run the real container read-only as its non-root user" in value
    assert "separate scan-only packet" in value
    assert "scan-passed `linux/amd64` child digest" in value
    assert "zero critical/high findings" in value
    assert "No vulnerability waiver" in value
    assert "0 critical and 4 high findings" in value
    assert "fairseq2n 0.6" in value


def test_measurement_set_is_complete_and_fail_closed() -> None:
    value = packet()
    for expected in (
        "WER/CER micro totals",
        "language-macro",
        "EOS/caps",
        "median, p95",
        "peak/baseline/sample-count GPU memory",
        "INCOMPLETE_MEASUREMENT",
        "FAILED_CLOSED_EXECUTION",
    ):
        assert expected in value


def test_cost_request_is_bounded_and_scale_to_zero() -> None:
    value = packet()
    assert "`$74.4286064216` committed" in value
    assert "`$225.5713935784` headroom" in value
    assert "`$1.0064/hour`" in value
    assert "one new `$10.00` conservative reservation" in value
    assert "two non-transferable attempts of 10,800 seconds each" in value
    assert "21,600 seconds = 6 hours = `$6.0384`" in value
    assert "scale GPU desired size to 0" in value


def test_promotion_and_full_suite_remain_prohibited() -> None:
    value = compact_packet()
    for expected in (
        "Writes to `approved/asr/`",
        "production SSM",
        "language `approved_version`",
        "full-suite scoring",
        "does not declare a winning model",
    ):
        assert expected in value
    assert "B5" in value
