"""SEALED EVALUATOR container entrypoint (SEALED-EVALUATOR-SPEC-2026-001).

Runs INSIDE a network-isolated SageMaker Processing job. There is no boto3
import anywhere in this module — every input arrives via SageMaker channels
(downloaded by the execution role BEFORE isolation) and every output leaves
via the Processing output upload (performed by the execution role AFTER the
container exits). The container itself can neither read S3 nor reach KMS,
which is the provenance boundary the promotion gate verifies at admission
(CloudTrail PutObject principal + Object-Lock, promotion_check round 13-17).

Functional contract (reverse-engineered from the admission verifier,
services/model-loader/medzen_model_loader/promotion_check.py — the sealed
spec deliberately has no functional section):
  per sealed utterance, one JSONL row binding
    audio_checksum_sha256, cluster_id (== manifest speaker_id),
    reference_text_sha256 (sha256 of the manifest's text_normalized),
    baseline_hypothesis(+_sha256), candidate_hypothesis(+_sha256),
    baseline_errors, candidate_errors, reference_words
  where the three numbers RECOMPUTE under the pinned scorer_v1 from the
  bound texts; rows cover the sealed manifest EXACTLY once; and an
  INFERENCE RECEIPT names artifact_tree_sha256, decoding_config_sha256,
  image_digest, job_name and the sha256 of every row object.

Decode parity: audio preprocessing and CTC-greedy decode are IMPORTED from
pipeline.omniasr_calibrate (the upstream-parity-probed functions), exactly
as the protected Arm-2 evaluator does; hypotheses are normalized with the
same per-language normalizer the sealed references were built with
(pipeline.normalizers.for_language). This module adds no decode logic.

Environment contract (all injected by scripts/sealed_evaluator.py from the
owner-authorized packet; the packet's environment_sha256 binds them):
  MEDZEN_SEALED_JOB_NAME            the ProcessingJobName (echoed as receipt binding)
  MEDZEN_SEALED_ARTIFACT_TREE       sha256 tree = canonical({checkpoint,tokenizer})
  MEDZEN_SEALED_DECODING_CONFIG_SHA256   pinned decode-contract identity
  MEDZEN_SEALED_IMAGE_DIGEST        this job's own image reference (receipt echo)
  MEDZEN_SEALED_SCORER_SHA256       sha256 the baked scorer_v1.py bytes must match
  MEDZEN_SEALED_BASE_SHA256         baseline checkpoint sha (omniASR-CTC-1B-v2.pt)
  MEDZEN_SEALED_ARM1_SHA256         candidate export sha (model.pt)
  MEDZEN_SEALED_TOKENIZER_SHA256    tokenizer sha
  MEDZEN_SEALED_LANGS               comma list of the 7 language aliases
  MEDZEN_SEALED_MANIFEST_<ALIAS>    "<s3 key>:<sha256>" per language
plus the minimal trainer vars parse_config needs (MEDZEN_VARIANT etc.).
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

INPUT = Path("/opt/ml/processing/input")
OUTPUT = Path("/opt/ml/processing/output")
MODELS = Path("/models")


class SealedRefusal(SystemExit):
    pass


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SealedRefusal(f"{name} is required — the sealed evaluator "
                            "runs only under the owner-authorized packet")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 22), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _assemble_parts(parts_dir: Path, dest: Path, want_sha: str,
                    label: str) -> None:
    """Concatenate part-#### files (sorted) into dest and refuse on a sha
    mismatch — the channel form of stage_model_artifacts' parts logic."""
    parts = sorted(parts_dir.rglob("part-*"))
    if not parts:
        raise SealedRefusal(f"{label}: no part files under {parts_dir}")
    with dest.open("wb") as out:
        for part in parts:
            out.write(part.read_bytes())
    actual = _sha256_file(dest)
    if actual != want_sha:
        dest.unlink()
        raise SealedRefusal(
            f"{label}: assembled bytes hash to {actual[:16]}, the packet "
            f"pins {want_sha[:16]} — refusing a substituted input")


def artifact_tree(checkpoint_sha256: str, tokenizer_sha256: str) -> str:
    """The CANDIDATE artifact tree — canonical json over the candidate's own
    checkpoint (for Arm-1 that is the EXPORT model.pt sha, NOT the frozen
    base) plus the tokenizer, exactly as the serving manifest and the
    model-loader compute it (loader_v2 artifact_tree_sha256). Pure, host-
    testable — the real-packet regression pins it against the committed
    packet (Codex sealed-review finding 1: the first implementation hashed
    the BASE checkpoint here and would have refused before decoding)."""
    return hashlib.sha256(json.dumps(
        {"checkpoint_sha256": checkpoint_sha256,
         "tokenizer_sha256": tokenizer_sha256},
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _load_scorer(want_sha: str):
    """The pinned scorer, imported from the image-baked copy whose bytes
    must hash to the packet pin (the same discipline promotion_check
    applies to its own baked module)."""
    scorer_path = Path("/opt/medzen/promotion/scorer_v1.py")
    actual = _sha256_file(scorer_path)
    if actual != want_sha:
        raise SealedRefusal(
            f"baked scorer hashes to {actual[:16]}, the packet pins "
            f"{want_sha[:16]} — the scoring method is part of the contract")
    import importlib.util
    spec = importlib.util.spec_from_file_location("sealed_scorer", scorer_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    import torch

    from pipeline.normalizers import for_language
    from pipeline.omniasr_calibrate import _ctc_greedy_text, _preprocess_wave
    from pipeline.omniasr_train import (CTC_MODEL_ARTIFACTS,
                                        _load_model_and_tokenizer,
                                        parse_config)

    job_name = _require("MEDZEN_SEALED_JOB_NAME")
    tree = _require("MEDZEN_SEALED_ARTIFACT_TREE")
    decoding_sha = _require("MEDZEN_SEALED_DECODING_CONFIG_SHA256")
    image_digest = _require("MEDZEN_SEALED_IMAGE_DIGEST")
    scorer_sha = _require("MEDZEN_SEALED_SCORER_SHA256")
    base_sha = _require("MEDZEN_SEALED_BASE_SHA256")
    arm1_sha = _require("MEDZEN_SEALED_ARM1_SHA256")
    tok_sha = _require("MEDZEN_SEALED_TOKENIZER_SHA256")
    languages = [x.strip() for x in
                 _require("MEDZEN_SEALED_LANGS").split(",") if x.strip()]
    if len(languages) != 7:
        raise SealedRefusal("the sealed run covers exactly the 7 mandatory "
                            f"languages, got {languages}")
    scorer = _load_scorer(scorer_sha)

    # the tree the receipt attests must recompute from the CANDIDATE's own
    # identities: arm1's export sha + the tokenizer (NOT the base — Codex
    # sealed-review finding 1)
    recomputed_tree = artifact_tree(arm1_sha, tok_sha)
    if recomputed_tree != tree:
        raise SealedRefusal(
            f"artifact tree {tree[:16]} does not recompute from the pinned "
            f"candidate checkpoint+tokenizer ({recomputed_tree[:16]})")

    # --- stage the model set from channels, every byte sha-verified ---
    MODELS.mkdir(parents=True, exist_ok=True)
    _assemble_parts(INPUT / "base", MODELS / "omniASR-CTC-1B-v2.pt",
                    base_sha, "base checkpoint")
    _assemble_parts(INPUT / "tokenizer",
                    MODELS / "omniASR_tokenizer_written_v2.model",
                    tok_sha, "tokenizer")
    for name, spec_ in CTC_MODEL_ARTIFACTS.items():
        if spec_["sha256"] != _sha256_file(MODELS / name):
            raise SealedRefusal(f"staged {name} does not match the "
                                "trainer's own pinned identity")
    arm1_candidates = sorted((INPUT / "arm1").rglob("*.pt"))
    if len(arm1_candidates) != 1:
        raise SealedRefusal(f"arm1 channel must carry exactly one .pt, "
                            f"got {len(arm1_candidates)}")
    if _sha256_file(arm1_candidates[0]) != arm1_sha:
        raise SealedRefusal("arm1 export does not hash to the packet pin")

    config = parse_config(dict(os.environ))
    baseline, tokenizer, device = _load_model_and_tokenizer(config)
    baseline.eval()
    candidate, _, _ = _load_model_and_tokenizer(config)
    state = torch.load(arm1_candidates[0], map_location="cpu",
                       weights_only=True)
    candidate.load_state_dict(state, strict=True)
    candidate.eval()
    print(json.dumps({"status": "SEALED_MODELS_LOADED",
                      "baseline": base_sha[:16], "candidate": arm1_sha[:16]},
                     sort_keys=True))

    import soundfile as sf
    from fairseq2.nn import BatchLayout

    blank_idx = int(getattr(getattr(tokenizer, "vocab_info", None),
                            "pad_idx", 0) or 0)
    decoder = tokenizer.create_decoder(skip_special_tokens=True)

    def decode(model, wave_t) -> str:
        layout = BatchLayout(tuple(wave_t.shape), seq_lens=[wave_t.shape[1]],
                             device=wave_t.device)
        with torch.no_grad():
            logits, out_layout = model(wave_t, layout)
        lens = getattr(out_layout, "seq_lens", None)
        valid = int(lens[0]) if lens is not None else None
        return _ctc_greedy_text(logits[0], decoder, blank_idx,
                                valid_frames=valid)

    OUTPUT.mkdir(parents=True, exist_ok=True)
    rows_sha: dict[str, str] = {}
    for language in languages:
        pin = _require(f"MEDZEN_SEALED_MANIFEST_{language.upper()}")
        man_key, _, man_sha = pin.partition(":")
        local = INPUT / "manifests" / man_key
        if not local.is_file():
            raise SealedRefusal(f"{language}: sealed manifest {man_key} "
                                "not delivered by the manifests channel")
        raw = local.read_bytes()
        if hashlib.sha256(raw).hexdigest() != man_sha:
            raise SealedRefusal(f"{language}: sealed manifest bytes do not "
                                "hash to the packet pin — refusing")
        norm = for_language(language)
        out_lines = []
        for line in raw.decode().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            checksum = str(row["audio_checksum_sha256"])
            reference = str(row["text_normalized"])
            speaker = str(row.get("speaker_id", ""))
            audio_uri = next(
                (str(v) for k, v in row.items()
                 if isinstance(v, str) and v.startswith("s3://")
                 and ("audio" in k or "uri" in k)), "")
            if not audio_uri:
                raise SealedRefusal(f"{language}: row {checksum[:12]} "
                                    "names no audio object")
            audio_local = INPUT / "audio" / audio_uri.removeprefix(
                "s3://medzen-speech/")
            if not audio_local.is_file():
                raise SealedRefusal(f"{language}: audio for {checksum[:12]} "
                                    "not delivered by the audio channel")
            if _sha256_file(audio_local) != checksum:
                raise SealedRefusal(f"{language}: audio bytes do not hash "
                                    "to the row identity — refusing")
            audio, sr = sf.read(audio_local, dtype="float32",
                                always_2d=False)
            if getattr(audio, "ndim", 1) > 1:
                audio = audio.mean(axis=1)
            wave = _preprocess_wave(audio, sr).to(
                torch.bfloat16).unsqueeze(0)
            if device is not None:
                wave = wave.to(device)
            baseline_hyp = norm(decode(baseline, wave))
            candidate_hyp = norm(decode(candidate, wave))
            out_lines.append(json.dumps({
                "audio_checksum_sha256": checksum,
                "cluster_id": speaker,
                "reference_text_sha256": hashlib.sha256(
                    reference.encode()).hexdigest(),
                "baseline_hypothesis": baseline_hyp,
                "baseline_hypothesis_sha256": hashlib.sha256(
                    baseline_hyp.encode()).hexdigest(),
                "candidate_hypothesis": candidate_hyp,
                "candidate_hypothesis_sha256": hashlib.sha256(
                    candidate_hyp.encode()).hexdigest(),
                "baseline_errors": scorer.score_errors(
                    reference, baseline_hyp),
                "candidate_errors": scorer.score_errors(
                    reference, candidate_hyp),
                "reference_words": scorer.reference_words(reference),
            }, sort_keys=True))
        payload = ("\n".join(out_lines) + "\n").encode()
        (OUTPUT / f"{language}.rows.jsonl").write_bytes(payload)
        rows_sha[language] = hashlib.sha256(payload).hexdigest()
        print(json.dumps({"status": "SEALED_LANGUAGE_DONE",
                          "language": language, "rows": len(out_lines)},
                         sort_keys=True))

    receipt = {
        "artifact_tree_sha256": tree,
        "decoding_config_sha256": decoding_sha,
        "image_digest": image_digest,
        "job_name": job_name,
        "rows_sha256": rows_sha,
    }
    payload = json.dumps(receipt, indent=1, sort_keys=True).encode() + b"\n"
    (OUTPUT / "inference-receipt.json").write_bytes(payload)
    print(json.dumps({"status": "SEALED_EVAL_COMPLETE",
                      "languages": len(rows_sha),
                      "receipt_sha256":
                          hashlib.sha256(payload).hexdigest()},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
