from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ProviderError(RuntimeError):
    """B6v2: a provider-level refusal (e.g. blank grounding) — the
    gateway maps it to a refusal response; it must never surface as an
    unhandled 500."""


@dataclass(frozen=True)
class ProviderRequest:
    language: str
    response_language: str
    policy_id: str
    normalized_transcript: str
    citations: tuple[dict[str, Any], ...]
    citation_binding_sha256: str
    maximum_output_tokens: int
    # Phase 2: prior (role, text) turns, strictly alternating, user first
    history: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ProviderResult:
    text: str
    cited_document_ids: tuple[str, ...]
    citation_binding_sha256: str
    model_version: str
    # B6v2: hash of the exact citation bytes sent to the provider —
    # auditable grounding identity (was computed then DISCARDED)
    grounding_sha256: str | None = None


class FakeBedrockProvider:
    """Deterministic local provider. It contains no AWS client or network path."""

    name = "fake_bedrock"
    model_version = "fake-bedrock-local-v1"

    def __init__(self, outcomes: list[str] | None = None):
        self.outcomes = list(outcomes or ["success"])
        self.calls: list[tuple[ProviderRequest, int]] = []

    def invoke(self, request: ProviderRequest, *, timeout_ms: int) -> ProviderResult:
        self.calls.append((request, timeout_ms))
        outcome = self.outcomes.pop(0) if self.outcomes else "success"
        if outcome == "timeout":
            raise TimeoutError("synthetic provider timeout")
        if outcome == "unavailable":
            raise RuntimeError("synthetic provider unavailable")
        document_ids = tuple(item["document_id"] for item in request.citations)
        binding = request.citation_binding_sha256
        if outcome == "cites_nothing":
            # a real model that used none of the supplied documents
            document_ids = ()
        elif outcome == "tampered_citation":
            document_ids = ("not-supplied",)
            binding = "0" * 64
        elif outcome != "success":
            raise RuntimeError("unknown synthetic provider outcome")
        return ProviderResult(
            text=(
                f"[{request.response_language} synthetic] "
                f"Answer supported only by {', '.join(document_ids)}."
            ),
            cited_document_ids=document_ids,
            citation_binding_sha256=binding,
            model_version=self.model_version,
        )

    def invoke_stream(self, request: ProviderRequest, *, timeout_ms: int,
                      on_delta) -> ProviderResult:
        """Phase 3b: deterministic delta narration of the same result."""
        result = self.invoke(request, timeout_ms=timeout_ms)
        half = max(1, len(result.text) // 2)
        on_delta(result.text[:half])
        on_delta(result.text[half:])
        return result


# A retry is only worth making with this much of the policy budget left.
MINIMUM_RETRY_MS = 4000


class _ReplyTextExtractor:
    """Incrementally decodes the value of the reply's "text" JSON string as
    raw model output streams in. Emits only fully-decodable characters (a
    trailing partial escape such as a lone backslash or \\u12 is held back)."""

    def __init__(self):
        self.raw = ""
        self.started = False
        self.finished = False
        self.emitted = ""
        self._start = 0

    def feed(self, chunk: str) -> str:
        import json as _json, re as _re
        if self.finished:
            return ""
        self.raw += chunk
        if not self.started:
            # Narrate only the contracted top-level FIRST field.  Searching
            # for `"text"` anywhere also matched nested objects, so a reply
            # such as {"other":{"text":"wrong"},"text":"right",...}
            # displayed "wrong" before finalising as "right".  A reordered
            # or fenced object still finalises normally; it simply does not
            # stream speculative text.
            m = _re.match(r'^\s*\{\s*"text"\s*:\s*"', self.raw)
            if not m:
                return ""
            self.started = True
            self._start = m.end()
        body = self.raw[self._start:]
        i, end = 0, None
        while i < len(body):
            c = body[i]
            if c == "\\":
                i += 2
                continue
            if c == '"':
                end = i
                break
            i += 1
        if end is not None:
            self.finished = True
            safe = body[:end]
        else:
            safe = body
            bs = len(safe) - len(safe.rstrip("\\"))
            if bs % 2 == 1:
                safe = safe[:-1]
            m2 = _re.search(r'\\u[0-9a-fA-F]{0,3}$', safe)
            if m2:
                safe = safe[:m2.start()]
        try:
            decoded = _json.loads('"' + safe + '"')
        except ValueError:
            return ""
        fresh = decoded[len(self.emitted):]
        if fresh:
            self.emitted = decoded
        return fresh


class BedrockProvider:
    """Real Bedrock backend via the Converse API (model-family agnostic, so
    the pinned model can move between Anthropic/Nova without code changes).

    The model NEVER sees or invents the citation binding: the gateway's own
    binding is echoed back verbatim, and the model may cite only supplied
    document ids — anything else is surfaced for the gateway's tamper check.
    boto3 is imported lazily so contract tests never need AWS installed.
    """

    name = "bedrock"

    def __init__(self, model_id: str, region: str, client: Any | None = None):
        if not model_id:
            raise ValueError("MEDZEN_BEDROCK_MODEL_ID is required for the "
                             "bedrock provider — there is no default model")
        self.model_id = model_id
        # B6v2 round 3 (Codex): the registry's v2 identity is
        # "bedrock:<model-id>" (V2_LLM_RE); a bare model id can never
        # match a v2 route, so the orchestrator would refuse every
        # real response at the version check.
        self.model_version = f"bedrock:{model_id}"
        self._region = region
        self._client = client

    @staticmethod
    def _parse_reply(raw: str) -> tuple[str, tuple[str, ...]]:
        """The contracted reply: ONE JSON object {text, cited_document_ids}.
        Tolerates fences and stray prose AROUND the object; never invents
        one (no object -> ValueError)."""
        import json as _json
        if raw.startswith("```"):
            raw = raw.strip("`").removeprefix("json").strip()
        if not raw.startswith("{"):
            start, end = raw.find("{"), raw.rfind("}")
            if start >= 0 and end > start:
                raw = raw[start:end + 1]
        try:
            payload = _json.loads(raw)
            text = str(payload["text"])
            cited = tuple(str(x) for x in payload.get("cited_document_ids", []))
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError(f"not the contracted JSON shape: {exc}") from exc
        if not text.strip():
            raise ValueError("empty text")
        return text, cited

    def _converse(self, system, messages, config, timeout_ms, _on_delta,
                  fresh_deadline: bool = False):
        client = (self._bedrock_within(timeout_ms) if fresh_deadline
                  else self._bedrock(timeout_ms))
        response = client.converse(
            modelId=self.model_id, system=[{"text": system}],
            messages=messages, inferenceConfig=config)
        parts = response.get("output", {}).get("message", {}).get("content", [])
        return "".join(p.get("text", "") for p in parts), response.get("stopReason")

    def _converse_stream(self, system, messages, config, timeout_ms, on_delta,
                         fresh_deadline: bool = False):
        client = (self._bedrock_within(timeout_ms) if fresh_deadline
                  else self._bedrock(timeout_ms))
        response = client.converse_stream(
            modelId=self.model_id, system=[{"text": system}],
            messages=messages, inferenceConfig=config)
        raw, stop_reason = "", None
        extractor = _ReplyTextExtractor()
        for event in response.get("stream", []):
            delta = (event.get("contentBlockDelta") or {}).get("delta") or {}
            text = delta.get("text")          # reasoning/tool deltas are skipped
            if text:
                raw += text
                fresh = extractor.feed(text)
                if fresh:
                    on_delta(fresh)
            stop = (event.get("messageStop") or {}).get("stopReason")
            if stop:
                stop_reason = stop
        return raw, stop_reason

    def _bedrock_within(self, timeout_ms: int):
        """A client whose read timeout is the REMAINING budget (used for the
        single retry). An injected client (tests, fakes) is reused as is."""
        if self._client is not None and not getattr(self, "_client_owned", False):
            return self._client
        return self._new_bedrock_client(timeout_ms)

    def _new_bedrock_client(self, timeout_ms: int):
        """Build a client whose connect+read envelope fits one call budget."""
        import boto3
        from botocore.config import Config
        total_seconds = max(1.0, timeout_ms / 1000.0)
        connect_seconds = min(2.0, max(0.25, total_seconds * 0.10))
        read_seconds = max(0.25, total_seconds - connect_seconds)
        return boto3.client(
            "bedrock-runtime", region_name=self._region,
            config=Config(read_timeout=read_seconds,
                          connect_timeout=connect_seconds,
                          # One SDK attempt: a hidden transport retry could
                          # otherwise spend the request budget twice.
                          retries={"total_max_attempts": 1, "mode": "standard"}))

    def _bedrock(self, timeout_ms: int):
        if self._client is not None:
            return self._client
        self._client = self._new_bedrock_client(timeout_ms)
        self._client_owned = True
        return self._client

    def invoke(self, request: ProviderRequest, *, timeout_ms: int) -> ProviderResult:
        return self._run(request, timeout_ms=timeout_ms, on_delta=None)

    def invoke_stream(self, request: ProviderRequest, *, timeout_ms: int,
                      on_delta) -> ProviderResult:
        """Phase 3b (2026-09-02): ConverseStream. Text deltas of the reply's
        `text` field are narrated as they arrive (partial-JSON string
        extraction); the COMPLETE JSON is then parsed and checked exactly
        like the buffered path, so nothing unvalidated is ever returned."""
        return self._run(request, timeout_ms=timeout_ms, on_delta=on_delta)

    def _run(self, request: ProviderRequest, *, timeout_ms: int, on_delta):
        allowed_ids = [item["document_id"] for item in request.citations]
        # B6v2 (Codex serving review): grounding comes from the explicit
        # grounding_text field; a citation with BLANK grounding refuses —
        # a document id without its text produces confidently 'cited'
        # answers that are actually ungrounded. Legacy field names are
        # accepted as fallback but blankness is never silently tolerated.
        def _grounding(item):
            for field in ("grounding_text", "content", "excerpt"):
                value = str(item.get(field) or "").strip()
                if value:
                    return value[:1200]
            raise ProviderError(
                f"citation {item.get('document_id')!r} carries no "
                "grounding text — refusing to send blank grounding")
        citations_block = "\n\n".join(
            f"[{item['document_id']}]\n{_grounding(item)}"
            for item in request.citations)
        import hashlib as _hashlib
        grounding_sha256 = _hashlib.sha256(
            citations_block.encode("utf-8")).hexdigest()
        # Dev (owner instruction 2026-08-28): the assistant is GENERAL
        # PURPOSE — users may say anything, not only medical topics. The
        # grounding contract is unchanged (answer from the supplied
        # citations and always cite at least one), but the medical framing
        # no longer causes the model to decline non-clinical transcripts,
        # which produced an empty cited_document_ids and a hard
        # CITATION_BINDING_INVALID refusal at the gateway.
        if request.citations:
            grounding_rule = (
                "Ground factual claims in the supplied citations. You MUST cite "
                "at least one supplied document id in cited_document_ids, "
                "choosing the most relevant one even if the match is partial.\n"
                "If the citations do not fully answer, say what you can and "
                "note the limit; for health questions suggest consulting a "
                "clinician.\n"
            )
        else:
            # dev ungrounded fallback: retrieval found nothing relevant —
            # answer from general knowledge and cite nothing.
            grounding_rule = (
                "No reference documents matched this request. Answer from "
                "your general knowledge, be helpful and concise, and for "
                "health questions suggest consulting a clinician.\n"
                "cited_document_ids MUST be exactly [].\n"
            )
        system = (
            "You are a careful, helpful assistant for the MedZen platform.\n"
            "The user may ask about ANY topic; engage with whatever they say.\n"
            f"Respond ONLY in {request.response_language}.\n"
            # Phase 1 (2026-09-02): the reply is READ ALOUD by text-to-speech;
            # length drives both LLM and TTS latency.
            "VOICE STYLE: your reply is spoken aloud by text-to-speech. Write 2 "
            "short spoken sentences (about 35-55 words in total) in natural, "
            f"conversational {request.response_language}. No lists, bullet "
            "points, markdown, headings, symbols, emojis or URLs. Say numbers the "
            "way people say them. Give a longer answer ONLY when the user "
            "explicitly asks for more detail.\n"
            "The transcript comes from speech recognition and may contain "
            "recognition errors; infer what the user most likely meant.\n"
            + grounding_rule +
            "Never repeat or invent personal identifiers.\n"
            "Reply with EXACTLY one JSON object, no markdown fences - also in an "
            "ongoing conversation, EVERY reply is one JSON object:\n"
            '{"text": "<your reply>", "cited_document_ids": ["<id>", ...]}\n'
            f"cited_document_ids MUST be a subset of {allowed_ids}."
        )
        user = (
            f"User transcript ({request.language}):\n"
            f"{request.normalized_transcript}\n\n"
            f"Citations:\n{citations_block if citations_block else '(none supplied)'}"
        )
        # Claude 5-family models REFUSE the temperature parameter
        # (Bedrock ValidationException: "`temperature` is deprecated for
        # this model"). Try the historical 0.2 once; if the model rejects
        # it, drop it and remember for the process lifetime.
        # Phase 2: prior turns become real multi-turn messages; the current
        # transcript stays verbatim as the final user message.
        # Prior ASSISTANT turns are replayed in the SAME JSON envelope the
        # model must produce: replayed as plain prose they became a style
        # precedent and multi-turn replies dropped the JSON format (5 of 6
        # observed 2026-09-03), which the strict parser then refused.
        import json as _json
        messages = []
        for role, text in request.history:
            shown = (text if role == "user" else
                     _json.dumps({"text": text, "cited_document_ids": []},
                                 ensure_ascii=False))
            messages.append({"role": role, "content": [{"text": shown}]})
        messages.append({"role": "user", "content": [{"text": user}]})
        config = {"maxTokens": request.maximum_output_tokens}
        if not getattr(self, "_no_temperature", False):
            config["temperature"] = 0.2
        call = (self._converse_stream if on_delta is not None else self._converse)
        import time as _time
        started = _time.monotonic()

        def remaining_ms() -> int:
            elapsed_ms = int((_time.monotonic() - started) * 1000)
            return max(0, timeout_ms - elapsed_ms)

        # Codex review 2026-09-03 (round 2): everything narrated to the
        # client is remembered, so a later retry can never replace text
        # the user has already seen with different text.
        narrated: list[str] = []
        if on_delta is not None:
            _client_delta = on_delta

            def on_delta(fresh: str, _sink=_client_delta) -> None:
                narrated.append(fresh)
                _sink(fresh)
        try:
            raw, stop_reason = call(system, messages, config, timeout_ms, on_delta)
        except Exception as exc:
            # retry decision keys on THIS call's config, not the shared
            # flag - two concurrent first-calls must both self-correct
            if "temperature" not in str(exc) or "temperature" not in config:
                raise
            self._no_temperature = True
            config.pop("temperature", None)
            retry_ms = remaining_ms()
            if retry_ms < MINIMUM_RETRY_MS:
                raise TimeoutError(
                    "LLM provider retry budget exhausted") from exc
            raw, stop_reason = call(
                system, messages, config, retry_ms, on_delta,
                fresh_deadline=True)
        raw = raw.strip()
        if stop_reason == "max_tokens":
            # a truncated reply can never satisfy the JSON contract; name
            # the cause instead of surfacing it as a parse error
            raise RuntimeError(
                "model output truncated at maximum_output_tokens - raise "
                "the policy cap; a cut-off reply cannot be verified")
        try:
            text, cited = self._parse_reply(raw)
        except ValueError as first_error:
            shown = "".join(narrated)
            if narrated:
                # Part of a reply was already spoken to the client: that
                # text IS the answer (ungrounded - the object never
                # validated). A retry could only contradict what was shown.
                if not shown.strip():
                    raise RuntimeError(
                        "bedrock streamed an empty malformed answer") from first_error
                text, cited = shown, ()
            else:
                # Codex review 2026-09-03: Sonnet occasionally answers in
                # plain prose (seen in Kinyarwanda) and the hard refusal
                # surfaced as a 502. Retry ONCE, buffered, inside the SAME
                # deadline as the first call (the policy timeout is the
                # whole budget - two full calls could exceed the
                # orchestrator's leg timeout); if the model still writes
                # prose, the prose IS the answer and it is returned
                # UNGROUNDED - never invented JSON, never an invented citation.
                retry_ms = remaining_ms()
                if retry_ms >= MINIMUM_RETRY_MS:
                    reminder = ("\nYour previous reply was not a JSON object. Reply "
                                "with EXACTLY one JSON object and nothing else: "
                                '{"text": "<your reply>", "cited_document_ids": [...]}')
                    self.last_retry_timeout_ms = retry_ms
                    retry_raw, retry_stop = self._converse(
                        system + reminder, messages, config, retry_ms, None,
                        fresh_deadline=True)
                    retry_raw = retry_raw.strip()
                else:
                    self.last_retry_timeout_ms = 0
                    retry_raw, retry_stop = raw, stop_reason
                try:
                    if retry_stop == "max_tokens":
                        raise ValueError("retry truncated")
                    text, cited = self._parse_reply(retry_raw)
                except ValueError:
                    prose = retry_raw if retry_stop != "max_tokens" else raw
                    if not prose or prose.startswith("{"):
                        raise RuntimeError(
                            "bedrock reply was not the contracted JSON shape: "
                            f"{first_error}") from first_error
                    text, cited = prose, ()
        if narrated and "".join(narrated) != text:
            # Last-resort invariant: the frontend may only finalise the exact
            # text it already rendered.  The anchored extractor above makes
            # this unreachable for contracted replies, but keeping it here
            # prevents future parser changes from reintroducing split-brain
            # stream/final output.
            text, cited = "".join(narrated), ()
        return ProviderResult(
            text=text,
            cited_document_ids=cited,
            citation_binding_sha256=request.citation_binding_sha256,
            model_version=self.model_version,
            grounding_sha256=grounding_sha256,
        )
