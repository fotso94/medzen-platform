"""Arm-2 calibration ENTRYPOINT (Codex review #20 F3).

Round 19 wrote a metrics file with `serve=null` and `dev_sentinel_wer=null`
and pointed SageMaker straight at `pipeline.omniasr_train`, so a genuine
calibration was GUARANTEED to fail its own verifier — the promised wrapper
that fills those fields never existed. This module is that wrapper: ONE real
entrypoint that runs the full chain and exits non-zero on any failure.

    train + export  ->  load / readyz  ->  dev-sentinel WER  ->
    finalize metrics (serve, dev_sentinel_wer, identity, samples/s)  ->
    run scripts/verify_arm2_calibration.py  ->  exit(verifier code)

The calibration packet's ContainerArguments point here (`-m
pipeline.omniasr_calibrate`), not at the bare trainer.

HOST SAFETY: importing this module needs no torch. Orchestration, metric
merging, identity binding, dev-manifest parsing and the verifier invocation
are pure and host-tested. The model-touching stages (readyz reload, CTC-greedy
decode) import torch lazily and are validated in the trainer image (C3) — and
they FAIL CLOSED: any exception leaves serve/dev_sentinel_wer unset, so the
verifier refuses and the job exits non-zero. A broken decode can never produce
a false PASS.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from pipeline.omniasr_train import (CALIBRATION_METRICS_FILE, TrainerRefusal,
                                    parse_config)

# scorer identity bound into the evidence (Codex review #20 F5): the decode +
# normalizer + metric this WER was produced with.
SCORER_ID = ("preproc=resample16k+utterance-znorm; "
             "ctc-greedy/seqlen-truncate+argmax+collapse+blank-strip+"
             "skip-special-tokens; "
             "normalizer=pipeline.normalizers.for_language; "
             "metric=corpus-word-error-rate/3")
# the pinned upstream decoder the mandatory in-run parity probe compares against
UPSTREAM_PIPELINE_ID = ("omnilingual_asr.models.inference.pipeline."
                        "ASRInferencePipeline@145a12a6")
VERIFIER_REL = "scripts/verify_arm2_calibration.py"


def word_edits(ref: str, hyp: str) -> tuple[int, int]:
    """(word-level Levenshtein edit distance, reference word count) for ONE
    pair. Pure and host-tested; the per-row receipts store exactly these two
    numbers so the corpus WER is recomputable by anyone (Codex #22: a scalar
    WER without per-row receipts could not be recomputed)."""
    r = ref.split()
    h = hyp.split()
    prev = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        cur = [i] + [0] * len(h)
        for j, hw in enumerate(h, 1):
            cost = 0 if rw == hw else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[len(h)], len(r)


def word_error_rate(refs: list[str], hyps: list[str]) -> float:
    """Corpus word error rate = (sum of word-level Levenshtein edit distances)
    / (total reference words). Pure and host-tested so the dev-sentinel score
    is deterministic and needs no extra dependency in the CUDA image. Matches
    the standard corpus-WER definition (jiwer.wer over paired lists)."""
    if len(refs) != len(hyps):
        raise CalibrationRefusal("refs/hyps length mismatch")
    total_edits = 0
    total_ref_words = 0
    for ref, hyp in zip(refs, hyps):
        edits, ref_words = word_edits(ref, hyp)
        total_edits += edits
        total_ref_words += ref_words
    if total_ref_words == 0:
        raise CalibrationRefusal(
            "dev slice has zero reference words — cannot score WER")
    return total_edits / total_ref_words


class CalibrationRefusal(RuntimeError):
    """Fail-closed: a calibration stage could not produce bound evidence."""


# --------------------------------------------------------------------------
# pure helpers (host-tested)
# --------------------------------------------------------------------------

def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    """Chunked file hash (the export model.pt is ~2.6 GB)."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 22), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_dev_manifest_files(raw: str) -> dict[str, str]:
    """`lang=repo/path.jsonl,lang2=...` -> {lang: path}. The dev slices are
    named by REPO PATH (baked into the image), never by an `eval/...` S3 key —
    the launch request is substring-screened and `eval/` is forbidden there
    (the audio the manifest rows point at is fetched at runtime, off-request)."""
    out: dict[str, str] = {}
    for pair in (raw or "").split(","):
        if not pair.strip():
            continue
        if "=" not in pair:
            raise CalibrationRefusal(
                f"MEDZEN_DEV_SENTINEL_MANIFEST_FILES entry {pair!r} is not "
                "lang=path")
        lang, _, path = pair.partition("=")
        lang = lang.strip().lower()
        path = path.strip()
        if not lang or not path:
            raise CalibrationRefusal(
                f"MEDZEN_DEV_SENTINEL_MANIFEST_FILES entry {pair!r} is empty")
        if path.startswith("/") or ".." in path:
            raise CalibrationRefusal(
                f"dev manifest path {path!r} must be a repo-relative path "
                "without traversal")
        out[lang] = path
    return out


def verify_contract_binding(contract_bytes: bytes, declared_sha: str) -> str:
    """The wrapper's contract gate (Codex #22 blocker 2): the baked execution
    contract's bytes must hash to the launcher-injected declaration, or the
    run refuses — a swapped/patched contract cannot execute. Returns the sha."""
    actual = _sha256_bytes(contract_bytes)
    if actual != str(declared_sha or "").strip():
        raise CalibrationRefusal(
            f"the baked execution contract hashes to {actual[:16]}, the "
            f"launcher declared {str(declared_sha)[:16] or '<absent>'} — "
            "refusing to run an unreviewed contract")
    return actual


def build_identity(*, run_fingerprint: str, training_job_name: str,
                   export: dict[str, Any], dev_manifest_shas: dict[str, str],
                   packet_sha256: str, execution_contract_sha256: str,
                   verifier_script_sha256: str,
                   scorer: str = SCORER_ID) -> dict[str, Any]:
    """The evidence-binding block the verifier requires (Codex review #20 F5):
    which run, export, scorer, dev slices, packet, execution contract and
    verifier produced these numbers. Absent/blank fields make the verifier
    refuse."""
    return {
        "run_fingerprint": run_fingerprint,
        "training_job_name": training_job_name,
        "export_manifest_sha256": export.get("manifest_sha256", ""),
        "export_model_sha256": export.get("checkpoint_sha256")
        or export.get("model_sha256", ""),
        "dev_manifest_shas": dict(sorted(dev_manifest_shas.items())),
        "scorer": scorer,
        "packet_sha256": packet_sha256,
        "execution_contract_sha256": execution_contract_sha256,
        "verifier_script_sha256": verifier_script_sha256,
    }


def patch_metrics(metrics_path: Path, *, serve: dict[str, Any],
                  dev_sentinel_wer: dict[str, Any],
                  identity: dict[str, Any],
                  dev_sentinel_results: dict[str, Any] | None = None,
                  parity: dict[str, Any] | None = None) -> dict[str, Any]:
    """Merge the post-training fields into the trainer's metrics artifact.
    Refuses to invent numbers: the training-side artifact must already exist."""
    if not metrics_path.exists():
        raise CalibrationRefusal(
            f"{metrics_path} was not written by the trainer — cannot finalize "
            "a calibration that produced no training-side metrics")
    metrics = json.loads(metrics_path.read_bytes())
    metrics["serve"] = serve
    metrics["dev_sentinel_wer"] = dev_sentinel_wer
    metrics["identity"] = identity
    if dev_sentinel_results is not None:
        # Codex #22: per-row receipts (audio checksum, normalized hypothesis,
        # edit distance, ref word count) so the corpus WER is RECOMPUTABLE
        # against the committed references — not a bare scalar.
        metrics["dev_sentinel_results"] = dev_sentinel_results
    if parity is not None:
        # Codex #25 finding 2: the digest-bound receipt of the MANDATORY
        # in-run upstream decode parity probe
        metrics["parity"] = parity
    metrics_path.write_bytes(
        json.dumps(metrics, sort_keys=True, separators=(",", ":")).encode()
        + b"\n")
    return metrics


def run_verifier(metrics_path: Path, contract_path: Path,
                 *, bind_packet_sha: bool) -> int:
    """Invoke the canonical verifier in-process against the EXECUTION CONTRACT
    baked in the image (Codex #22 blocker 2). In-image this is a fail-closed
    SMOKE (bind_packet_sha=False: the launch packet — with the image digest —
    cannot exist inside the image, so the committed-packet cross-bind is the
    reviewer's --live run); every other acceptance check runs, including the
    per-row dev receipts recompute. Returns 0 on PASS, 1 on FAIL."""
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "scripts"))
    from verify_arm2_calibration import (load_verifier_spec,
                                         verify_calibration,
                                         verify_dev_row_receipts)

    contract_bytes = contract_path.read_bytes()
    contract = json.loads(contract_bytes)
    metrics = json.loads(metrics_path.read_bytes())
    spec = load_verifier_spec(contract)
    verifier_path = root / VERIFIER_REL
    verifier_sha = _sha256_bytes(verifier_path.read_bytes())
    job_id = str(contract.get("job_id") or "").strip()
    expected_job_name = f"medzen-b5-{job_id}" if job_id else None
    packet_sha = None
    if bind_packet_sha:
        packet_sha = _sha256_bytes(json.dumps(
            contract, sort_keys=True, separators=(",", ":")).encode())
    failures = verify_calibration(
        metrics, spec, packet_canonical_sha=packet_sha,
        verifier_script_sha=verifier_sha, expected_job_name=expected_job_name,
        expected_contract_sha=_sha256_bytes(contract_bytes))
    failures.extend(verify_dev_row_receipts(
        metrics, spec, read_manifest=lambda rel: (root / rel).read_bytes()))
    print(json.dumps({"status": "CALIBRATION_VERIFY",
                      "verdict": "PASS" if not failures else "FAIL",
                      "failures": failures}, sort_keys=True), flush=True)
    return 0 if not failures else 1


# --------------------------------------------------------------------------
# model-touching stages (in-image / C3; fail closed)
# --------------------------------------------------------------------------

def readyz_audit(model) -> dict[str, Any]:
    """Load-and-serve check on the EXPORTED merged model: every parameter is
    finite and NO LoRA/adapter module remains (a merged export must carry no
    adapter residue). Structural — no serving stack needed."""
    import torch

    residue = [name for name, _ in model.named_modules()
               if any(tag in type(_).__name__.lower()
                      for tag in ("lora", "adapter"))]
    weights_finite = all(bool(torch.isfinite(p).all())
                         for p in model.parameters())
    return {"readyz": bool(weights_finite and not residue),
            "adapter_residue": bool(residue),
            "weights_finite": bool(weights_finite)}


def _ctc_greedy_text(logits, decoder, blank_idx: int,
                     valid_frames: int | None = None) -> str:
    """Greedy CTC decode of one utterance's frame logits [T, vocab], matching
    the PINNED upstream OmniASR pipeline (Codex #23): logits are TRUNCATED to
    the encoder output layout's valid frame count (`bl_out.seq_lens`) before
    argmax — padded frames must not vote — then consecutive duplicates
    collapse, the blank drops, and a decoder created with
    skip_special_tokens=True renders the text."""
    import torch

    frames = torch.as_tensor(logits)
    if valid_frames is not None:
        frames = frames[: int(valid_frames)]
    ids = frames.argmax(dim=-1).tolist()
    collapsed: list[int] = []
    prev = None
    for token in ids:
        if token != prev and token != blank_idx:
            collapsed.append(int(token))
        prev = token
    text = decoder(torch.as_tensor(collapsed, dtype=torch.int64))
    return text if isinstance(text, str) else str(text)


def main() -> int:
    config = parse_config(dict(os.environ))
    if not config.kd_enable:
        raise TrainerRefusal(
            "pipeline.omniasr_calibrate is the Arm-2 KD calibration entrypoint; "
            "MEDZEN_KD_ENABLE must be set. A non-KD training run uses "
            "pipeline.omniasr_train directly.")

    # Codex #22 blocker 2: the image bakes the SELF-REFERENCE-FREE execution
    # contract (the launch packet, which carries the image digest, cannot exist
    # inside the image). The launcher injects the contract's in-image path AND
    # its sha; the wrapper refuses a contract whose bytes do not hash to the
    # injected declaration — a swapped contract cannot run.
    packet_path = Path(os.environ["MEDZEN_EXECUTION_CONTRACT"])
    execution_contract_sha256 = verify_contract_binding(
        packet_path.read_bytes(),
        os.environ.get("MEDZEN_EXECUTION_CONTRACT_SHA256", ""))
    dev_files = parse_dev_manifest_files(
        os.environ.get("MEDZEN_DEV_SENTINEL_MANIFEST_FILES", ""))
    packet_sha256 = os.environ.get("MEDZEN_CALIBRATION_PACKET_SHA256", "").strip()
    # the launcher injects the real SageMaker TrainingJobName (medzen-b5-<job_id>)
    # for KD packets; the verifier requires identity.training_job_name to equal
    # the name DERIVED from the packet, so a fabricated file naming another job
    # fails (Codex review #20 F5 follow-up).
    job_name = (os.environ.get("MEDZEN_TRAINING_JOB_NAME")
                or os.environ.get("TRAINING_JOB_NAME") or "").strip()

    # 1. train + export + training-side metrics (byte-identical to the trainer)
    from pipeline.omniasr_train import main as train_main
    rc = train_main()
    if rc != 0:
        print(json.dumps({"status": "CALIBRATION_TRAINING_FAILED",
                          "trainer_exit": rc}, sort_keys=True))
        return rc

    metrics_path = config.output_dir / CALIBRATION_METRICS_FILE
    # bind the export identity to the EXACT authenticated artifact (Codex
    # review #20 F5 follow-up): export_manifest_sha256 is the sha of the raw
    # manifest.json bytes (the reviewer recomputes it from the S3-fetched file),
    # and export_model_sha256 is the model sha that (authenticated) manifest
    # DECLARES — so the verifier can cross-check both against the real export.
    manifest_bytes = (config.output_dir / "export" / "manifest.json").read_bytes()
    export_manifest = json.loads(manifest_bytes)
    provenance = json.loads(
        (config.output_dir / "training-provenance.json").read_bytes())

    # 2. MANDATORY upstream parity on the fresh BASE model (Codex #25) —
    # then reload the export -> readyz; dev-sentinel WER (all fail closed)
    from pipeline.omniasr_train import _load_model_and_tokenizer
    model, tokenizer, device = _load_model_and_tokenizer(config)
    parity = _parity_probe(model, tokenizer, device, dev_files)
    export_ckpt = config.output_dir / "export" / "model.pt"
    # Codex review #21 F3 (in-image half): hash the ACTUAL export bytes and
    # require the manifest's declared model_sha256 to reproduce it — the
    # declared hash is a claim until the artifact itself matches. The recorded
    # identity value is the ACTUAL hash, which the reviewer's authoritative
    # --export-model run recomputes from the S3-fetched file.
    actual_model_sha = _sha256_file(export_ckpt)
    if actual_model_sha != str(export_manifest.get("model_sha256")):
        raise CalibrationRefusal(
            f"export model.pt hashes to {actual_model_sha[:16]}, the manifest "
            f"declares {str(export_manifest.get('model_sha256'))[:16]} — the "
            "export pair is torn; refusing to bind mismatched evidence")
    _load_export_weights(model, export_ckpt)
    serve = readyz_audit(model)

    dev_wer, dev_manifest_shas, dev_results = _score_dev_sentinels(
        config, model, tokenizer, device, dev_files)

    # Codex review #21 F4 (in-image half): the scored slices must BE the
    # predeclared ones — compare each computed manifest sha against the
    # packet's result_verifier.dev_manifests declaration before binding.
    declared = (json.loads(packet_path.read_bytes())
                .get("result_verifier", {}).get("dev_manifests", {}))
    for language, sha in sorted(dev_manifest_shas.items()):
        want = str((declared.get(language) or {}).get("sha256") or "")
        if sha != want:
            raise CalibrationRefusal(
                f"dev slice for {language!r} hashes to {sha[:16]}, the packet "
                f"predeclares {want[:16] or '<absent>'} — refusing to score an "
                "undeclared slice")

    export_identity = {
        # raw manifest.json bytes sha (the reviewer recomputes it from the
        # S3-fetched file) and the ACTUAL model bytes sha (manifest-confirmed)
        "manifest_sha256": _sha256_bytes(manifest_bytes),
        "model_sha256": actual_model_sha,
    }
    identity = build_identity(
        run_fingerprint=provenance.get("run_fingerprint", ""),
        training_job_name=job_name,
        export=export_identity, dev_manifest_shas=dev_manifest_shas,
        packet_sha256=packet_sha256,
        execution_contract_sha256=execution_contract_sha256,
        verifier_script_sha256=_sha256_bytes(
            (Path(__file__).resolve().parents[1] / VERIFIER_REL).read_bytes()))

    # 4. finalize; 5. verify (fail closed)
    patch_metrics(metrics_path, serve=serve, dev_sentinel_wer=dev_wer,
                  identity=identity, dev_sentinel_results=dev_results,
                  parity=parity)
    return run_verifier(metrics_path, packet_path, bind_packet_sha=False)


def _preprocess_wave(audio, sr: int):
    """Upstream-equivalent audio preprocessing (Codex #25 finding 2): the
    pinned OmniASR pipeline resamples to 16 kHz and applies per-utterance
    zero-mean/unit-variance waveform normalization before inference; feeding
    raw audio produced different hypotheses. This mirror is PROVEN equivalent
    by the mandatory in-run parity probe (_parity_probe) — if it ever drifts
    from upstream, parity fails and the calibration refuses."""
    import torch
    import torch.nn.functional as functional

    wave = torch.as_tensor(audio, dtype=torch.float32)
    if int(sr) != 16000:
        import torchaudio.functional as taf
        wave = taf.resample(wave, int(sr), 16000)
    # Codex #26 finding 3: match the pinned upstream EXACTLY — Meta's
    # audio.py uses layer_norm(waveform, waveform.shape) (POPULATION variance,
    # eps 1e-5), not sample variance (wave.var() is unbiased and drifted by
    # ~1.4e-4 on 16k samples, more on shorter inputs).
    return functional.layer_norm(wave, wave.shape, eps=1e-5)


def _parity_probe(model, tokenizer, device, dev_files: dict[str, str],
                  *, rows_per_language: int = 1) -> dict[str, Any]:
    """MANDATORY upstream decode parity (Codex #25 finding 2): on the FRESH
    BASE model (before the export is loaded), decode the first row(s) of each
    dev slice through OUR scorer path AND through the pinned upstream
    ASRInferencePipeline; post-normalization hypotheses must be IDENTICAL.
    Any mismatch refuses the calibration — fail, never skip. Returns the
    digest-bound parity receipt recorded into calibration-metrics.json."""
    import soundfile as sf
    import torch
    from fairseq2.nn import BatchLayout
    from omnilingual_asr.models.inference.pipeline import ASRInferencePipeline

    from pipeline.normalizers import for_language
    from pipeline.omniasr_data import fetch_audio
    from pipeline.train_asr import s3

    model.eval()
    upstream = ASRInferencePipeline(
        model_card="medzen_omniASR_CTC_1B_v2", device=device,
        dtype=torch.bfloat16)
    decoder = tokenizer.create_decoder(skip_special_tokens=True)
    blank_idx = int(getattr(getattr(tokenizer, "vocab_info", None),
                            "pad_idx", 0) or 0)
    cli = s3()
    cache = Path(os.environ.get("MEDZEN_AUDIO_CACHE", "/tmp/medzen-audio-cache"))
    root = Path(__file__).resolve().parents[1]
    rows_checked: dict[str, int] = {}
    row_receipts: dict[str, list] = {}
    for language, rel in sorted(dev_files.items()):
        norm = for_language(language)
        rows = [json.loads(line)
                for line in (root / rel).read_text().splitlines()
                if line.strip()][:rows_per_language]
        row_receipts[language] = []
        for row in rows:
            audio_path = fetch_audio(cli, row, cache)
            audio, sr = sf.read(audio_path, dtype="float32", always_2d=False)
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
            ours = norm(_ctc_greedy_text(
                logits[0], decoder, blank_idx,
                valid_frames=int(out_lens[0]) if out_lens is not None else None))
            theirs = norm(str(upstream.transcribe(
                [str(audio_path)], lang=None, batch_size=1)[0]))
            if ours != theirs:
                raise CalibrationRefusal(
                    f"upstream decode parity FAILED on {language} row "
                    f"{row['audio_checksum_sha256'][:12]}: ours={ours!r} vs "
                    f"upstream={theirs!r} — the scorer does not match the "
                    "pinned pipeline; refusing to score with it")
            # Codex #28 finding 4: record the actual NORMALIZED hypotheses (the
            # dev slices are one short public-research utterance per language)
            # plus their independently-computed hashes, so the verifier
            # RECOMPUTES sha256(text)==hash and requires ours_hyp==upstream_hyp
            # — 64 zeros (or any pair that is not the real hash of a real,
            # matching hypothesis) can no longer pass.
            row_receipts[language].append({
                "audio_checksum_sha256": row["audio_checksum_sha256"],
                "ours_hyp": ours,
                "upstream_hyp": theirs,
                "ours_hyp_sha256": _sha256_bytes(ours.encode()),
                "upstream_hyp_sha256": _sha256_bytes(theirs.encode())})
        rows_checked[language] = len(rows)
    del upstream
    if device is not None and str(device).startswith("cuda"):
        torch.cuda.empty_cache()
    return {"upstream_equal": True, "upstream": UPSTREAM_PIPELINE_ID,
            "rows_checked": rows_checked, "rows": row_receipts,
            "scorer": SCORER_ID}


def _load_export_weights(model, checkpoint_path: Path) -> None:
    """Load the merged full-FT export into the base architecture. Codex review
    #20 F3 follow-up: strict=False silently loaded NOTHING when the keys did not
    map (a renamed/corrupt export), so readyz then reported healthy BASE weights
    and the job PASSED without serving the export. Refuse any missing/unexpected
    key — a non-loading export must fail closed, not serve un-updated weights."""
    import torch
    state = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    result = model.load_state_dict(state.get("model", state), strict=False)
    missing = list(getattr(result, "missing_keys", []) or [])
    unexpected = list(getattr(result, "unexpected_keys", []) or [])
    if missing or unexpected:
        raise CalibrationRefusal(
            f"the export did not map onto the model — {len(missing)} missing / "
            f"{len(unexpected)} unexpected keys (e.g. missing={missing[:3]}, "
            f"unexpected={unexpected[:3]}); refusing to readyz un-updated "
            "base weights as if the export had loaded")
    model.eval()


def _score_dev_sentinels(config, model, tokenizer, device,
                         dev_files: dict[str, str]) -> tuple[dict, dict]:
    """Decode each dev slice and score WER vs its references. Every dev
    language declared in the packet must have a slice here (the verifier
    refuses a missing language), so a slice that fails to load fails the job."""
    import soundfile as sf
    import torch

    from pipeline.normalizers import for_language
    from pipeline.omniasr_data import fetch_audio
    from pipeline.train_asr import s3

    if not dev_files:
        raise CalibrationRefusal(
            "MEDZEN_DEV_SENTINEL_MANIFEST_FILES is empty — the dev-sentinel "
            "slices must be provisioned and bound before calibration")
    blank_idx = int(getattr(getattr(tokenizer, "vocab_info", None),
                            "pad_idx", 0) or 0)
    # decoder parity with the pinned upstream pipeline (Codex #23): special
    # tokens are stripped by the DECODER, not left in the hypothesis
    decoder = tokenizer.create_decoder(skip_special_tokens=True)
    cli = s3()
    cache = Path(os.environ.get("MEDZEN_AUDIO_CACHE", "/tmp/medzen-audio-cache"))
    root = Path(__file__).resolve().parents[1]
    wer_by_lang: dict[str, float] = {}
    manifest_shas: dict[str, str] = {}
    results_by_lang: dict[str, Any] = {}
    for language, rel in sorted(dev_files.items()):
        raw = (root / rel).read_bytes()
        manifest_shas[language] = _sha256_bytes(raw)
        rows = [json.loads(line) for line in raw.decode().splitlines()
                if line.strip()]
        if not rows:
            raise CalibrationRefusal(f"dev slice {rel} for {language} is empty")
        norm = for_language(language)
        refs, hyps = [], []
        row_receipts = []
        for row in rows:
            audio, sr = sf.read(fetch_audio(cli, row, cache),
                                dtype="float32", always_2d=False)
            if getattr(audio, "ndim", 1) > 1:
                audio = audio.mean(axis=1)
            wave = _preprocess_wave(audio, sr).to(torch.bfloat16).unsqueeze(0)
            if device is not None:
                wave = wave.to(device)
            from fairseq2.nn import BatchLayout
            layout = BatchLayout(tuple(wave.shape), seq_lens=[wave.shape[1]],
                                 device=wave.device)
            with torch.no_grad():
                logits, out_layout = model(wave, layout)
            # upstream parity (Codex #23): truncate to the model's RETURNED
            # output length — padded frames must not vote in the argmax
            out_lens = getattr(out_layout, "seq_lens", None)
            valid_frames = int(out_lens[0]) if out_lens is not None else None
            hyp = _ctc_greedy_text(logits[0], decoder, blank_idx,
                                   valid_frames=valid_frames)
            reference = norm(row["text_normalized"])
            hypothesis = norm(hyp)
            refs.append(reference)
            hyps.append(hypothesis)
            # Codex #22: per-row receipt so the corpus WER is RECOMPUTABLE —
            # the committed manifest holds the reference; the receipt holds
            # the hypothesis + the numbers the verifier reproduces from both.
            edits, ref_words = word_edits(reference, hypothesis)
            row_receipts.append({
                "audio_checksum_sha256": row["audio_checksum_sha256"],
                "hyp_normalized": hypothesis,
                "edit_distance": edits,
                "ref_words": ref_words,
            })
        wer_by_lang[language] = round(word_error_rate(refs, hyps), 4)
        results_by_lang[language] = {"rows": row_receipts}
    return wer_by_lang, manifest_shas, results_by_lang


if __name__ == "__main__":
    raise SystemExit(main())
