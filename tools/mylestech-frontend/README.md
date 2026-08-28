# MylestechSpeechVoice — dev test frontend

Single-Lambda web app that exercises the full MedZen dev chain
(ASR -> RAG -> LLM -> TTS) from a browser: pick one of five languages
(en / fr / kin / pcm / swa), record from the microphone (client-side
16 kHz mono WAV), and get back the transcript, the grounded answer, and
the synthesized voice for exactly that answer text (the platform binds
audio to the answer by content hash).

## Layout
- `lambda_function.py` — serves the page (GET /), proxies POST /api/speech
  to the orchestrator's public contract, and returns the MP3 base64.
  The client bearer token lives ONLY in the Lambda environment.
- `index.html` — the page template. `__LOGO__`/`__FAVICON__` are replaced
  at package time with a data URI (owner logo, or an SVG monogram fallback).

## Deployed dev resources (eu-central-1, account 558069890522)
- Lambda `mylestech-speechvoice` (python3.12, 512MB, 120s, in-VPC:
  the three medzen subnets, SG `mylestech-proxy-sg`), public Function URL.
- Env: `MEDZEN_ORCH_URL` (internal CLB below), `MEDZEN_CLIENT_TOKEN`
  (client_id `mylestech-frontend` in `medzen/client-api-keys`).
- `platform/k8s/dev-b6v2/04-orchestrator-internal-lb.yaml` — INTERNAL
  classic ELB in front of the orchestrator (legacy in-tree provisioning;
  the ALB controller stays gated off). Internet -> Lambda URL only.
- The logs VPC-endpoint SG admits `mylestech-proxy-sg` on 443 so the
  in-VPC function can write CloudWatch logs.

## Package + deploy
    python3 - <<'EOF'   # substitute the logo, then zip lambda+index
    # (see session evidence B6-112; any data-URI image works)
    EOF
    aws lambda update-function-code --function-name mylestech-speechvoice \
      --zip-file fileb://app.zip

## Limits
- Recording capped at 30 s client-side; Function URL payload cap 6 MB.
- Fish `s2.1-pro-free` latency is ~20-25 s per fresh synthesis (evening,
  2026-08-28); dev runs `MEDZEN_FISH_TIMEOUT_MS=25000`. Cache hits are
  instant. Promo model expires 2026-08-31.
