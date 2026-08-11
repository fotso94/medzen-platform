from __future__ import annotations

import copy
import json
import struct
import subprocess
from pathlib import Path

import pytest

from pipeline.b6_integration_receipts import ReceiptRefusal, ReceiptStore
from scripts.b6_6_probe import (
    DIAGNOSTIC_MAX_UTF8_BYTES,
    MODEL_KEYS,
    PROOF_EXIT_CODES,
    REGISTRY,
    ProbeRefusal,
    WebSocket,
    evaluate_file_response,
    sanitize_response_body,
)
from scripts.b6_6_runner import (
    RealOperations,
    RunContext,
    StageFailure,
    safe_proof_refusal,
    safe_stage0_refusal,
)


ROOT = Path(__file__).resolve().parents[1]


def _passing_response() -> dict:
    return {
        "reply": {
            "tts_backend": "text_only",
            "text": "synthetic reply content",
            "citations": [
                {"id": "one", "snippet": "synthetic one"},
                {"id": "two", "snippet": "synthetic two"},
                {"id": "three", "snippet": "synthetic three"},
            ],
        },
        "model_versions": {
            "asr": "v0",
            "registry_snapshot": REGISTRY,
            "llm": "fake-bedrock-local-v1",
            "rag": "embedded-synthetic-v1",
            "tts": None,
        },
    }


def _encoded(value: object) -> bytes:
    return json.dumps(value, sort_keys=True).encode()


def _file_failure_cases() -> list[tuple[int, bytes, str]]:
    good = _passing_response()
    cases: list[tuple[int, bytes, str]] = [
        (503, _encoded(good), "FILE_HTTP_STATUS_IS_200"),
        (200, b"{not-json", "FILE_RESPONSE_IS_JSON"),
        (200, b"[]", "FILE_RESPONSE_IS_OBJECT"),
    ]
    mutations: list[tuple[str, object]] = [
        ("FILE_REPLY_IS_OBJECT", {**good, "reply": None}),
        (
            "FILE_TTS_BACKEND_IS_TEXT_ONLY",
            {**good, "reply": {**good["reply"], "tts_backend": "fish"}},
        ),
        (
            "FILE_CITATIONS_IS_LIST",
            {**good, "reply": {**good["reply"], "citations": "three"}},
        ),
        (
            "FILE_CITATION_COUNT_IS_THREE",
            {**good, "reply": {**good["reply"], "citations": [{}, {}]}},
        ),
        ("FILE_MODEL_VERSIONS_IS_OBJECT", {**good, "model_versions": []}),
    ]
    wrong_keys = copy.deepcopy(good)
    wrong_keys["model_versions"]["extra"] = "not-allowed"
    mutations.append(("FILE_MODEL_VERSION_KEYS_ARE_EXACT", wrong_keys))
    for assertion, key, value in (
        ("FILE_REGISTRY_SNAPSHOT_MATCHES", "registry_snapshot", "wrong"),
        ("FILE_ASR_VERSION_IS_V0", "asr", "v1"),
        ("FILE_LLM_VERSION_IS_FAKE_LOCAL", "llm", "real-provider"),
        ("FILE_TTS_VERSION_IS_NULL", "tts", "fish"),
    ):
        changed = copy.deepcopy(good)
        changed["model_versions"][key] = value
        mutations.append((assertion, changed))
    cases.extend((200, _encoded(value), assertion) for assertion, value in mutations)
    return cases


def test_file_proof_passes_the_exact_contract() -> None:
    result = evaluate_file_response(200, _encoded(_passing_response()))
    assert result["status"] == "PASS"
    assert result["citation_count"] == 3
    assert set(result["model_versions"]) == MODEL_KEYS


@pytest.mark.parametrize(("status", "raw", "assertion"), _file_failure_cases())
def test_every_file_assertion_has_a_distinct_exit_and_complete_diagnostic(
    status: int, raw: bytes, assertion: str
) -> None:
    with pytest.raises(ProbeRefusal) as captured:
        evaluate_file_response(status, raw)
    failure = captured.value
    assert failure.failed_assertion == assertion
    assert failure.exit_code == PROOF_EXIT_CODES[assertion]
    diagnostic = failure.diagnostic()
    assert diagnostic["http_status"] == status
    assert diagnostic["failed_assertion"] == assertion
    assert diagnostic["probe_exit_code"] == PROOF_EXIT_CODES[assertion]
    assert isinstance(diagnostic["sanitized_response_body"], str)
    assert len(diagnostic["sanitized_response_body"].encode()) <= DIAGNOSTIC_MAX_UTF8_BYTES
    assert diagnostic["synthetic_only"] is True
    assert diagnostic["phi_present"] is False


def test_all_probe_assertion_exit_codes_are_globally_distinct() -> None:
    assert len(set(PROOF_EXIT_CODES.values())) == len(PROOF_EXIT_CODES)


def test_response_sanitizer_redacts_content_and_credentials_and_truncates() -> None:
    raw = _encoded(
        {
            "authorization": "Bearer should-never-persist",
            "token": "secret-token",
            "reply": {"text": "x" * 4000, "tts_backend": "wrong"},
        }
    )
    body, truncated = sanitize_response_body(raw)
    assert truncated is False
    assert "should-never-persist" not in body
    assert "secret-token" not in body
    assert "x" * 20 not in body
    assert "[REDACTED]" in body
    malformed, malformed_truncated = sanitize_response_body(
        b'Bearer abc "token":"secret" ' + b"x" * 3000
    )
    assert malformed_truncated is True
    assert "Bearer abc" not in malformed
    assert '"secret"' not in malformed
    assert len(malformed.encode()) <= DIAGNOSTIC_MAX_UTF8_BYTES


def test_runner_accepts_only_complete_allowlisted_proof_diagnostics(
    tmp_path: Path,
) -> None:
    with pytest.raises(ProbeRefusal) as captured:
        evaluate_file_response(503, _encoded(_passing_response()))
    diagnostic = captured.value.diagnostic()
    path = tmp_path / "payload.json"
    path.write_text(json.dumps(diagnostic))
    assert safe_proof_refusal(path, captured.value.exit_code) == {
        key: diagnostic[key]
        for key in (
            "reason_code",
            "failed_assertion",
            "probe_exit_code",
            "http_status",
            "sanitized_response_body",
            "response_body_truncated",
            "response_body_sha256",
            "safe_error_text",
            "synthetic_only",
            "phi_present",
        )
    }
    assert safe_proof_refusal(path, 2) is None
    diagnostic["failed_assertion"] = "UNKNOWN_ASSERTION"
    path.write_text(json.dumps(diagnostic))
    assert safe_proof_refusal(path, captured.value.exit_code) is None


def test_websocket_close_frame_retains_type_code_and_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    websocket = object.__new__(WebSocket)
    payload = struct.pack("!H", 4503) + b"synthetic dependency unavailable"
    monkeypatch.setattr(websocket, "receive", lambda: (8, payload))
    with pytest.raises(ProbeRefusal) as captured:
        websocket.receive_json()
    failure = captured.value
    assert failure.failed_assertion == "WEBSOCKET_CLOSED_BEFORE_EVENT"
    assert failure.exit_code == PROOF_EXIT_CODES["WEBSOCKET_CLOSED_BEFORE_EVENT"]
    diagnostic = failure.diagnostic()
    assert diagnostic["websocket_frame_type"] == "close"
    assert diagnostic["websocket_close_code"] == 4503
    assert diagnostic["websocket_close_reason"] == (
        "synthetic dependency unavailable"
    )
    path = tmp_path / "websocket-close.json"
    path.write_text(json.dumps(diagnostic))
    retained = safe_proof_refusal(path, failure.exit_code)
    assert retained is not None
    assert retained["websocket_frame_type"] == "close"
    assert retained["websocket_close_code"] == 4503
    assert retained["websocket_close_reason"] == (
        "synthetic dependency unavailable"
    )


def test_websocket_close_reason_is_sanitized_before_runner_retention(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    websocket = object.__new__(WebSocket)
    payload = struct.pack("!H", 4503) + b"Bearer should-not-persist"
    monkeypatch.setattr(websocket, "receive", lambda: (8, payload))
    with pytest.raises(ProbeRefusal) as captured:
        websocket.receive_json()
    diagnostic = captured.value.diagnostic()
    assert diagnostic["websocket_close_reason"] == "[CREDENTIAL_REDACTED]"
    path = tmp_path / "unsafe-websocket-close.json"
    path.write_text(json.dumps(diagnostic))
    retained = safe_proof_refusal(path, captured.value.exit_code)
    assert retained is not None
    assert retained["websocket_close_reason"] == "[CREDENTIAL_REDACTED]"


def test_receipt_engine_scopes_synthetic_body_to_refused_proof_stages(
    tmp_path: Path,
) -> None:
    with pytest.raises(ProbeRefusal) as captured:
        evaluate_file_response(503, _encoded(_passing_response()))
    payload = {"proof_refusal": captured.value.diagnostic()}
    receipt = ReceiptStore(tmp_path / "allowed").persist(
        "file_proof", "REFUSED", payload
    )
    assert receipt["status"] == "REFUSED"
    with pytest.raises(ReceiptRefusal, match="outside its approved stage"):
        ReceiptStore(tmp_path / "stage0").persist("stage0", "REFUSED", payload)
    with pytest.raises(ReceiptRefusal, match="outside its approved stage"):
        ReceiptStore(tmp_path / "pass").persist("file_proof", "PASS", payload)


def test_stage0_reason_parser_retains_exact_bounded_pre_model_detail(
    tmp_path: Path,
) -> None:
    value = {
        "status": "REFUSED",
        "reason_code": "STAGE0_TEST_REGISTRY_REFUSED",
        "failed_assertion": "TEST_REGISTRY_PARAMETER_COUNT_IS_THREE",
        "stage_exit_code": 35,
        "safe_error_text": "expected three parameters; observed two",
        "pre_model_and_audio": True,
    }
    path = tmp_path / "stage0.json"
    path.write_text(json.dumps(value))
    assert safe_stage0_refusal(path, 35) == {
        "reason_code": value["reason_code"],
        "failed_assertion": value["failed_assertion"],
        "stage_exit_code": 35,
        "safe_error_text": value["safe_error_text"],
        "pre_model_and_audio": True,
    }
    assert safe_stage0_refusal(path, 2) is None


@pytest.mark.parametrize("stage", ["file_proof", "stage0"])
def test_real_operation_failure_propagates_the_safe_structured_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    if stage == "file_proof":
        with pytest.raises(ProbeRefusal) as captured:
            evaluate_file_response(503, _encoded(_passing_response()))
        payload = captured.value.diagnostic()
        exit_code = captured.value.exit_code
        expected_key = "proof_refusal"
    else:
        payload = {
            "status": "REFUSED",
            "reason_code": "STAGE0_TEST_REGISTRY_REFUSED",
            "failed_assertion": "TEST_REGISTRY_PARAMETER_COUNT_IS_THREE",
            "stage_exit_code": 35,
            "safe_error_text": "expected three; observed two",
            "pre_model_and_audio": True,
        }
        exit_code = 35
        expected_key = "stage0_refusal"

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        Path(command[-1]).write_text(json.dumps(payload))
        return subprocess.CompletedProcess(command, exit_code, "", "")

    monkeypatch.setattr("scripts.b6_6_runner.subprocess.run", fake_run)
    context = RunContext(
        kubeconfig=tmp_path / "kubeconfig",
        authorization=tmp_path / "authorization",
        packet_sha256="0" * 64,
        receipts_dir=tmp_path / "receipts",
        token_file=tmp_path / "token",
        attempt=1,
    )
    with pytest.raises(StageFailure) as refused:
        RealOperations().execute(stage, context)
    assert refused.value.payload[expected_key]["failed_assertion"] == payload[
        "failed_assertion"
    ]
    assert refused.value.payload[expected_key][
        "safe_error_text"
    ] == payload["safe_error_text"]


def test_shell_persists_probe_and_stage0_payloads_before_nonzero_return() -> None:
    source = (ROOT / "scripts/b6_6_operations.sh").read_text()
    proof = source[source.index("run_probe_stage()") : source.index("stage_failure_drills()")]
    assert proof.index('write_payload "$payload"') < proof.index(
        '[[ "$probe_status" == "0" ]] || return "$probe_status"'
    )
    stage0 = source[source.index("stage0_refuse()") : source.index("terraform_plan_receipt()")]
    assert stage0.index("write_payload") < stage0.index('return "$stage_exit_code"')


def test_runtime_receipt_policy_v3_scopes_the_synthetic_exception() -> None:
    policy = (ROOT / "platform/runtime-receipt-policy-v3.yaml").read_text()
    assert "maximum_sanitized_response_utf8_bytes: 1024" in policy
    assert "distinct_exit_code_per_assertion: true" in policy
    assert "B6_6_SYNTHETIC_INTEGRATION_ONLY" in policy
    assert "raw_request_audio_permitted: false" in policy
    assert "raw_credentials_permitted: false" in policy
    assert "An absent or REFUSED deadline receipt with zero actions is already disarmed." in policy
