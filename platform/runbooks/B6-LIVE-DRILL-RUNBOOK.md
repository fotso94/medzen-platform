# B6 live failure drills (task F — B6 row 4; execute only on a live deploy)

The live twins of the committed local drill suite
(tests/test_b6_7_drills.py). Every drill's exit condition: degradation
exactly per §A6, NO 500 cascade, and the orchestrator keeps serving
other requests throughout. Run against a staging window, never during
production traffic, with the on-call able to `kubectl rollout undo`.

## Drill 1 — kill Fish (TTS provider death)
Action: scale the Fish egress to nothing (or blackhole its upstream in
the tts-gateway config) mid-traffic; send 5 speech turns.
EXPECT: every response still carries the full reply text with
tts_backend=text_only and a documented degradation_reason
(FISH_UNAVAILABLE/FISH_TIMEOUT); zero 5xx from the orchestrator.
RESTORE: revert the egress; confirm tts_backend returns to fish.

## Drill 2 — kill the ASR pod mid-stream
Action: open a streaming session, send audio, then
`kubectl -n medzen delete pod -l app=asr-runtime` while streaming.
EXPECT: the open session ends with a clean contract error
(DEPENDENCY_UNAVAILABLE, retryable=true, close code 4503) — never a
hang; internal exception text never reaches the client; new sessions
succeed once the replacement pod is Ready (startupProbe window).

## Drill 3 — open the LLM breaker
Action: point llm-gateway at a blackholed Bedrock endpoint (or inject
failures) until the breaker opens (5 failures / 30s window).
EXPECT: turns fail with a controlled retryable 503
(DEPENDENCY_UNAVAILABLE) and NO invented clinical text; breaker state
visible in /readyz; recovery via one half-open probe after 15s once
the endpoint is restored.

## Recording
Each drill appends its observations (timestamps, request ids, observed
codes) to platform/evidence/B6-LIVE-DRILL-EVIDENCE-<date>.md, committed
with the drill review — the same evidence discipline as everything else.
