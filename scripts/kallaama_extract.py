#!/usr/bin/env python3
"""Kallaama extract: NIST STM transcriptions -> segments.tsv for the adapter.

Kallaama ships timestamped transcriptions in NIST STM format (one file per
recording), audio in a separate OpenSLR tarball. This normalises the STM into
the flat segments TSV the kallaama adapter reads:

    <out_root>/<language>/segments.tsv
        wav_path  start_s  end_s  text  speaker_id  session_id

STM line:  <file> <channel> <speaker> <start> <end> <label> <text...>
Text carries Transcriber markup we strip for a clean verbatim transcript:
  * <...>  label field (also stripped defensively)
  * [r] [i] [mic] ...   event/noise tags
  * ` :fra`             per-word language tag (keep the word, drop the tag)

Usage:
  python scripts/kallaama_extract.py --lang wolof \
     --stm-dir  ~/medzen-data-staging/kallaama/repo/data/transcriptions/checked/transcriptions-wol/stm \
     --audio-dir ~/medzen-data-staging/kallaama/audio_wol \
     --out-root  ~/medzen-data-staging/kallaama/extract
Then: MEDZEN_KALLAAMA_DIR=<out-root> python -m pipeline.ingest --source kallaama --language wolof ...
"""
from __future__ import annotations
import argparse
import csv
import pathlib
import re

EVENT = re.compile(r"\[[^\]]*\]")          # [r] [i] [mic] ...
LABEL = re.compile(r"<[^>]*>")             # <o,f1,male>
LANGTAG = re.compile(r"\s*:[a-zA-Z]{2,4}\b")  # ` :fra` code-switch marker
WS = re.compile(r"\s+")


def clean(text: str) -> str:
    text = LABEL.sub(" ", text)
    text = EVENT.sub(" ", text)
    text = LANGTAG.sub(" ", text)          # drop the tag, keep the word
    return WS.sub(" ", text).strip()


def parse_stm(path: pathlib.Path):
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not raw.strip() or raw.startswith(";;"):
            continue
        parts = raw.split(None, 5)
        if len(parts) < 6:
            continue
        fileid, _chan, spk, start, end, rest = parts
        # rest may begin with the <label> token; clean() removes it
        try:
            start, end = float(start), float(end)
        except ValueError:
            continue
        text = clean(rest)
        if len(text) <= 3:
            continue
        yield fileid, spk, start, end, text


def find_audio(audio_dir: pathlib.Path, fileid: str) -> pathlib.Path | None:
    for ext in (".wav", ".WAV", ".mp3", ".flac", ".m4a"):
        hits = list(audio_dir.rglob(f"{fileid}{ext}"))
        if hits:
            return hits[0]
    hits = list(audio_dir.rglob(f"{fileid}.*"))
    return hits[0] if hits else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", required=True)
    ap.add_argument("--stm-dir", required=True)
    ap.add_argument("--audio-dir", required=True)
    ap.add_argument("--out-root", required=True)
    a = ap.parse_args()

    stm_dir = pathlib.Path(a.stm_dir).expanduser()
    audio_dir = pathlib.Path(a.audio_dir).expanduser()
    out = pathlib.Path(a.out_root).expanduser() / a.lang
    out.mkdir(parents=True, exist_ok=True)
    tsv = out / "segments.tsv"

    n_seg = n_skip_audio = 0
    missing: set[str] = set()
    with tsv.open("w", newline="") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow(["wav_path", "start_s", "end_s", "text",
                    "speaker_id", "session_id"])
        for stm in sorted(stm_dir.glob("*.stm")):
            for fileid, spk, start, end, text in parse_stm(stm):
                wav = find_audio(audio_dir, fileid)
                if wav is None:
                    missing.add(fileid); n_skip_audio += 1
                    continue
                w.writerow([str(wav), f"{start:.3f}", f"{end:.3f}", text,
                            spk, fileid])
                n_seg += 1
    print(f"  {a.lang}: wrote {n_seg} segments -> {tsv}")
    if n_skip_audio:
        print(f"  WARNING: {n_skip_audio} segments skipped (audio not found); "
              f"missing file ids e.g. {sorted(missing)[:5]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
