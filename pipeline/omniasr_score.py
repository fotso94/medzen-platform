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

    from pipeline.omniasr_calibrate import _ctc_greedy_text, _preprocess_wave
    from pipeline.omniasr_data import fetch_audio
    from pipeline.omniasr_train import _load_model_and_tokenizer, parse_config
    from pipeline.train_asr import s3

    manifest_uri = _require("MEDZEN_SCORE_MANIFEST_S3_URI")
    manifest_vid = _require("MEDZEN_SCORE_MANIFEST_VERSION_ID")
    manifest_sha = _require("MEDZEN_SCORE_MANIFEST_SHA256")
    model_uri = _require("MEDZEN_SCORE_MODEL_S3_URI")
    model_vid = _require("MEDZEN_SCORE_MODEL_VERSION_ID")
    model_sha = _require("MEDZEN_SCORE_MODEL_SHA256")
    arm = _require("MEDZEN_SCORE_ARM")
    split_sha = _require("MEDZEN_SCORE_SPLIT_SHA256")
    image_digest = _require("MEDZEN_SCORE_EVALUATOR_IMAGE_DIGEST")
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
    for row in rows:
        audio, sr = sf.read(fetch_audio(cli, row, cache),
                            dtype="float32", always_2d=False)
        if getattr(audio, "ndim", 1) > 1:
            audio = audio.mean(axis=1)
        wave = _preprocess_wave(audio, sr).to(torch.bfloat16).unsqueeze(0)
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

    receipts = {
        "record": f"ARM2-SCORING-RECEIPTS-{arm}",
        "job_name": job_name,
        "arm": arm,
        "model_sha256": model_sha,
        "model_artifact": {"s3_uri": model_uri, "s3_version_id": model_vid},
        "training_packet_canonical_sha256": training_packet_sha or None,
        "split_sha256": split_sha,
        "evaluator_image_digest": image_digest,
        "manifest_sha256": manifest_sha,
        "rows": out_rows,
    }
    payload = json.dumps(receipts, indent=1, sort_keys=True).encode() + b"\n"
    (work / "receipts.json").write_bytes(payload)
    print(json.dumps({"status": "SCORING_RECEIPTS_WRITTEN",
                      "arm": arm, "rows": len(out_rows),
                      "receipts_sha256":
                          hashlib.sha256(payload).hexdigest()},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
