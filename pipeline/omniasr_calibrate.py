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
SCORER_ID = ("ctc-greedy/argmax+collapse+blank-strip; "
             "normalizer=pipeline.normalizers.for_language; "
             "metric=corpus-word-error-rate/1")
VERIFIER_REL = "scripts/verify_arm2_calibration.py"


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
        r = ref.split()
        h = hyp.split()
        total_ref_words += len(r)
        # word-level Levenshtein
        prev = list(range(len(h) + 1))
        for i, rw in enumerate(r, 1):
            cur = [i] + [0] * len(h)
            for j, hw in enumerate(h, 1):
                cost = 0 if rw == hw else 1
                cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            prev = cur
        total_edits += prev[len(h)]
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


def build_identity(*, run_fingerprint: str, training_job_name: str,
                   export: dict[str, Any], dev_manifest_shas: dict[str, str],
                   packet_sha256: str, verifier_script_sha256: str,
                   scorer: str = SCORER_ID) -> dict[str, Any]:
    """The evidence-binding block the verifier requires (Codex review #20 F5):
    which run, export, scorer, dev slices, packet and verifier produced these
    numbers. Absent/blank fields make the verifier refuse."""
    return {
        "run_fingerprint": run_fingerprint,
        "training_job_name": training_job_name,
        "export_manifest_sha256": export.get("manifest_sha256", ""),
        "export_model_sha256": export.get("checkpoint_sha256")
        or export.get("model_sha256", ""),
        "dev_manifest_shas": dict(sorted(dev_manifest_shas.items())),
        "scorer": scorer,
        "packet_sha256": packet_sha256,
        "verifier_script_sha256": verifier_script_sha256,
    }


def patch_metrics(metrics_path: Path, *, serve: dict[str, Any],
                  dev_sentinel_wer: dict[str, Any],
                  identity: dict[str, Any]) -> dict[str, Any]:
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
    metrics_path.write_bytes(
        json.dumps(metrics, sort_keys=True, separators=(",", ":")).encode()
        + b"\n")
    return metrics


def run_verifier(metrics_path: Path, packet_path: Path,
                 *, bind_packet_sha: bool) -> int:
    """Invoke the canonical verifier in-process. In-image this is a fail-closed
    SMOKE (bind_packet_sha=False: the in-image packet is the pre-pin DRAFT, so
    the committed-packet cross-bind is left to the reviewer's out-of-image
    run); every other acceptance check runs. Returns 0 on PASS, 1 on FAIL."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from verify_arm2_calibration import (load_verifier_spec, verify_calibration)

    packet = json.loads(packet_path.read_bytes())
    metrics = json.loads(metrics_path.read_bytes())
    spec = load_verifier_spec(packet)
    verifier_path = Path(__file__).resolve().parents[1] / VERIFIER_REL
    verifier_sha = _sha256_bytes(verifier_path.read_bytes())
    job_id = str(packet.get("job_id") or "").strip()
    expected_job_name = f"medzen-b5-{job_id}" if job_id else None
    packet_sha = None
    if bind_packet_sha:
        packet_sha = _sha256_bytes(json.dumps(
            packet, sort_keys=True, separators=(",", ":")).encode())
    failures = verify_calibration(
        metrics, spec, packet_canonical_sha=packet_sha,
        verifier_script_sha=verifier_sha, expected_job_name=expected_job_name)
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


def _ctc_greedy_text(logits, tokenizer, blank_idx: int) -> str:
    """Greedy CTC decode of one utterance's frame logits [T, vocab]: argmax
    per frame, collapse consecutive duplicates, drop the blank, decode."""
    import torch

    ids = torch.as_tensor(logits).argmax(dim=-1).tolist()
    collapsed: list[int] = []
    prev = None
    for token in ids:
        if token != prev and token != blank_idx:
            collapsed.append(int(token))
        prev = token
    decoder = tokenizer.create_decoder()
    text = decoder(torch.as_tensor(collapsed, dtype=torch.int64))
    return text if isinstance(text, str) else str(text)


def main() -> int:
    config = parse_config(dict(os.environ))
    if not config.kd_enable:
        raise TrainerRefusal(
            "pipeline.omniasr_calibrate is the Arm-2 KD calibration entrypoint; "
            "MEDZEN_KD_ENABLE must be set. A non-KD training run uses "
            "pipeline.omniasr_train directly.")

    packet_path = Path(os.environ["MEDZEN_CALIBRATION_PACKET"])
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

    # 2. reload the export -> readyz; 3. dev-sentinel WER (both fail closed)
    from pipeline.omniasr_train import _load_model_and_tokenizer
    model, tokenizer, device = _load_model_and_tokenizer(config)
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

    dev_wer, dev_manifest_shas = _score_dev_sentinels(
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
        verifier_script_sha256=_sha256_bytes(
            (Path(__file__).resolve().parents[1] / VERIFIER_REL).read_bytes()))

    # 4. finalize; 5. verify (fail closed)
    patch_metrics(metrics_path, serve=serve, dev_sentinel_wer=dev_wer,
                  identity=identity)
    return run_verifier(metrics_path, packet_path, bind_packet_sha=False)


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
    cli = s3()
    cache = Path(os.environ.get("MEDZEN_AUDIO_CACHE", "/tmp/medzen-audio-cache"))
    root = Path(__file__).resolve().parents[1]
    wer_by_lang: dict[str, float] = {}
    manifest_shas: dict[str, str] = {}
    for language, rel in sorted(dev_files.items()):
        raw = (root / rel).read_bytes()
        manifest_shas[language] = _sha256_bytes(raw)
        rows = [json.loads(line) for line in raw.decode().splitlines()
                if line.strip()]
        if not rows:
            raise CalibrationRefusal(f"dev slice {rel} for {language} is empty")
        norm = for_language(language)
        refs, hyps = [], []
        for row in rows:
            audio, sr = sf.read(fetch_audio(cli, row, cache),
                                dtype="float32", always_2d=False)
            if getattr(audio, "ndim", 1) > 1:
                audio = audio.mean(axis=1)
            wave = torch.from_numpy(audio).to(torch.bfloat16).unsqueeze(0)
            if device is not None:
                wave = wave.to(device)
            from fairseq2.nn import BatchLayout
            layout = BatchLayout(tuple(wave.shape), seq_lens=[wave.shape[1]],
                                 device=wave.device)
            with torch.no_grad():
                logits, _ = model(wave, layout)
            hyp = _ctc_greedy_text(logits[0], tokenizer, blank_idx)
            refs.append(norm(row["text_normalized"]))
            hyps.append(norm(hyp))
        wer_by_lang[language] = round(word_error_rate(refs, hyps), 4)
    return wer_by_lang, manifest_shas


if __name__ == "__main__":
    raise SystemExit(main())
