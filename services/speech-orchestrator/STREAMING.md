# B6.4 local streaming design

The B6.4 entry point composes the immutable B6.3 file-mode app with a local
WebSocket route. B6.3-bound source files are not edited.

```text
WebSocket upgrade
  -> auth + contract header -> start/session identity -> registry route
  -> PCM frames -> VAD interface -> partial queue (4, drop oldest)
  -> end_of_speech -> guarded local pipeline
       -> final transcript -> cited reply -> completed
       -> persist complete final batch before first send

cancel / barge_in -> cancellation token + bounded pipeline hook -> cancelled
audio output      -> queue (8, pause upstream; exercised before B6.5 TTS)
final results     -> queue (16, atomic refusal; never drop)
```

Assumptions and trade-offs:

- The local runtime uses a deterministic energy detector over the generated
  non-speech fixture. A `SileroVADAdapter` exists behind the same interface,
  but no Silero weights are downloaded or presented as validated in B6.4.
- The completed event batch is retained in a process-local store before
  delivery. This proves slow-client and disconnect behavior without adding a
  database; B6.6 must select a durable session store before production.
- Cancellation is cooperative and tested with a controllable local pipeline.
  Deployed ASR/LLM/TTS clients must implement the same bounded cancellation
  hook and will be re-tested during B6.6 integration.
- Audio-output backpressure is implemented now although B6.4 emits no TTS
  chunks. B6.5 can attach its producer without changing the queue policy.

Test pyramid:

- Unit: VAD adapters, state transitions, queue policies, final persistence,
  timeout and breaker behavior.
- Integration: complete WebSocket turn, invalid ordering, payload limits,
  cancellation/barge-in timing, idle timeout and clean disconnect.
- Regression: canonical suite, immutable B6.3 hashes, pinned multipart stack,
  generated registries and infrastructure validation.

Revisit as the system grows: real Silero model loading and threshold evidence,
durable session/final-result storage, distributed cancellation across service
clients, multi-turn barge-in semantics, and load tests over real network
buffers.
