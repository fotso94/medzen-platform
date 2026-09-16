"""Arm-2 PROTECTED EVALUATOR entrypoint (Codex final-gap correction
2026-08-26 item 1): decode a pinned scoring manifest through ONE pinned model
export and emit hypothesis receipts. Decode-only — no training, no scoring
statistics, no references (the scorer recomputes WER from the pinned
references; this job never sees them, so it cannot bias toward them).

Decode parity BY CONSTRUCTION: the audio preprocessing and CTC-greedy decode
are IMPORTED from pipeline.omniasr_calibrate — the exact functions the
mandatory in-run upstream-parity probe validates on every calibration
(_preprocess_wave, _ctc_greedy_text). This module adds no decode logic of its
own; the decoding contract record pins this import relationship.

Environment contract (all REQUIRED; the launcher injects them from the
reviewed scoring-job packet):
  MEDZEN_SCORE_MANIFEST_S3_URI / _VERSION_ID / _SHA256:
      the scoring manifest (jsonl: audio_checksum_sha256 + audio_s3_uri),
      fetched by exact VersionId and sha-verified.
  MEDZEN_SCORE_MODEL_S3_URI / _VERSION_ID / _SHA256:
      the model export (model.pt) to decode with, fetched by exact VersionId;
      the artifact bytes MUST hash to _SHA256 (the arm's completion-receipt
      export.model_sha256) or the job refuses.
  MEDZEN_SCORE_ARM: the arm label (base|arm1|KD_CONTROL|H0|H1..H4).
  MEDZEN_SCORE_TRAINING_PACKET_CANONICAL_SHA256: the arm's training-packet
      canonical sha ('' for the frozen base/arm1 checkpoints).
  MEDZEN_SCORE_SPLIT_SHA256: the frozen nomination-split artifact sha.
  MEDZEN_SCORE_EVALUATOR_IMAGE_DIGEST: this job's own image digest (the
      launcher injects the packet-pinned digest; recorded into the receipts).
  MEDZEN_SCORE_INPUT_CONVENTION (OPTIONAL, default 'normalized'): 'normalized'
      is the frozen contract path (_preprocess_wave) and serving's convention.
      'raw' is DIAGNOSTIC ONLY: the waveform exactly as training feeds it
      (_raw_wave, also imported from calibrate). Receipts declare which was
      used, and the nomination scorer refuses any that are not 'normalized'.
  MEDZEN_SCORE_DUMP_MODE (OPTIONAL, default 'off'): 'hybrid' ALSO writes a
      per-frame probability dump (decoder diagnostic) beside the UNCHANGED
      receipts; an unrecognised value refuses rather than falling back. It
      is read with MEDZEN_SCORE_DUMP_TOPK (default 16),
      MEDZEN_SCORE_DUMP_SYMBOLS_S3_URI / _VERSION_ID / _SHA256 (the
      byte-pinned kept-set artifact, fetched and sha-verified like every
      other pinned input) and MEDZEN_SCORE_DUMP_DEST (default
      /opt/ml/checkpoints, which SageMaker already syncs). receipts.json is
      byte-identical in both modes: everything new lands in the dump header.
The receipts file is written to /opt/ml/model/receipts.json (the SageMaker
output artifact); the workflow's attest step signs its exact bytes.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


class EvaluatorRefusal(SystemExit):
    pass


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise EvaluatorRefusal(f"{name} is required — the evaluator runs only "
                               "under the reviewed scoring-job packet")
    return value


INPUT_CONVENTIONS = ("normalized", "raw")


def resolve_input_convention(environ) -> str:
    """'normalized' unless MEDZEN_SCORE_INPUT_CONVENTION explicitly says
    'raw'. An unrecognised value refuses rather than falling back, so a typo
    can never silently produce contract-path receipts labelled as raw."""
    value = str(environ.get("MEDZEN_SCORE_INPUT_CONVENTION", "")).strip().lower()
    if not value:
        return "normalized"
    if value not in INPUT_CONVENTIONS:
        raise EvaluatorRefusal(
            f"MEDZEN_SCORE_INPUT_CONVENTION={value!r} is not one of "
            f"{INPUT_CONVENTIONS}")
    return value


# ---------------------------------------------------------------------------
# OPTIONAL per-frame probability dump (decoder diagnostic, 2026-09-15).
# DEFAULT OFF. When off, this evaluator behaves exactly as before: the only
# statements reached are `dump_mode = resolve_dump_mode(...)` (which returns
# "off") and `dump = None`; receipts.json is byte-for-byte what it was.
#
# When MEDZEN_SCORE_DUMP_MODE=hybrid the job ALSO writes, beside the
# unchanged receipts, a per-frame dump that is enough to (a) recompute the
# job's own greedy transcripts byte-identically and (b) run an OFFLINE beam
# search over a declared symbol set. Per valid frame it stores
#   the |S| kept-set logits  UNION  the top-k ids+logits,
#   the FULL-vocabulary logsumexp (fp32, so every stored logit converts to an
#   exact full-vocabulary log-probability),
#   and the argmax id AND its logit — required, because every scored model
#   emits characters outside its language's training-target set, so a
#   kept-set-only dump cannot even reproduce the receipts it came from.
#
# This module still adds NO decode logic: recomputation reconstructs a dense
# frame matrix and calls pipeline.omniasr_calibrate._ctc_greedy_text, the same
# frozen function the decode path uses and the in-run upstream parity probe
# validates.
DUMP_MODES = ("off", "hybrid")
# 'full' (every one of the 10,288 logits per frame) is deliberately NOT in the
# enum: dev alone would be 5.64 GB, so it refuses here and needs its own
# owner decision rather than a typo's worth of silent gigabytes.
DUMP_DEFAULT_TOPK = 16
DUMP_MAX_TOPK = 64
# the job's ALREADY-CONFIGURED output directories — /opt/ml/checkpoints is
# synced to s3://<bucket>/research/b5-training/<job_id>/checkpoints and
# /opt/ml/model is tarred to .../output/model.tar.gz (scripts/b5_sagemaker_job
# render(): OutputDataConfig + CheckpointConfig). Both are existing scope; no
# new S3 write permission is involved. The default is /opt/ml/checkpoints so
# the attested model.tar.gz keeps carrying receipts.json and nothing else.
DUMP_DEST_ROOTS = ("/opt/ml/checkpoints", "/opt/ml/model")
DUMP_FORMAT = "medzen-score-dump-v1"
DUMP_HEADER_NAME = "score-dump-v1.header.json"
DUMP_FRAMES_NAME = "score-dump-v1.frames.bin"
# ESTIMATE ONLY (w2v2 conv stack: total stride 320 samples at 16 kHz = one
# frame per 20 ms). The AUTHORITATIVE per-clip count is the encoder's
# seq_lens, recorded per row; the header also reports the measured mean.
DUMP_FRAME_RATE_FPS_ESTIMATE = 49.9
# fp16 storage of bf16 logits is bit-exact in range (bf16 carries 7 mantissa
# bits, fp16 carries 10; only the exponent range differs), so the writer
# refuses rather than silently saturating near the fp16 limit.
DUMP_FP16_MAX = 65504.0


def resolve_dump_mode(environ) -> str:
    """'off' unless MEDZEN_SCORE_DUMP_MODE explicitly names a known mode. An
    unrecognised value REFUSES rather than falling back, so a typo can never
    produce a dump-less run that everyone believes is a dump run — the same
    fail-closed pattern resolve_input_convention uses."""
    value = str(environ.get("MEDZEN_SCORE_DUMP_MODE", "")).strip().lower()
    if not value:
        return "off"
    if value not in DUMP_MODES:
        raise EvaluatorRefusal(
            f"MEDZEN_SCORE_DUMP_MODE={value!r} is not one of {DUMP_MODES}")
    return value


def resolve_dump_topk(environ) -> int:
    value = str(environ.get("MEDZEN_SCORE_DUMP_TOPK", "")).strip()
    if not value:
        return DUMP_DEFAULT_TOPK
    try:
        topk = int(value)
    except ValueError:
        raise EvaluatorRefusal(
            f"MEDZEN_SCORE_DUMP_TOPK={value!r} is not an integer")
    if not 1 <= topk <= DUMP_MAX_TOPK:
        raise EvaluatorRefusal(
            f"MEDZEN_SCORE_DUMP_TOPK={topk} is outside 1..{DUMP_MAX_TOPK}")
    return topk


def resolve_dump_dest(environ) -> Path:
    """The dump lands in one of the job's existing output directories. A path
    anywhere else refuses: a dump is a diagnostic artifact, not a licence to
    write somewhere the job's reviewed output scope does not already cover."""
    value = str(environ.get("MEDZEN_SCORE_DUMP_DEST", "")).strip()
    dest = Path(value) if value else Path(DUMP_DEST_ROOTS[0])
    roots = [Path(root) for root in DUMP_DEST_ROOTS]
    if not any(dest == root or root in dest.parents for root in roots):
        raise EvaluatorRefusal(
            f"MEDZEN_SCORE_DUMP_DEST={str(dest)!r} is outside the job's "
            f"existing output directories {DUMP_DEST_ROOTS}")
    return dest


def parse_kept_set(raw: bytes, *, blank_idx: int) -> tuple[list[int], dict]:
    """Parse the BYTE-PINNED kept-set artifact (its sha256 is verified by the
    pinned fetch and recorded in the dump header). The set is never recomputed
    at job time: recomputing 'the training-target token set' gave 68/71/47
    tokens here against 70/71/48 in an earlier pass, which is exactly why the
    decoder's symbol set has to be a reviewed artifact.

    The artifact must DECLARE the blank id it was built against, and that
    declaration must equal the blank the tokenizer actually reports — a dump
    built around a different blank would decode to something else."""
    try:
        doc = json.loads(raw.decode())
    except Exception as exc:                       # noqa: BLE001
        raise EvaluatorRefusal(f"kept-set artifact is not JSON: {exc}")
    ids = doc.get("token_ids")
    if not isinstance(ids, list) or not ids:
        raise EvaluatorRefusal(
            "kept-set artifact carries no non-empty token_ids list")
    kept: list[int] = []
    for value in ids:
        if isinstance(value, bool) or not isinstance(value, int):
            raise EvaluatorRefusal(
                f"kept-set token id {value!r} is not an integer")
        kept.append(int(value))
    if len(set(kept)) != len(kept):
        raise EvaluatorRefusal("kept-set token_ids contain duplicates")
    declared_blank = doc.get("blank_token_id")
    if declared_blank is None:
        raise EvaluatorRefusal(
            "kept-set artifact must declare blank_token_id — the blank is "
            "tokenizer.vocab_info.pad_idx and is never assumed")
    if int(declared_blank) != int(blank_idx):
        raise EvaluatorRefusal(
            f"kept-set artifact declares blank_token_id={int(declared_blank)}, "
            f"the tokenizer reports pad_idx={int(blank_idx)} — refusing a "
            "symbol set built against a different blank")
    if int(blank_idx) not in kept:
        raise EvaluatorRefusal(
            f"kept-set token_ids omit the blank id {int(blank_idx)}")
    return kept, doc


def dump_frame_dtype(kept_count: int, topk: int):
    """The FIXED-SIZE little-endian per-frame record. Fixed size (and no
    deduplication between the kept set and the top-k) is deliberate: it makes
    the offline reader a single memory-map and the byte arithmetic exact —
    105 kept + top-16 is 210 + 32 + 32 + 4 + 2 + 2 = 282 bytes per frame."""
    import numpy as np

    return np.dtype([("kept", "<f2", (int(kept_count),)),
                     ("topk_ids", "<u2", (int(topk),)),
                     ("topk_logits", "<f2", (int(topk),)),
                     ("logsumexp", "<f4"),
                     ("argmax_id", "<u2"),
                     ("argmax_logit", "<f2")])


def encode_frames(wide, argmax_ids, *, kept_ids: list[int], topk: int):
    """Pure-numpy encoder: [T, vocab] fp32 logits + the argmax ids the decode
    rule itself produced -> the structured per-frame records. `argmax_ids` is
    passed in rather than recomputed so the stored argmax is, by construction,
    the one the frozen decoder used on this exact tensor."""
    import numpy as np

    wide = np.asarray(wide, dtype=np.float32)
    argmax_ids = np.asarray(argmax_ids, dtype=np.int64)
    frames, vocab = wide.shape
    if argmax_ids.shape != (frames,):
        raise EvaluatorRefusal("argmax id count does not match the frame count")
    if vocab > 65535:
        raise EvaluatorRefusal(
            f"vocabulary {vocab} exceeds the uint16 token-id width")
    if topk > vocab:
        raise EvaluatorRefusal(f"top-k {topk} exceeds the vocabulary {vocab}")
    finite = np.isfinite(wide)
    if not bool(finite.all()):
        raise EvaluatorRefusal("logits carry non-finite values")
    peak = float(np.abs(wide).max()) if frames else 0.0
    if peak >= DUMP_FP16_MAX:
        raise EvaluatorRefusal(
            f"max |logit| {peak} reaches the fp16 limit {DUMP_FP16_MAX} — "
            "fp16 storage would no longer be lossless")
    record = np.zeros(frames, dtype=dump_frame_dtype(len(kept_ids), topk))
    index = np.arange(frames)
    order = np.argsort(-wide, axis=-1, kind="stable")[:, :topk]
    record["kept"] = wide[:, kept_ids]
    record["topk_ids"] = order
    record["topk_logits"] = np.take_along_axis(wide, order, axis=-1)
    record["logsumexp"] = _logsumexp(wide)
    record["argmax_id"] = argmax_ids
    record["argmax_logit"] = wide[index, argmax_ids]
    return record, peak


def _logsumexp(wide):
    import numpy as np

    peak = wide.max(axis=-1, keepdims=True)
    return (peak + np.log(np.exp(wide - peak).sum(axis=-1, keepdims=True))
            ).astype(np.float32).reshape(-1)


def reconstruct_dense(record, *, kept_ids: list[int], vocab_size: int):
    """The dense [T, vocab] matrix an offline decoder sees: the stored values
    where the dump kept something, -inf everywhere else. The argmax column is
    written LAST so the value the decode rule actually maximised is the one
    present, even when that id is outside the kept set."""
    import numpy as np

    frames = int(record.shape[0])
    dense = np.full((frames, int(vocab_size)), -np.inf, dtype=np.float32)
    index = np.arange(frames)[:, None]
    dense[:, list(kept_ids)] = record["kept"].astype(np.float32)
    dense[index, record["topk_ids"].astype(np.int64)] = \
        record["topk_logits"].astype(np.float32)
    dense[index[:, 0], record["argmax_id"].astype(np.int64)] = \
        record["argmax_logit"].astype(np.float32)
    return dense


def greedy_from_dump(record, *, kept_ids: list[int], vocab_size: int,
                     blank_idx: int, decoder) -> tuple[str, int]:
    """Recompute the greedy transcript from the dump ALONE, through the frozen
    decoder. Returns (text, argmax_divergence_frames): the second number
    counts frames where re-taking the argmax over the reconstructed matrix
    picks a different id than the one stored — a tie-breaking difference,
    which the transcript comparison then either confirms harmless or fails."""
    import numpy as np

    from pipeline.omniasr_calibrate import _ctc_greedy_text

    dense = reconstruct_dense(record, kept_ids=kept_ids, vocab_size=vocab_size)
    stored = record["argmax_id"].astype(np.int64)
    divergence = int((dense.argmax(axis=-1) != stored).sum())
    text = _ctc_greedy_text(dense, decoder, int(blank_idx),
                            valid_frames=int(dense.shape[0]))
    return text, divergence


class _DumpWriter:
    """Streams the per-frame records to <dest>/score-dump-v1.frames.bin and
    writes the self-describing header at close. Every row is VERIFIED before
    it is kept: the transcript recomputed from the bytes just encoded must
    equal the hypothesis this job is putting in receipts.json, or the job
    refuses. A dump that cannot reproduce its own receipts is worthless."""

    def __init__(self, dest: Path, *, kept_ids: list[int], topk: int,
                 blank_idx: int, header: dict):
        self.dest = dest
        self.kept_ids = list(kept_ids)
        self.topk = int(topk)
        self.blank_idx = int(blank_idx)
        self.header = dict(header)
        self.rows: list[dict] = []
        self.offset = 0
        self.divergences = 0
        self.vocab_size = None
        self.logits_dtype = None
        dest.mkdir(parents=True, exist_ok=True)
        self.frames_path = dest / DUMP_FRAMES_NAME
        self.handle = self.frames_path.open("wb")

    def write_row(self, audio_checksum_sha256: str, logits, valid_frames,
                  hyp: str, decoder, *, samples: int, sample_rate: int) -> dict:
        if valid_frames is None:
            raise EvaluatorRefusal(
                "the dump needs the encoder's valid frame count (seq_lens); "
                "refusing to guess how many frames are real")
        # the SAME truncation and the SAME argmax the frozen decode rule
        # applies, on the same tensor, so the stored argmax cannot disagree
        frames = logits[: int(valid_frames)]
        argmax_ids = frames.argmax(dim=-1)
        wide = frames.float().detach().cpu().numpy()
        argmax_ids = argmax_ids.detach().cpu().numpy()
        if self.vocab_size is None:
            self.vocab_size = int(wide.shape[1])
            self.logits_dtype = str(getattr(frames, "dtype", ""))
            for token in self.kept_ids:
                if not 0 <= token < self.vocab_size:
                    raise EvaluatorRefusal(
                        f"kept-set token id {token} is outside the observed "
                        f"vocabulary 0..{self.vocab_size - 1}")
        elif int(wide.shape[1]) != self.vocab_size:
            raise EvaluatorRefusal(
                f"vocabulary changed mid-run: {wide.shape[1]} vs "
                f"{self.vocab_size}")
        record, peak = encode_frames(wide, argmax_ids,
                                     kept_ids=self.kept_ids, topk=self.topk)
        text, divergence = greedy_from_dump(
            record, kept_ids=self.kept_ids, vocab_size=self.vocab_size,
            blank_idx=self.blank_idx, decoder=decoder)
        if text != hyp:
            raise EvaluatorRefusal(
                f"row {audio_checksum_sha256[:12]}: greedy recomputed from the "
                f"dump is {text!r} but the receipts say {hyp!r} — the dump "
                "does not reproduce this job's own decode")
        payload = record.tobytes()
        self.handle.write(payload)
        self.divergences += divergence
        entry = {"audio_checksum_sha256": audio_checksum_sha256,
                 "offset": self.offset, "bytes": len(payload),
                 "valid_frames": int(valid_frames), "samples": int(samples),
                 "sample_rate": int(sample_rate),
                 "max_abs_logit": round(float(peak), 6),
                 "argmax_divergence_frames": divergence,
                 "hyp_sha256": hashlib.sha256(hyp.encode()).hexdigest()}
        self.rows.append(entry)
        self.offset += len(payload)
        return entry

    def close(self) -> dict:
        self.handle.close()
        frames_total = sum(row["valid_frames"] for row in self.rows)
        seconds = sum(row["samples"] / row["sample_rate"] for row in self.rows
                      if row["sample_rate"])
        header = dict(self.header)
        header.update({
            "blank_index": int(self.blank_idx),
            "vocab_size": self.vocab_size,
            "logits_dtype": self.logits_dtype,
            "frame_record_bytes": (
                int(dump_frame_dtype(len(self.kept_ids), self.topk).itemsize)
                if self.vocab_size is not None else None),
            "frame_rate_fps": {
                "declared_estimate": DUMP_FRAME_RATE_FPS_ESTIMATE,
                "estimate_basis": "w2v2 conv stride 320 at 16 kHz (ESTIMATE)",
                "measured_mean": (round(frames_total / seconds, 4)
                                  if seconds else None),
                "authoritative_per_clip": "rows[].valid_frames (seq_lens)"},
            "rows": self.rows,
            "rows_written": len(self.rows),
            "frames_total": frames_total,
            "bytes_total": self.offset,
            "argmax_divergence_frames_total": self.divergences,
            "greedy_parity": {
                "rule": "every row's transcript was recomputed from these "
                        "bytes through pipeline.omniasr_calibrate."
                        "_ctc_greedy_text and equals receipts.rows[i]."
                        "hyp_normalized; any mismatch refused the job",
                "rows_checked": len(self.rows)},
        })
        payload = json.dumps(header, indent=1, sort_keys=True).encode() + b"\n"
        (self.dest / DUMP_HEADER_NAME).write_bytes(payload)
        return {"status": "SCORE_DUMP_WRITTEN",
                "dest": str(self.dest),
                "rows": len(self.rows),
                "frames": frames_total,
                "bytes": self.offset,
                "header_sha256": hashlib.sha256(payload).hexdigest(),
                "frames_sha256": _sha256_file(self.frames_path)}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _open_dump(cli, environ, *, scratch: Path, blank_idx: int, tokenizer,
               manifest_sha: str, job_name: str, arm: str, model_sha: str,
               image_digest: str, input_convention: str,
               manifest_rows: int) -> "_DumpWriter":
    """Fetch the byte-pinned kept set by EXACT VersionId, validate it against
    the blank the tokenizer actually reports, and open the writer."""
    mode = resolve_dump_mode(environ)
    topk = resolve_dump_topk(environ)
    dest = resolve_dump_dest(environ)
    symbols_uri = _require("MEDZEN_SCORE_DUMP_SYMBOLS_S3_URI")
    symbols_vid = _require("MEDZEN_SCORE_DUMP_SYMBOLS_VERSION_ID")
    symbols_sha = _require("MEDZEN_SCORE_DUMP_SYMBOLS_SHA256")
    raw = _fetch_pinned(cli, symbols_uri, symbols_vid, symbols_sha,
                        scratch / "score-dump-symbols.json")
    kept_ids, doc = parse_kept_set(raw, blank_idx=blank_idx)
    pad_idx = getattr(getattr(tokenizer, "vocab_info", None), "pad_idx", None)
    if pad_idx is None:
        raise EvaluatorRefusal(
            "tokenizer.vocab_info.pad_idx is absent — a dump run must record "
            "the blank it used, not fall back to 0")
    header = {
        "record": "ARM2-SCORE-DUMP-HEADER",
        "format": DUMP_FORMAT,
        "mode": mode,
        "job_name": job_name,
        "arm": arm,
        "model_sha256": model_sha,
        "manifest_sha256": manifest_sha,
        "manifest_rows": int(manifest_rows),
        "evaluator_image_digest": image_digest,
        "input_convention": input_convention,
        "blank_index": int(blank_idx),
        "blank_index_source": "tokenizer.vocab_info.pad_idx",
        "tokenizer_pad_idx_raw": (None if pad_idx is None else int(pad_idx)),
        "topk": topk,
        "byte_order": "little",
        "logits_are_normalized": False,
        "frame_record_layout": [
            "kept_logits[|S|] float16 (declared kept-set id order)",
            "topk_ids[k] uint16", "topk_logits[k] float16",
            "logsumexp float32 (FULL vocabulary)",
            "argmax_id uint16", "argmax_logit float16"],
        "kept_set": {"sha256": symbols_sha, "s3_uri": symbols_uri,
                     "s3_version_id": symbols_vid, "count": len(kept_ids),
                     "token_ids": list(kept_ids),
                     "declared_blank_token_id": int(doc["blank_token_id"]),
                     "record": doc.get("record")},
        "frames_file": DUMP_FRAMES_NAME,
    }
    print(json.dumps({"status": "SCORE_DUMP_OPEN", "mode": mode,
                      "dest": str(dest), "topk": topk,
                      "kept_set_sha256": symbols_sha,
                      "kept_symbols": len(kept_ids),
                      "blank_index": int(blank_idx)}, sort_keys=True))
    return _DumpWriter(dest, kept_ids=kept_ids, topk=topk,
                       blank_idx=blank_idx, header=header)


def _fetch_pinned(cli, s3_uri: str, version_id: str, sha256: str,
                  dest: Path, *, expected_sha_of: str = "object") -> bytes:
    """Fetch by EXACT VersionId. When `expected_sha_of` is 'object' the raw
    bytes must hash to `sha256`. When it is 'member:export/model.pt' the
    object is a SageMaker model.tar.gz — the member export/model.pt is
    SAFELY extracted (no links/traversal) and ITS bytes must hash to
    `sha256` (the arm completion receipt's export.model_sha256)."""
    import tarfile

    bucket, _, key = s3_uri.removeprefix("s3://").partition("/")
    body = cli.get_object(Bucket=bucket, Key=key,
                          VersionId=version_id)["Body"].read()
    if expected_sha_of == "object":
        actual = hashlib.sha256(body).hexdigest()
        if actual != sha256:
            raise EvaluatorRefusal(
                f"{s3_uri}@{version_id} hashes to {actual[:16]}, the packet "
                f"pins {sha256[:16]} — refusing a substituted input")
        dest.write_bytes(body)
        return body
    member_name = expected_sha_of.removeprefix("member:")
    tar_path = dest.with_suffix(".tar.gz")
    tar_path.write_bytes(body)
    with tarfile.open(tar_path, "r:*") as archive:
        member = None
        for cand in archive.getmembers():
            if cand.name.lstrip("./") == member_name:
                member = cand
                break
        if member is None or not member.isreg():
            raise EvaluatorRefusal(
                f"{s3_uri} carries no regular member {member_name!r}")
        extracted = archive.extractfile(member).read()
    tar_path.unlink()
    actual = hashlib.sha256(extracted).hexdigest()
    if actual != sha256:
        raise EvaluatorRefusal(
            f"{member_name} inside {s3_uri}@{version_id} hashes to "
            f"{actual[:16]}, the arm's completion receipt requires "
            f"{sha256[:16]} — refusing a substituted model")
    dest.write_bytes(extracted)
    return extracted


def main() -> int:
    import torch

    from pipeline.omniasr_calibrate import (_ctc_greedy_text,
                                            _preprocess_wave, _raw_wave)
    from pipeline.omniasr_data import fetch_audio
    from pipeline.omniasr_train import (_load_model_and_tokenizer,
                                        parse_config, stage_model_artifacts)
    from pipeline.train_asr import s3

    manifest_uri = _require("MEDZEN_SCORE_MANIFEST_S3_URI")
    manifest_vid = _require("MEDZEN_SCORE_MANIFEST_VERSION_ID")
    manifest_sha = _require("MEDZEN_SCORE_MANIFEST_SHA256")
    arm = _require("MEDZEN_SCORE_ARM")
    # BASE MODE (owner decision 2026-08-27): the frozen base teacher has NO
    # fine-tuned export/model.pt — the staged base checkpoint IS the model. So
    # ONLY when the arm is exactly 'base' the export-weight fetch+load is
    # skipped; every other arm still fetches and strict-loads its export. The
    # model identity for base is the staged base checkpoint sha (verified by
    # stage_model_artifacts), which MEDZEN_SCORE_MODEL_SHA256 must equal.
    base_mode = (arm.lower() == "base")
    model_sha = _require("MEDZEN_SCORE_MODEL_SHA256")
    model_uri = "" if base_mode else _require("MEDZEN_SCORE_MODEL_S3_URI")
    model_vid = "" if base_mode else _require("MEDZEN_SCORE_MODEL_VERSION_ID")
    split_sha = _require("MEDZEN_SCORE_SPLIT_SHA256")
    image_digest = _require("MEDZEN_SCORE_EVALUATOR_IMAGE_DIGEST")
    input_convention = resolve_input_convention(os.environ)
    # an unrecognised dump mode refuses HERE, before a model is fetched or a
    # row decoded — a typo must never produce a dump-less run that the packet,
    # the reviewer and the receipts all describe as a dump run
    dump_mode = resolve_dump_mode(os.environ)
    training_packet_sha = os.environ.get(
        "MEDZEN_SCORE_TRAINING_PACKET_CANONICAL_SHA256", "").strip()
    job_name = (os.environ.get("MEDZEN_TRAINING_JOB_NAME")
                or os.environ.get("TRAINING_JOB_NAME") or "").strip()
    if not job_name:
        raise EvaluatorRefusal("no injected TrainingJobName — the evaluator "
                               "runs only inside the protected job")

    # the ARM label must be bound into the job's own name — the launcher
    # derives job names as medzen-b5-<job_id> and every scoring job_id embeds
    # its arm; a receipts file whose arm disagrees with its job refuses here,
    # before a single row is decoded.
    if f"-score-{arm.lower()}" not in job_name.lower():
        raise EvaluatorRefusal(
            f"job {job_name!r} does not embed arm {arm!r} — the arm/job "
            "binding is broken")

    cli = s3()
    # the student architecture loads from the STAGED base checkpoint (the
    # same stage_model_artifacts step the trainer runs before model load).
    # stage_model_artifacts sha-verifies the base against CTC_MODEL_ARTIFACTS
    # (omniASR-CTC-1B-v2.pt == 354f9817…); in base mode that IS the model.
    staged = stage_model_artifacts(cli)
    if base_mode:
        staged_base_sha = staged.get("omniASR-CTC-1B-v2.pt", "")
        if staged_base_sha != model_sha:
            raise EvaluatorRefusal(
                f"base mode: the staged base checkpoint hashes to "
                f"{staged_base_sha[:16]}, the packet pins {model_sha[:16]} — "
                "refusing a base identity mismatch")
    work = Path("/opt/ml/model")          # ONLY receipts.json lands here
    work.mkdir(parents=True, exist_ok=True)
    scratch = Path("/tmp/medzen-score-scratch")
    scratch.mkdir(parents=True, exist_ok=True)
    manifest_raw = _fetch_pinned(cli, manifest_uri, manifest_vid,
                                 manifest_sha,
                                 scratch / "score-manifest.jsonl")
    rows = [json.loads(line) for line in manifest_raw.decode().splitlines()
            if line.strip()]
    if not rows:
        raise EvaluatorRefusal("scoring manifest is empty")

    # the student architecture + tokenizer come from the staged base config;
    # the EXPORT weights are then loaded over it (mirrors the calibrate flow)
    config = parse_config(dict(os.environ))
    model, tokenizer, device = _load_model_and_tokenizer(config)
    if base_mode:
        # the staged, sha-verified base loaded above IS the model — no export
        # weights to fetch or load (owner base-mode decision)
        print(json.dumps({"status": "BASE_MODE_NO_EXPORT_LOAD",
                          "base_sha256": model_sha}, sort_keys=True))
    else:
        export_path = scratch / "score-model.pt"
        _fetch_pinned(cli, model_uri, model_vid, model_sha, export_path,
                      expected_sha_of=("member:export/model.pt"
                                       if model_uri.endswith(".tar.gz")
                                       else "object"))
        state = torch.load(export_path, map_location="cpu", weights_only=True)
        model.load_state_dict(state, strict=True)
    model.eval()

    import soundfile as sf
    from fairseq2.nn import BatchLayout

    blank_idx = int(getattr(getattr(tokenizer, "vocab_info", None),
                            "pad_idx", 0) or 0)
    decoder = tokenizer.create_decoder(skip_special_tokens=True)
    cache = Path(os.environ.get("MEDZEN_AUDIO_CACHE",
                                "/tmp/medzen-audio-cache"))
    out_rows = []
    dump = None
    if dump_mode != "off":
        dump = _open_dump(cli, os.environ, scratch=scratch,
                          blank_idx=blank_idx, tokenizer=tokenizer,
                          manifest_sha=manifest_sha, job_name=job_name,
                          arm=arm, model_sha=model_sha,
                          image_digest=image_digest,
                          input_convention=input_convention,
                          manifest_rows=len(rows))
    for row in rows:
        audio, sr = sf.read(fetch_audio(cli, row, cache),
                            dtype="float32", always_2d=False)
        if getattr(audio, "ndim", 1) > 1:
            audio = audio.mean(axis=1)
        prepared = (_raw_wave(audio, sr) if input_convention == "raw"
                    else _preprocess_wave(audio, sr))
        wave = prepared.to(torch.bfloat16).unsqueeze(0)
        if device is not None:
            wave = wave.to(device)
        layout = BatchLayout(tuple(wave.shape), seq_lens=[wave.shape[1]],
                             device=wave.device)
        with torch.no_grad():
            logits, out_layout = model(wave, layout)
        out_lens = getattr(out_layout, "seq_lens", None)
        valid = int(out_lens[0]) if out_lens is not None else None
        hyp = _ctc_greedy_text(logits[0], decoder, blank_idx,
                               valid_frames=valid)
        out_rows.append({"audio_checksum_sha256": row["audio_checksum_sha256"],
                         "hyp_normalized": hyp})
        if dump is not None:
            dump.write_row(row["audio_checksum_sha256"], logits[0], valid,
                           hyp, decoder, samples=int(len(audio)),
                           sample_rate=int(sr))

    receipts = {
        "record": f"ARM2-SCORING-RECEIPTS-{arm}",
        "job_name": job_name,
        "arm": arm,
        "model_sha256": model_sha,
        "model_artifact": ({"base_mode": True, "base_sha256": model_sha}
                           if base_mode else
                           {"s3_uri": model_uri, "s3_version_id": model_vid}),
        "training_packet_canonical_sha256": training_packet_sha or None,
        "split_sha256": split_sha,
        "evaluator_image_digest": image_digest,
        "manifest_sha256": manifest_sha,
        "input_convention": input_convention,
        "rows": out_rows,
    }
    payload = json.dumps(receipts, indent=1, sort_keys=True).encode() + b"\n"
    (work / "receipts.json").write_bytes(payload)
    print(json.dumps({"status": "SCORING_RECEIPTS_WRITTEN",
                      "arm": arm, "rows": len(out_rows),
                      "input_convention": input_convention,
                      "receipts_sha256":
                          hashlib.sha256(payload).hexdigest()},
                     sort_keys=True))
    if dump is not None:
        print(json.dumps(dump.close(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
