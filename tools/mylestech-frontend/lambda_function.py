"""MylestechSpeechVoice — dev test frontend for the MedZen speech platform.

One Lambda serves both:
  GET  /            -> the single-page app (index.html, packaged in the zip)
  GET  /api/health  -> proxies the orchestrator's /readyz (no auth needed)
  POST /api/speech  -> {language, audio_b64(WAV)} -> multipart to the
                       orchestrator's /v1/conversations/speech with the
                       client bearer token, which lives ONLY in this
                       function's environment — the browser never sees it.

The function runs inside the MedZen VPC and reaches the orchestrator through
an INTERNAL classic ELB (speech-orchestrator-lb). The synthesized MP3 is
fetched server-side via its presigned URL (S3 gateway endpoint) and returned
base64 so the page needs no S3 CORS and no cross-origin fetches.
"""
import base64
import json
import time
import os
import urllib.error
import urllib.request
import uuid

ORCH = os.environ["MEDZEN_ORCH_URL"].rstrip("/")
TOKEN = os.environ["MEDZEN_CLIENT_TOKEN"]
CONTRACT = "medzen.speech.v2"
ALLOWED = {"en", "fr", "kin", "pcm", "swa"}
MAX_B64 = 5_500_000  # stay under the 6MB Function URL payload cap

with open(os.path.join(os.path.dirname(__file__), "index.html"), "rb") as fh:
    INDEX_HTML = fh.read()


def _resp(status, body, ctype="application/json", extra=None):
    headers = {"Content-Type": ctype, "Cache-Control": "no-store"}
    if extra:
        headers.update(extra)
    if isinstance(body, (dict, list)):
        body = json.dumps(body)
    return {"statusCode": status, "headers": headers, "body": body}


def _multipart(audio, language, request_id):
    b = uuid.uuid4().hex
    body = b""

    def part(name, value, filename=None, ctype=None):
        h = f'--{b}\r\nContent-Disposition: form-data; name="{name}"'
        if filename:
            h += f'; filename="{filename}"'
        h += "\r\n"
        if ctype:
            h += f"Content-Type: {ctype}\r\n"
        return h.encode() + b"\r\n" + value + b"\r\n"

    body += part("audio", audio, "speech.wav", "audio/wav")
    body += part("language_hint", language.encode())
    body += part("response_audio", b"true")
    body += part("request_id", request_id.encode())
    body += f"--{b}--\r\n".encode()
    return body, f"multipart/form-data; boundary={b}"


def _speech(payload):
    language = str(payload.get("language", "")).strip()
    if language not in ALLOWED:
        return _resp(400, {"error": f"language must be one of {sorted(ALLOWED)}"})
    b64 = payload.get("audio_b64") or ""
    if not b64 or len(b64) > MAX_B64:
        return _resp(400, {"error": "audio_b64 missing or too large (max ~30s)"})
    try:
        audio = base64.b64decode(b64, validate=True)
    except Exception:
        return _resp(400, {"error": "audio_b64 is not valid base64"})
    if audio[:4] != b"RIFF":
        return _resp(400, {"error": "audio must be a WAV recording"})

    # Phase 1 (2026-09-02): ONE scoped retry. Every attempt re-runs the whole
    # ASR->RAG->LLM->TTS chain, so unbounded retries tripled time-to-failure
    # and could outlive the Lambda timeout. Retry only a transport failure or
    # an explicitly retryable upstream refusal; never a 4xx / non-retryable.
    t_proxy = time.time()
    data = err_out = None
    for attempt in (1, 2):
        request_id = str(uuid.uuid4())
        body, ctype = _multipart(audio, language, request_id)
        req = urllib.request.Request(
            f"{ORCH}/v1/conversations/speech", data=body, method="POST",
            headers={"Content-Type": ctype,
                     "Authorization": f"Bearer {TOKEN}",
                     "X-MedZen-Contract-Version": CONTRACT})
        try:
            with urllib.request.urlopen(req, timeout=100) as r:
                data = json.loads(r.read())
            break
        except urllib.error.HTTPError as e:
            try:
                err = json.loads(e.read() or b"{}")
            except Exception:
                err = {}
            detail = err.get("error") or {}
            msg = detail.get("message") or str(e)
            code = detail.get("code") or "UPSTREAM_ERROR"
            retryable = bool(detail.get("retryable")) and e.code >= 500
            err_out = _resp(502, {"error": f"{code}: {msg}",
                                  "request_id": request_id,
                                  "retryable": retryable})
            if not retryable or attempt == 2:
                return err_out
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            err_out = _resp(504, {"error": f"platform unreachable: {type(e).__name__}",
                                  "request_id": request_id})
            if attempt == 2:
                return err_out
    if data is None:
        return err_out

    reply = data.get("reply") or {}
    mv = data.get("model_versions") or {}
    out = {
        "request_id": data.get("request_id"),
        "language": data.get("language"),
        "transcript": (data.get("transcript") or {}).get("normalized", ""),
        "answer": reply.get("text", ""),
        "citations": len(reply.get("citations") or []),
        "grounded": bool(reply.get("citations")),
        "asr_model": mv.get("asr"),
        "llm_model": mv.get("llm"),
        "tts_model": mv.get("tts"),
        "latency_ms": data.get("latency_ms"),
        "proxy_overhead_ms": None,
        "audio_b64": None,
        "audio_url": reply.get("audio_url"),
    }
    url = reply.get("audio_url")
    if url:
        try:  # presigned GET; the S3 gateway endpoint carries it, no IAM needed
            with urllib.request.urlopen(url, timeout=25) as r:
                mp3 = r.read()
            if mp3:
                out["audio_b64"] = base64.b64encode(mp3).decode()
        except Exception as e:  # audio degrades gracefully to the URL/text
            out["audio_error"] = type(e).__name__
    upstream = float(((data.get("latency_ms") or {}).get("total")) or 0.0)
    out["proxy_overhead_ms"] = round((time.time() - t_proxy) * 1000.0 - upstream, 1)
    print(json.dumps({"timing": {"request_id": out["request_id"], "language": language,
                                 "upstream_ms": data.get("latency_ms"),
                                 "proxy_overhead_ms": out["proxy_overhead_ms"],
                                 "mp3_bytes": len(out["audio_b64"] or "") * 3 // 4}}))
    return _resp(200, out)


def _health():
    try:
        with urllib.request.urlopen(f"{ORCH}/readyz", timeout=10) as r:
            return _resp(200, r.read().decode())
    except urllib.error.HTTPError as e:
        return _resp(e.code, e.read().decode()[:400])
    except Exception as e:
        return _resp(504, {"error": f"orchestrator unreachable: {type(e).__name__}"})


def lambda_handler(event, _ctx):
    http = (event.get("requestContext", {}).get("http", {}) or {})
    method = http.get("method", "GET")
    path = event.get("rawPath") or "/"
    # access line: who is testing, from where, on what device (owner ask
    # 2026-08-29). Function URLs have no built-in access log; this is it.
    print(json.dumps({"access": {"method": method, "path": path,
                                 "ip": http.get("sourceIp"),
                                 "ua": (http.get("userAgent") or "")[:160]}}))
    if method == "GET" and path in ("/", "/index.html"):
        return _resp(200, INDEX_HTML.decode(), "text/html; charset=utf-8")
    if method == "POST" and path == "/api/telemetry":
        # Phase 1: browser-side marks (stop->response, stop->audio playing)
        try:
            body = event.get("body") or ""
            if event.get("isBase64Encoded"):
                body = base64.b64decode(body).decode()
            t = json.loads(body or "{}")
            print(json.dumps({"client_timing": {
                "request_id": str(t.get("request_id", ""))[:64],
                "language": str(t.get("language", ""))[:8],
                "stop_to_response_ms": t.get("stop_to_response_ms"),
                "stop_to_audio_ms": t.get("stop_to_audio_ms"),
                "ok": bool(t.get("ok"))}}))
        except Exception:
            pass
        return _resp(204, "")
    if method == "GET" and path == "/api/health":
        return _health()
    if method == "POST" and path == "/api/speech":
        raw = event.get("body") or ""
        if event.get("isBase64Encoded"):
            raw = base64.b64decode(raw).decode()
        try:
            payload = json.loads(raw)
        except Exception:
            return _resp(400, {"error": "body must be JSON"})
        return _speech(payload)
    return _resp(404, {"error": "not found"})
