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

    # The orchestrator caps each dependency leg at 30s; Fish s2.1-pro-free
    # sometimes needs 24-26s + gateway overhead, so a retryable 503 here is a
    # boundary miss, not an outage. One in-proxy retry rides Fish's variance.
    data = err_out = None
    for attempt in (1, 2, 3):
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
            msg = (err.get("error") or {}).get("message") or str(e)
            code = (err.get("error") or {}).get("code") or "UPSTREAM_ERROR"
            err_out = _resp(502, {"error": f"{code}: {msg}",
                                  "request_id": request_id,
                                  "retryable": (err.get("error") or {}).get("retryable")})
            if not (err.get("error") or {}).get("retryable") or attempt == 3:
                return err_out
        except Exception as e:
            err_out = _resp(504, {"error": f"platform unreachable: {type(e).__name__}",
                                  "request_id": request_id})
            if attempt == 3:
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
