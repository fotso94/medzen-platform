#!/usr/bin/env python3
"""Local-only human review of flagged rows, in a native window.

WHY A NATIVE WINDOW AND NOT A TERMINAL
--------------------------------------
An earlier version printed transcripts to stdout. stdout is captured by whatever
launched the process -- including an automation agent -- so "shown only in the
terminal" was not true. Transcript text now goes ONLY into a Tk widget owned by
the local windowing system. It is never written to stdout, stderr, a file, an
argument, an environment variable or a log. The terminal shows checksums,
counts and prompts; never content.

The tool refuses to run at all when it cannot be sure a human is present at a
local display: no TTY, an SSH session, or an automation environment all abort
before any content is fetched.

LISTENING IS MANDATORY
----------------------
A z-score says a row is unusual; only listening says whether the transcript
matches the audio. If playback is unavailable or fails, the row CANNOT be
classified -- the tool refuses rather than letting someone judge from metrics.

AUDIO HANDLING, STATED ACCURATELY
---------------------------------
Playback prefers true in-memory output via sounddevice when installed. When it
is not, the fallback writes a temporary file created with O_EXCL at mode 0600
(owner-only), plays it, and unlinks it in a finally block. Its path is never
printed or logged. This is a real file on disk for the duration of playback --
not "memory only" -- and saying otherwise would be false.

    python scripts/review_labels.py --reviewer-role data-steward
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import review_bindings as RB  # noqa: E402

BUCKET = "medzen-speech"
PROFILE = "medzen"
REGION = "eu-central-1"
ROOT = Path(__file__).resolve().parent.parent
DECISION = ROOT / "platform/decisions/DQ-2026-001-label-review.json"

ACTION_FOR = {
    "confirmed_data_defect": "exclude",
    "valid_but_decoder_incompatible": "defer_for_segmentation",
    "valid_under_limit": "retain",
    "uncertain": "defer_pending_review",
}
# Compatibility runs BOTH ways. An over-limit row cannot be "valid under limit";
# an under-limit row cannot be "decoder incompatible" -- the decoder accepts it.
CLASSES_FOR_TRIGGER = {
    "over_decoder_limit": ["confirmed_data_defect", "valid_but_decoder_incompatible",
                           "uncertain"],
    "extreme_token_rate_under_limit": ["confirmed_data_defect", "valid_under_limit",
                                       "uncertain"],
}
REASON_CODES = {
    "a": "transcript does not match audio",
    "b": "audio truncated or clipped",
    "c": "transcript covers more than this clip",
    "d": "correct but exceeds decoder limit for this script",
    "e": "correct and within limit; unusual but valid",
    "f": "cannot determine from listening",
}
# A classification must be supported by a reason of the matching kind. Without
# this, "confirmed defect / correct and valid" would be recordable.
REASON_FOR = {
    "confirmed_data_defect": {"a", "b", "c"},
    "valid_but_decoder_incompatible": {"d"},
    "valid_under_limit": {"e"},
    "uncertain": {"f"},
}

# An agent terminal can be a REAL tty, so the interactivity check alone does not
# catch one. These markers are the only reliable signal that a process is being
# driven by automation rather than a person, and a miss here means transcripts
# rendered where an agent can read them.
AGENT_ENV = (
    # Claude Code
    "CLAUDECODE", "CLAUDE_CODE", "ANTHROPIC_API_KEY",
    # Codex — an interactive Codex terminal HAS a tty and would otherwise pass
    "CODEX_THREAD_ID", "CODEX_CI", "CODEX_SANDBOX",
    # generic CI
    "CI", "GITHUB_ACTIONS", "BUILDKITE", "JENKINS_URL", "TEAMCITY_VERSION",
)


def refuse_unless_local_human() -> None:
    """Abort before any content is fetched unless a human is at a local display."""
    reasons = []
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        reasons.append("not an interactive terminal")
    if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"):
        reasons.append("remote session (SSH)")
    present = [v for v in AGENT_ENV if os.environ.get(v)]
    if present:
        reasons.append(f"automation environment ({', '.join(present)})")
    if sys.platform == "linux" and not (os.environ.get("DISPLAY")
                                        or os.environ.get("WAYLAND_DISPLAY")):
        reasons.append("no local display")
    if reasons:
        raise SystemExit(
            "REFUSING to start review: " + "; ".join(reasons) + ".\n"
            "  This tool shows consented speech transcripts and must run only "
            "where\n  a human is present at a local display. It has fetched "
            "nothing.")
    try:
        import tkinter  # noqa: F401
    except Exception as e:
        raise SystemExit(f"REFUSING: no local windowing available ({e}). "
                         "Transcripts are never rendered to a terminal.")


def client():
    import boto3
    return boto3.Session(profile_name=PROFILE, region_name=REGION).client("s3")


def play_audio(data: bytes) -> bool:
    """Play from memory if possible, else a 0600 temp file. True only on success."""
    try:
        import io as _io
        import soundfile as _sf
        import sounddevice as _sd
        arr, rate = _sf.read(_io.BytesIO(data), dtype="float32")
        _sd.play(arr, rate)
        _sd.wait()
        return True
    except Exception:
        pass
    path = None
    try:
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        os.chmod(path, 0o600)
        with open(path, "wb") as f:
            f.write(data)
        import subprocess
        for cmd in (["afplay", path], ["aplay", "-q", path], ["play", "-q", path]):
            try:
                subprocess.run(cmd, check=True, capture_output=True, timeout=180)
                return True
            except FileNotFoundError:
                continue
            except Exception:
                return False
        return False
    except Exception:
        return False
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


def review_window(text: str, header: str, allowed_classes: dict,
                  play_fn) -> dict | None:
    """ONE window: transcript, playback, classification and reason together.

    An earlier version played the audio and only then opened a separate window,
    so the reviewer could never hear the clip while reading the transcript --
    which is the entire task. Everything now lives in one window, and Submit
    stays disabled until playback has actually succeeded at least once, so a
    classification cannot be recorded from reading alone.

    Returns {"classification","action","reason_code"} or None for skip/quit.
    """
    import tkinter as tk

    state: dict = {"played": False, "result": None, "quit": False}
    win = tk.Tk()
    win.title("MedZen review — local only")

    tk.Label(win, text=header, font=("TkDefaultFont", 11, "bold"),
             justify="left").pack(anchor="w", padx=12, pady=(12, 4))

    box = tk.Text(win, wrap="word", width=92, height=14)
    box.insert("1.0", text)
    box.configure(state="disabled")
    box.pack(padx=12, pady=6)

    status = tk.Label(win, text="Listen before classifying — Submit is disabled "
                                "until playback succeeds.", fg="#a33")
    status.pack(anchor="w", padx=12)

    cls_var = tk.StringVar(value="")
    reason_var = tk.StringVar(value="")

    reason_frame = tk.LabelFrame(win, text="reason")
    submit = tk.Button(win, text="Submit", state="disabled")

    def refresh_submit(*_):
        ok = state["played"] and cls_var.get() and reason_var.get()
        submit.configure(state="normal" if ok else "disabled")

    def on_class(*_):
        for w in reason_frame.winfo_children():
            w.destroy()
        reason_var.set("")
        for code in sorted(allowed_classes[cls_var.get()]["reasons"]):
            tk.Radiobutton(reason_frame, text=f"{code}. {REASON_CODES[code]}",
                           variable=reason_var, value=code,
                           command=refresh_submit).pack(anchor="w")
        refresh_submit()

    cf = tk.LabelFrame(win, text="classification")
    for cls, meta in allowed_classes.items():
        tk.Radiobutton(cf, text=f"{cls}  ->  {meta['action']}", variable=cls_var,
                       value=cls, command=on_class).pack(anchor="w")
    cf.pack(fill="x", padx=12, pady=(8, 4))
    reason_frame.pack(fill="x", padx=12, pady=4)

    def on_play():
        status.configure(text="playing…", fg="#333")
        win.update_idletasks()
        ok = play_fn()
        state["played"] = state["played"] or ok
        status.configure(
            text=("playback ok — you may classify" if ok
                  else "PLAYBACK FAILED — this row cannot be classified"),
            fg=("#282" if ok else "#a33"))
        refresh_submit()

    def on_submit():
        if not state["played"]:
            return
        cls = cls_var.get()
        state["result"] = {"classification": cls,
                           "action": allowed_classes[cls]["action"],
                           "reason_code": reason_var.get()}
        win.quit()

    def on_skip():
        win.quit()

    def on_quit():
        state["quit"] = True
        win.quit()

    bar = tk.Frame(win)
    tk.Button(bar, text="Play / Replay", command=on_play).pack(side="left")
    submit.configure(command=on_submit)
    submit.pack(side="left", padx=8)
    tk.Button(bar, text="Skip", command=on_skip).pack(side="left")
    tk.Button(bar, text="Quit", command=on_quit).pack(side="left", padx=8)
    bar.pack(anchor="w", padx=12, pady=(6, 12))

    win.mainloop()
    try:
        win.destroy()
    except Exception:
        pass
    if state["quit"]:
        return {"__quit__": True}
    return state["result"]


def atomic_write(path: Path, doc: dict) -> None:
    """Write via a temp file and rename, so an interruption cannot truncate it."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2) + "\n")
    os.replace(tmp, path)


def find_row(cli, checksum: str, version: str) -> dict | None:
    tok = {"Bucket": BUCKET, "Prefix": "curated/"}
    while True:
        r = cli.list_objects_v2(**tok)
        for o in r.get("Contents", []):
            if not o["Key"].endswith(f"/{version}/manifest.jsonl"):
                continue
            body = cli.get_object(Bucket=BUCKET, Key=o["Key"])["Body"].read()
            for line in body.decode().splitlines():
                if checksum[:24] in line:
                    rec = json.loads(line)
                    if rec["audio_checksum_sha256"] == checksum:
                        return rec
        if not r.get("IsTruncated"):
            return None
        tok["ContinuationToken"] = r["NextContinuationToken"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reviewer-role", required=True)
    ap.add_argument("--version", default="v2")
    a = ap.parse_args()
    if "@" in a.reviewer_role:
        raise SystemExit("REFUSING: reviewer-role must be a role, not an identity")

    refuse_unless_local_human()

    doc = json.loads(DECISION.read_text())
    if doc["status"] != "draft":
        raise SystemExit(f"REFUSING: {doc['decision_id']} is {doc['status']}")

    cli = client()
    print("verifying bindings before any content is fetched...")
    b = RB.recompute(cli, a.version)
    bad = RB.verify(b)
    if bad:
        raise SystemExit("REFUSING — bindings do not verify:\n  " + "\n  ".join(bad))
    print(f"  audit sha256      {b['audit_sha256'][:16]}  (clean commit "
          f"{b['audit_verifier_git_commit'][:12]})")
    print(f"  COMPLETE sha256   {b['complete_sha256'][:16]}  adopted="
          f"{b['complete_adopted']}")
    print(f"  manifests         {b['manifests_matching']}/{b['manifests_total']} match")
    print(f"  tokenizer cache   {b['tokenizer_cache_manifest_sha256'][:16]}\n")

    todo = [e for e in doc["entries"] if e["classification"] is None]
    print(f"{len(todo)} entr{'y' if len(todo)==1 else 'ies'} to review\n")

    for i, entry in enumerate(todo, 1):
        cs = entry["audio_checksum_sha256"]
        rec = find_row(cli, cs, a.version)
        if rec is None:
            print(f"[{i}/{len(todo)}] {cs[:16]} NOT FOUND at {a.version}; skipping")
            continue
        key = rec["audio_filepath"].split(f"{BUCKET}/", 1)[1]
        audio = cli.get_object(Bucket=BUCKET, Key=key)["Body"].read()
        if hashlib.sha256(audio).hexdigest() != cs:
            print(f"[{i}/{len(todo)}] CHECKSUM MISMATCH; refusing this row")
            continue

        header = (f"{entry['language']}/{entry['task']}   "
                  f"{entry['label_tokens_effective']} tokens   "
                  f"{entry['duration_s']:.2f}s   {entry['tokens_per_s']:.2f} tok/s\n"
                  f"trigger: {entry['trigger']}\n{cs}")
        print(f"[{i}/{len(todo)}] {cs[:16]}  {entry['language']}/{entry['task']}  "
              f"{entry['label_tokens_effective']} tok  {entry['duration_s']:.2f}s")

        allowed = {c: {"action": ACTION_FOR[c], "reasons": REASON_FOR[c]}
                   for c in CLASSES_FOR_TRIGGER[entry["trigger"]]}
        result = review_window(rec["text_normalized"], header, allowed,
                               lambda: play_audio(audio))
        if result is None:
            print("  skipped\n")
            del audio, rec
            continue
        if result.get("__quit__"):
            atomic_write(DECISION, doc)
            print("saved.")
            return 0
        entry.update(classification=result["classification"],
                     action=result["action"], reason_code=result["reason_code"],
                     reviewer_role=a.reviewer_role, listened=True,
                     reviewed_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        atomic_write(DECISION, doc)     # after every entry, not at the end
        print(f"  recorded {result['classification']}\n")
        del audio, rec

    atomic_write(DECISION, doc)
    done = sum(1 for e in doc["entries"] if e["classification"])
    print(f"\nclassified {done}/{len(doc['entries'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
