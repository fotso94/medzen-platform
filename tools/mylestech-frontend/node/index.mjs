// MylestechSpeechVoice proxy — Node.js response-streaming edition (Phase 3).
// Same URL, same VPC, same env (MEDZEN_ORCH_URL, MEDZEN_CLIENT_TOKEN).
// Routes:
//   GET  /                  the single-page app
//   GET  /api/health        orchestrator readiness
//   POST /api/speech        buffered: full result in one JSON (fallback)
//   POST /api/speech-stream NDJSON stage events relayed as they happen
//   POST /api/telemetry     browser-side timing marks (logged)
import { readFileSync } from "node:fs";
import { randomUUID } from "node:crypto";

const ORCH = (process.env.MEDZEN_ORCH_URL || "").replace(/\/$/, "");
const TOKEN = process.env.MEDZEN_CLIENT_TOKEN || "";
const CONTRACT = "medzen.speech.v2";
const INDEX_HTML = readFileSync(new URL("./index.html", import.meta.url));
const MAX_AUDIO_B64 = 5_500_000;

const log = (o) => console.log(JSON.stringify(o));

function multipart(audio, language, requestId, history) {
  const b = randomUUID().replace(/-/g, "");
  const parts = [];
  const part = (name, value, filename, ctype) => {
    let h = `--${b}\r\nContent-Disposition: form-data; name="${name}"`;
    if (filename) h += `; filename="${filename}"`;
    h += "\r\n";
    if (ctype) h += `Content-Type: ${ctype}\r\n`;
    parts.push(Buffer.from(h + "\r\n"), Buffer.isBuffer(value) ? value : Buffer.from(value), Buffer.from("\r\n"));
  };
  part("audio", audio, "speech.wav", "audio/wav");
  part("language_hint", language);
  part("response_audio", "true");
  part("request_id", requestId);
  if (history && history.length) part("history", JSON.stringify(history));
  parts.push(Buffer.from(`--${b}--\r\n`));
  return { body: Buffer.concat(parts), ctype: `multipart/form-data; boundary=${b}` };
}

function parseInput(event) {
  let body = event.body || "";
  if (event.isBase64Encoded) body = Buffer.from(body, "base64").toString();
  const p = JSON.parse(body || "{}");
  const language = String(p.language || "").trim();
  if (!/^[a-z]{2,3}$/.test(language)) return { error: "language must be a 2-3 letter code" };
  const b64 = p.audio_b64 || "";
  if (!b64 || b64.length > MAX_AUDIO_B64) return { error: "audio_b64 missing or too large (max ~30s)" };
  const audio = Buffer.from(b64, "base64");
  if (audio.subarray(0, 4).toString() !== "RIFF") return { error: "audio must be a WAV recording" };
  const history = Array.isArray(p.history) ? p.history : [];
  if (history.length > 8 || history.some(t => !t || typeof t !== "object" || Object.keys(t).sort().join() !== "role,text"
      || typeof t.text !== "string" || t.text.length > 1000)) return { error: "history must be <= 8 {role,text} turns" };
  return { language, audio, history };
}

async function fetchMp3(url) {
  try {
    const r = await fetch(url, { signal: AbortSignal.timeout(25000) });
    if (!r.ok) return null;
    return Buffer.from(await r.arrayBuffer()).toString("base64");
  } catch { return null; }
}

function summarize(data) {
  const reply = data.reply || {}, mv = data.model_versions || {};
  return {
    request_id: data.request_id, language: data.language,
    transcript: (data.transcript || {}).normalized || "",
    answer: reply.text || "", citations: (reply.citations || []).length,
    grounded: !!(reply.citations || []).length,
    asr_model: mv.asr, llm_model: mv.llm, tts_model: mv.tts,
    latency_ms: data.latency_ms, audio_url: reply.audio_url || null, audio_b64: null,
  };
}

function upstreamError(status, errBody, requestId) {
  let err = {}; try { err = JSON.parse(errBody || "{}"); } catch {}
  const d = err.error || {};
  return { status: status >= 400 && status < 500 ? status : 502,
           body: { error: `${d.code || "UPSTREAM_ERROR"}: ${d.message || "upstream failure"}`,
                   request_id: requestId, retryable: !!d.retryable && status >= 500 } };
}

async function buffered(input, out) {
  const t0 = Date.now();
  let last = null;
  for (const attempt of [1, 2]) {   // one scoped retry (transport or retryable 5xx)
    const requestId = randomUUID();
    const { body, ctype } = multipart(input.audio, input.language, requestId, input.history);
    let r;
    try {
      r = await fetch(`${ORCH}/v1/conversations/speech`, { method: "POST", body,
        headers: { "Content-Type": ctype, Authorization: `Bearer ${TOKEN}`, "X-MedZen-Contract-Version": CONTRACT },
        signal: AbortSignal.timeout(100000) });
    } catch (e) {
      last = { status: 504, body: { error: `platform unreachable: ${e.name}`, request_id: requestId } };
      if (attempt === 2) return out(last.status, last.body); continue;
    }
    if (!r.ok) {
      last = upstreamError(r.status, await r.text(), requestId);
      if (!last.body.retryable || attempt === 2) return out(last.status, last.body); continue;
    }
    const data = await r.json();
    const s = summarize(data);
    if (s.audio_url) s.audio_b64 = await fetchMp3(s.audio_url);
    const upstream = Number((data.latency_ms || {}).total || 0);
    s.proxy_overhead_ms = Math.round(Date.now() - t0 - upstream);
    log({ timing: { request_id: s.request_id, language: input.language, upstream_ms: data.latency_ms,
                    proxy_overhead_ms: s.proxy_overhead_ms, mode: "buffered" } });
    return out(200, s);
  }
  return out(last.status, last.body);
}

async function streamed(input, stream) {
  // NDJSON relay: each upstream stage line is forwarded the moment it lands.
  const t0 = Date.now();
  const requestId = randomUUID();
  const write = (o) => stream.write(JSON.stringify(o) + "\n");
  const { body, ctype } = multipart(input.audio, input.language, requestId, input.history);
  let r;
  try {
    r = await fetch(`${ORCH}/v1/conversations/speech`, { method: "POST", body,
      headers: { "Content-Type": ctype, Accept: "application/x-ndjson", Authorization: `Bearer ${TOKEN}`,
                 "X-MedZen-Contract-Version": CONTRACT }, signal: AbortSignal.timeout(100000) });
  } catch (e) {
    write({ event: "error", status: 504, error: `platform unreachable: ${e.name}`, request_id: requestId }); return;
  }
  if (!r.ok) { const u = upstreamError(r.status, await r.text(), requestId); write({ event: "error", status: u.status, ...u.body }); return; }
  const dec = new TextDecoder(); let buf = "";
  let firstAt = null, final = null;
  for await (const chunk of r.body) {
    buf += dec.decode(chunk, { stream: true });
    let nl;
    while ((nl = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, nl).trim(); buf = buf.slice(nl + 1);
      if (!line) continue;
      let ev; try { ev = JSON.parse(line); } catch { continue; }
      if (firstAt === null) firstAt = Date.now();
      if (ev.event === "audio_ready" && ev.audio_url) {
        ev.audio_b64 = await fetchMp3(ev.audio_url);   // same-origin bytes: WebAudio autoplay path, no S3 CORS
        write(ev);
      } else if (ev.event === "final") {
        final = ev;
        const s = summarize(ev);
        s.audio_b64 = null;             // already delivered on audio_ready
        write({ event: "final", ...s, proxy_first_event_ms: firstAt - t0,
                proxy_overhead_ms: Math.round(Date.now() - t0 - Number((ev.latency_ms || {}).total || 0)) });
      } else {
        write(ev);
      }
    }
  }
  log({ timing: { request_id: requestId, language: input.language, mode: "stream",
                  first_event_ms: firstAt === null ? null : firstAt - t0, total_ms: Date.now() - t0,
                  upstream_ms: final ? final.latency_ms : null } });
}

export const handler = awslambda.streamifyResponse(async (event, responseStream) => {
  const http = (event.requestContext || {}).http || {};
  const method = http.method || "GET", path = event.rawPath || "/";
  log({ access: { method, path, ip: http.sourceIp, ua: (http.userAgent || "").slice(0, 160) } });
  const send = (status, body, ctype = "application/json") => {
    const s = awslambda.HttpResponseStream.from(responseStream, { statusCode: status,
      headers: { "Content-Type": ctype, "Cache-Control": "no-store" } });
    s.write(typeof body === "string" ? body : JSON.stringify(body)); s.end();
  };
  try {
    if (method === "GET" && (path === "/" || path === "/index.html")) return send(200, INDEX_HTML.toString(), "text/html; charset=utf-8");
    if (method === "GET" && path === "/api/health") {
      try { const r = await fetch(`${ORCH}/readyz`, { signal: AbortSignal.timeout(8000) }); return send(r.status, await r.text()); }
      catch (e) { return send(504, { ready: false, error: e.name }); }
    }
    if (method === "POST" && path === "/api/telemetry") {
      try { let b = event.body || ""; if (event.isBase64Encoded) b = Buffer.from(b, "base64").toString();
            const t = JSON.parse(b || "{}");
            log({ client_timing: { request_id: String(t.request_id || "").slice(0, 64), language: String(t.language || "").slice(0, 8),
                  stop_to_transcript_ms: t.stop_to_transcript_ms ?? null, stop_to_first_word_ms: t.stop_to_first_word_ms ?? null, stop_to_text_ms: t.stop_to_text_ms ?? null,
                  stop_to_response_ms: t.stop_to_response_ms ?? null, stop_to_audio_ms: t.stop_to_audio_ms ?? null,
                  mode: String(t.mode || "").slice(0, 12), ok: !!t.ok } }); } catch {}
      return send(204, "");
    }
    if (method === "POST" && (path === "/api/speech" || path === "/api/speech-stream")) {
      let input; try { input = parseInput(event); } catch { input = { error: "invalid JSON body" }; }
      if (input.error) return send(400, { error: input.error });
      if (path === "/api/speech") return await buffered(input, send);
      const s = awslambda.HttpResponseStream.from(responseStream, { statusCode: 200,
        headers: { "Content-Type": "application/x-ndjson", "Cache-Control": "no-store", "X-Accel-Buffering": "no" } });
      try { await streamed(input, s); } finally { s.end(); }
      return;
    }
    return send(404, { error: "not found" });
  } catch (e) {
    log({ proxy_error: e.name, message: String(e.message || "").slice(0, 200) });
    try { send(500, { error: "proxy failure" }); } catch {}
  }
});
