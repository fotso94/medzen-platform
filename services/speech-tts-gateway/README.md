# MedZen speech TTS gateway — B6.5 local slice

This is the new repository-owned `medzen-speech-tts-gateway`. It does not
import, reuse, modify, deploy, or depend on the separately owned gateway.

```text
validated text + model versions
       |
       +-- default policy ----------------------> text_only (HTTP 200)
       |
       +-- injected local fake Fish
              -> content/voice synthesis key
              -> cache hit ---------------------> synthetic audio reference
              -> provider success -> cache -----> synthetic audio reference
              -> timeout/error/open breaker ----> text_only (HTTP 200)
```

Assumptions and trade-offs:

- The application defaults to `text_only`. Fake Fish is dependency-injected
  by local tests; every environment-selected real provider mode refuses
  startup. There are no credentials, SDKs, HTTP clients, or network paths.
- Exact text is returned unchanged on every valid path. Text-only is a
  successful terminal response, so provider failure cannot discard a reply or
  create an HTTP 500 cascade.
- The synthesis key binds the exact UTF-8 text hash, language, provider,
  synthetic voice, provider version, and media type. Request identity is
  intentionally excluded, so retries and duplicate requests share one result.
- Cached audio carries its own SHA-256 and is accepted only when that checksum
  matches the immutable bytes; the request-derived synthesis key and audio
  integrity hash serve different purposes.
- The process-local cache serializes cache creation. That is simple and proves
  concurrent idempotency locally, but B6.6 must replace it with content-addressed
  S3 storage plus cross-pod coordination before deployment.
- A cached result remains available when the Fish breaker is open. Readiness
  stays healthy because text-only still works, while the open provider state is
  exposed explicitly.
- Self-hosted TTS is absent. It remains blocked until a voice/model and consent
  evidence are approved through the registry.

Test pyramid:

- Unit: canonical hashes, cache/collision behavior, concurrent idempotency,
  breaker transitions, malformed-provider refusal.
- Integration: HTTP text-only success, fake Fish success/cache hit, timeout,
  provider error, open-breaker degradation, payload validation and safe logs.
- Contract/regression: JSON Schema fixtures, parent contract hash, no external
  gateway dependency, exact pinned requirements, canonical repository suite.

Revisit for B6.6 and later: durable S3 cache, per-key distributed locking,
real Fish authentication and request cancellation, audio format validation,
streamed audio chunks, registry-approved voice routing, cache lifecycle, and
provider cost/latency telemetry.
