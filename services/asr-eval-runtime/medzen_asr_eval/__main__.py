"""Container entry point for package qualification and gated inference."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
from pathlib import Path

from .harness import EvaluationRefusal, canonical_json, sha256_bytes, validate_mode, write_once
from .identity import CANDIDATES, SOURCE_IDENTITY


def qualify() -> dict[str, object]:
    versions = {
        name: importlib.metadata.version(name)
        for name in ("fairseq2", "torch", "torchaudio", "faster-whisper")
    }
    import omnilingual_asr

    if omnilingual_asr.__version__ != SOURCE_IDENTITY["omnilingual_internal_version"]:
        raise EvaluationRefusal("Omnilingual internal version differs")
    for name in CANDIDATES:
        validate_mode(name, "unconditioned", None)
    validate_mode("whisper-large-v3", "conditioned", "en")
    validate_mode("omniASR_LLM_1B_v2", "conditioned", "eng_Latn")
    return {
        "status": "PASS_LOCAL_RUNTIME_IMPORT_QUALIFICATION",
        "candidates": sorted(CANDIDATES),
        "source_identity": SOURCE_IDENTITY,
        "versions": versions,
        "uid": os.getuid(),
        "gid": os.getgid(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("qualify")
    network = subparsers.add_parser("network-probe")
    network.add_argument("--binding", type=Path, required=True)
    network.add_argument("--receipt", type=Path, required=True)
    pilot = subparsers.add_parser("pilot")
    pilot.add_argument("--rows", type=Path, required=True)
    pilot.add_argument("--model-root", type=Path, default=Path("/models"))
    pilot.add_argument("--model-binding", type=Path, required=True)
    pilot.add_argument("--conditioning", type=Path)
    pilot.add_argument("--receipt-root", type=Path, required=True)
    pilot.add_argument("--aggregate-receipt", type=Path, required=True)
    transcribe = subparsers.add_parser("transcribe")
    transcribe.add_argument("--candidate", required=True)
    transcribe.add_argument("--mode", required=True)
    transcribe.add_argument("--language-id")
    transcribe.add_argument("--audio", type=Path, required=True)
    transcribe.add_argument("--audio-sha256", required=True)
    transcribe.add_argument("--model-root", type=Path, default=Path("/models"))
    transcribe.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "qualify":
            result = qualify()
        elif args.command == "network-probe":
            from .network_probe import probe_network

            result = probe_network(args.binding, args.receipt)
        elif args.command == "pilot":
            from .pilot import run_pilot

            result = run_pilot(
                rows_path=args.rows,
                model_root=args.model_root,
                model_binding_path=args.model_binding,
                conditioning_path=args.conditioning,
                receipt_root=args.receipt_root,
                aggregate_path=args.aggregate_receipt,
            )
        else:
            from .backends import load_backend

            validate_mode(args.candidate, args.mode, args.language_id)
            audio = args.audio.read_bytes()
            if sha256_bytes(audio) != args.audio_sha256:
                raise EvaluationRefusal("audio SHA-256 mismatch")
            backend = load_backend(
                args.candidate, args.mode, args.language_id, args.model_root
            )
            transcript = backend.transcribe(args.audio, args.language_id)
            result = {
                "status": "PASS_ROW_INFERENCE",
                "candidate": args.candidate,
                "mode": args.mode,
                "language_id": args.language_id,
                "audio_sha256": args.audio_sha256,
                "prediction": transcript.text,
                "eos_observed": transcript.eos_observed,
                "cap_hit": transcript.cap_hit,
                "termination_evidence": transcript.termination_evidence,
            }
            write_once(args.receipt, result)
    except (EvaluationRefusal, FileExistsError) as exc:
        print(
            canonical_json({"status": "REFUSED", "reason": str(exc)}).decode(),
            end="",
        )
        return 2
    print(canonical_json(result).decode(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
