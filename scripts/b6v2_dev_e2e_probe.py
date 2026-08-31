#!/usr/bin/env python3
"""Multilingual dev E2E probe: ASR -> RAG -> LLM -> TTS through the
orchestrator's public contract. Verifies Arm-1 ASR identity, matching-language
RAG grounding, real Bedrock output, the registry-bound Fish voice/model, and a
downloadable non-empty MP3."""
import json, sys, urllib.request, uuid, hashlib
BASE = "http://127.0.0.1:18080"
TREE = "34ca18bab2f7c6f34e67c0598db416438f0bada15ab004f0b58e3dbafa3c0ca6"
ASR_V = f"omniasr_ctc_1b:{TREE[:12]}"
LLM_V = "bedrock:eu.anthropic.claude-sonnet-5"
token = open(sys.argv[1]).read().strip()
clips = json.load(open(sys.argv[2]))

def post(path, lang_hint, audio_path, response_audio):
    b = uuid.uuid4().hex
    body = b""
    def part(name, value, filename=None, ctype=None):
        h = f'--{b}\r\nContent-Disposition: form-data; name="{name}"'
        if filename: h += f'; filename="{filename}"'
        h += "\r\n"
        if ctype: h += f"Content-Type: {ctype}\r\n"
        return h.encode() + b"\r\n" + value + b"\r\n"
    body += part("audio", open(audio_path,"rb").read(), "a.wav", "audio/wav")
    if lang_hint: body += part("language_hint", lang_hint.encode())
    body += part("response_audio", (b"true" if response_audio else b"false"))
    body += f"--{b}--\r\n".encode()
    req = urllib.request.Request(BASE+path, data=body, method="POST", headers={
        "Content-Type": f"multipart/form-data; boundary={b}",
        "Content-Length": str(len(body)),
        "Authorization": f"Bearer {token}",
        "X-MedZen-Contract-Version": "medzen.speech.v2"})
    try:
        r = urllib.request.urlopen(req, timeout=180)
        return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")

def check(lang, iso, want_audio, expect_fish):
    c = clips[lang]
    st, resp = post("/v1/conversations/speech", iso, c["path"], want_audio)
    if st != 200:
        return False, f"HTTP {st} {json.dumps(resp)[:220]}"
    fails = []
    mv = resp.get("model_versions", {})
    if mv.get("asr") != ASR_V: fails.append(f"asr id {mv.get('asr')}")
    if mv.get("llm") != LLM_V: fails.append(f"llm id {mv.get('llm')}")
    tr = (resp.get("transcript") or {}).get("normalized", "")
    if not tr.strip(): fails.append("empty transcript")
    reply = resp.get("reply") or {}
    cits = reply.get("citations") or []
    if not cits: fails.append("no RAG citations")
    else:
        DL = json.load(open(sys.argv[2].replace("clips.json","doclang.json")))
        bad = [x.get("document_id") for x in cits
               if DL.get(x.get("document_id")) != iso]
        if bad: fails.append(f"citations not in {iso}: {bad}")
    if not (reply.get("text") or "").strip(): fails.append("empty LLM reply")
    backend, url = reply.get("tts_backend"), reply.get("audio_url")
    if want_audio and expect_fish:
        if backend != "fish": fails.append(f"tts_backend={backend}")
        if mv.get("tts") != "fish:s2.1-pro-free": fails.append(f"tts model {mv.get('tts')}")
        if not url: fails.append("no audio_url")
        else:
            try:
                mp3 = urllib.request.urlopen(url, timeout=60).read()
                if len(mp3) < 1000: fails.append(f"mp3 too small {len(mp3)}B")
                else: resp["_mp3_bytes"] = len(mp3)
            except Exception as e: fails.append(f"mp3 fetch {type(e).__name__}")
    if not want_audio:
        if url is not None: fails.append("audio_url present with response_audio=false")
        if backend != "text_only": fails.append(f"backend {backend} != text_only")
    return (not fails), (json.dumps({
        "transcript": tr[:60], "citations": len(cits),
        "reply": (reply.get("text") or "")[:60], "tts": backend,
        "tts_model": mv.get("tts"), "mp3B": resp.get("_mp3_bytes"),
        "latency_ms": (resp.get("latency_ms") or {}).get("total")}) if not fails
        else "; ".join(fails))

if __name__ == "__main__":
    lang, iso, wa, fish = sys.argv[3], sys.argv[4], sys.argv[5]=="true", sys.argv[6]=="true"
    ok, detail = check(lang, iso, wa, fish)
    print(("PASS " if ok else "FAIL ") + f"{lang}/{iso} audio={wa}: {detail}")
    sys.exit(0 if ok else 1)
