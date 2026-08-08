from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class EmergencyPolicyRefusal(RuntimeError):
    """The mandatory pre-LLM emergency policy cannot be trusted."""


@dataclass(frozen=True)
class EmergencyResult:
    triggered: bool
    response_text: str | None
    policy_id: str
    policy_sha256: str


def _normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


class EmergencyChecker:
    def __init__(self, path: Path):
        try:
            raw = path.read_bytes()
            value: Any = yaml.safe_load(raw)
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
            raise EmergencyPolicyRefusal("emergency policy is unavailable") from exc
        if not isinstance(value, dict) or set(value) != {
            "version", "classification", "policy_id", "matching", "response", "rules"
        }:
            raise EmergencyPolicyRefusal("emergency policy fields are incomplete or unknown")
        matching = value["matching"]
        response = value["response"]
        if (
            value["version"] != "medzen.emergency.policy.v1"
            or value["classification"] != "B6_3_LOCAL_SYNTHETIC_ONLY"
            or value["policy_id"] != "synthetic-emergency-check-v1"
            or not isinstance(matching, dict)
            or set(matching) != {"mode", "normalized_exact_phrases"}
            or matching["mode"] != "normalized_exact_phrase"
            or not isinstance(matching["normalized_exact_phrases"], list)
            or not matching["normalized_exact_phrases"]
            or not all(isinstance(item, str) and item for item in matching["normalized_exact_phrases"])
            or not isinstance(response, dict)
            or set(response) != {"text", "tts_backend"}
            or not isinstance(response["text"], str)
            or not response["text"]
            or response["tts_backend"] != "text_only"
            or value["rules"] != [
                "runs_after_asr_and_before_rag_or_llm",
                "cannot_be_shed_or_bypassed",
                "local_synthetic_test_only_not_a_clinical_policy",
            ]
        ):
            raise EmergencyPolicyRefusal("emergency policy is unsafe or malformed")
        phrases = tuple(_normalize(item) for item in matching["normalized_exact_phrases"])
        if len(set(phrases)) != len(phrases):
            raise EmergencyPolicyRefusal("emergency policy phrases are ambiguous")
        self._phrases = frozenset(phrases)
        self._response = response["text"]
        self.policy_id = value["policy_id"]
        self.policy_sha256 = hashlib.sha256(raw).hexdigest()

    def check(self, normalized_transcript: str) -> EmergencyResult:
        triggered = _normalize(normalized_transcript) in self._phrases
        return EmergencyResult(
            triggered=triggered,
            response_text=self._response if triggered else None,
            policy_id=self.policy_id,
            policy_sha256=self.policy_sha256,
        )
