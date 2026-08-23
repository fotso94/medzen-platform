#!/usr/bin/env python3
"""Assemble, COMPLETELY verify, sign, and runtime-verify a promotion
bundle — the ONE deterministic admission path (Codex round 13: the
workflow signed only the receipt, no assembler existed, and the loader's
signed-root design had never been exercised end to end).

  python scripts/assemble_promotion_bundle.py \\
      --gate-report ... --results-dir ... --candidate-packet ... \\
      --manifests-dir ... --artifact-tree <64hex> --anchor-envelope ... \\
      --independent-review ... --owner-authorization ... \\
      --output-dir bundle/ [--sign] [--signer <cmd>]

Order (each step fails closed):
  1. ADMISSION GATE — verify_complete_promotion with LIVE AWS fetchers
     (anchor chronology, the complete sealed-job contract, the job's own
     immutable output objects + inference receipt); produces the
     attested admission receipt.
  2. LAYOUT — flat bundle: protocol pointer + protocol, gate report,
     packet, envelope, receipt, grade authority, holdout bindings (no
     grades), rows + sealed manifests, licence/reservation documents,
     independent review, promotion record; bundle.json index.
  3. SIGN (--sign) — the grade authority per-document and the evidence
     ROOT (bundle.json) with the KMS promotion key; the signer validates
     its own authority (account, alias, committed public key).
  4. RUNTIME VERIFY — the loader's OWN offline verifier runs against the
     assembled bundle with the baked/committed public key. A bundle this
     step refuses is deleted, never published.
Emits <output-dir>/ADMISSION-PINS.json with every deployment pin.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/model-loader"))
sys.path.insert(0, str(ROOT / "scripts"))

from medzen_model_loader.promotion_check import (  # noqa: E402
    PromotionCheckRefusal, verify_complete_promotion,
    verify_packet_chronology)
import b7_model_promotion_check as admission  # noqa: E402


GRADES_PATH = ROOT / "platform/decisions/HOLDOUT-GRADES-2026-001.json"
POINTER_PATH = ROOT / "platform/decisions/CURRENT-PROMOTION-PROTOCOL.json"


def _sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _flat(path: str) -> str:
    return str(path).lstrip("/").replace("/", "__")


def main() -> int:
    parser = argparse.ArgumentParser()
    for flag in ("--gate-report", "--results-dir", "--candidate-packet",
                 "--manifests-dir", "--anchor-envelope",
                 "--independent-review", "--owner-authorization",
                 "--output-dir"):
        parser.add_argument(flag, type=Path, required=True)
    parser.add_argument("--artifact-tree", required=True)
    parser.add_argument("--sign", action="store_true",
                        help="sign authority + root with the KMS key and "
                             "run the runtime verifier (admission env)")
    parser.add_argument("--signer", default=None,
                        help="override signer command (tests inject a "
                             "local ECDSA signer); default: "
                             "scripts/sign_promotion_document.py")
    args = parser.parse_args()
    out = args.output_dir
    if out.exists() and any(out.iterdir()):
        print(json.dumps({"status": "REFUSED",
                          "detail": f"output dir {out} is not empty — "
                                    "bundles are assembled once, never "
                                    "overwritten"}))
        return 1
    out.mkdir(parents=True, exist_ok=True)
    try:
        report = admission.load_gate_report(args.gate_report)
        report_bytes = args.gate_report.read_bytes()
        packet_bytes = args.candidate_packet.read_bytes()
        packet = json.loads(packet_bytes)
        envelope_bytes = args.anchor_envelope.read_bytes()
        envelope = json.loads(envelope_bytes)
        tree = str(args.artifact_tree).lower()

        def rows_bytes(label):
            path = args.results_dir / f"{label}.rows.jsonl"
            return path.read_bytes() if path.is_file() else None

        def manifest_bytes(label):
            path = args.manifests_dir / f"{label}.holdout-manifest.jsonl"
            return path.read_bytes() if path.is_file() else None

        receipt_holder: dict = {}

        def verify_chronology() -> None:
            receipt_holder["receipt"] = verify_packet_chronology(
                report, anchor_envelope=envelope, packet_bytes=packet_bytes,
                candidate_packet=packet,
                anchor_fetch=admission._anchor_fetch,
                sealed_start_fetch=admission._sealed_start_fetch,
                artifact_tree_sha256=tree, rows_bytes=rows_bytes,
                output_object_fetch=admission._output_object_fetch)

        # ---- 1. admission gate (live)
        protocol = admission._protocol_record()
        states = verify_complete_promotion(
            report, protocol=protocol,
            holdouts_by_language=admission._authoritative_holdouts_by_language(),
            grade_authority=admission._grade_authority(),
            licensed_code_switch_sets=admission._licensed_code_switch_sets(),
            candidate_packet=packet, packet_bytes=packet_bytes,
            anchor_envelope=envelope, artifact_tree_sha256=tree,
            rows_bytes=rows_bytes, manifest_bytes=manifest_bytes,
            verify_chronology=verify_chronology,
            document_bytes=admission._document_bytes)
        receipt = receipt_holder["receipt"]

        # ---- 2. layout
        files: dict[str, bytes] = {}
        pointer_path = POINTER_PATH
        pointer = json.loads(pointer_path.read_bytes())
        protocol_rel = str(pointer["file"])
        files["CURRENT-PROMOTION-PROTOCOL.json"] = pointer_path.read_bytes()
        files[protocol_rel.rsplit("/", 1)[-1]] = (ROOT / protocol_rel).read_bytes()
        files["T6-GATE-REPORT.json"] = report_bytes
        files["CANDIDATE-PACKET.json"] = packet_bytes
        files["ANCHOR-ENVELOPE.json"] = envelope_bytes
        files["ADMISSION-RECEIPT.json"] = json.dumps(
            receipt, indent=1, sort_keys=True).encode() + b"\n"
        files["HOLDOUT-GRADES.json"] = GRADES_PATH.read_bytes()
        holdouts = admission._authoritative_holdouts_by_language()
        files["HOLDOUT-BINDINGS.json"] = json.dumps(
            {language: [{"sha256": sha} for sha in sorted(shas)]
             for language, shas in sorted(holdouts.items())},
            indent=1, sort_keys=True).encode() + b"\n"
        labels = list(protocol.get("mandatory_languages", [])) + ["code_switch"]
        for label in labels:
            rows = rows_bytes(label)
            manifest = manifest_bytes(label)
            if rows is None or manifest is None:
                raise PromotionCheckRefusal(
                    f"{label}: rows or sealed manifest missing at layout "
                    "— the gate consumed them, they must ship")
            files[f"{label}.rows.jsonl"] = rows
            files[f"{label}.holdout-manifest.jsonl"] = manifest
        registry = admission._licensed_code_switch_sets()
        cs_entry = registry[str(packet["code_switch"]["set"])]
        for field in ("license_record", "reservation_ledger_entry"):
            body = admission._document_bytes(cs_entry[field])
            if body is None:
                raise PromotionCheckRefusal(f"{field} vanished at layout")
            files[_flat(cs_entry[field])] = body
        review_bytes = args.independent_review.read_bytes()
        review = json.loads(review_bytes)
        if review.get("status") != "PASS":
            raise PromotionCheckRefusal(
                "independent review is not PASS — no bundle")
        files["INDEPENDENT-REVIEW.json"] = review_bytes
        authorization = json.loads(args.owner_authorization.read_bytes())
        if tree[:12] not in str(authorization.get("statement", "")):
            raise PromotionCheckRefusal(
                "owner authorization does not name this artifact tree")
        shas = {name: _sha(body) for name, body in files.items()}
        record = {
            "protocol": pointer["record"],
            "decision": "APPROVED",
            "artifact_sha256": tree,
            "gate_report": {"record": "T6-GATE-REPORT.json",
                            "record_sha256": shas["T6-GATE-REPORT.json"]},
            "candidate_packet": {"record": "CANDIDATE-PACKET.json",
                                 "record_sha256": shas["CANDIDATE-PACKET.json"]},
            "anchor_envelope": {"record": "ANCHOR-ENVELOPE.json",
                                "record_sha256": shas["ANCHOR-ENVELOPE.json"]},
            "admission_receipt": {"record": "ADMISSION-RECEIPT.json",
                                  "record_sha256": shas["ADMISSION-RECEIPT.json"]},
            "independent_review": {"record": "INDEPENDENT-REVIEW.json",
                                   "record_sha256": shas["INDEPENDENT-REVIEW.json"]},
            "owner_authorization": authorization,
            "languages": states,
        }
        files["PROMOTION-RECORD.json"] = json.dumps(
            record, indent=1, sort_keys=True).encode() + b"\n"
        shas["PROMOTION-RECORD.json"] = _sha(files["PROMOTION-RECORD.json"])
        for name, body in files.items():
            if "/" in name or ".." in name or name.endswith(
                    (".py", ".pyc", ".so", ".sh")):
                raise PromotionCheckRefusal(f"unsafe bundle entry {name!r}")
            (out / name).write_bytes(body)
        signer = ([sys.executable, str(ROOT / "scripts/sign_promotion_document.py")]
                  if args.signer is None else args.signer.split())
        if args.sign:
            # ---- 3a. the grade authority is signed PER DOCUMENT and its
            # signature is part of the indexed evidence
            subprocess.run(signer + [str(out / "HOLDOUT-GRADES.json")],
                           check=True)
            shas["HOLDOUT-GRADES.json.sig"] = _sha(
                (out / "HOLDOUT-GRADES.json.sig").read_bytes())
        index_bytes = json.dumps(
            {"files": shas, "record": "PROMOTION-RECORD.json"},
            indent=1, sort_keys=True).encode() + b"\n"
        (out / "bundle.json").write_bytes(index_bytes)
        pins = {"artifact_tree_sha256": tree,
                "MEDZEN_PROMOTION_BUNDLE_SHA256": _sha(index_bytes),
                "MEDZEN_HOLDOUT_GRADES_SHA256": shas["HOLDOUT-GRADES.json"],
                "languages": states, "signed": False,
                "runtime_verified": False}

        # ---- 3b. sign the evidence ROOT last + 4. runtime verify
        if args.sign:
            subprocess.run(signer + [str(out / "bundle.json")], check=True)
            pins["signed"] = True
            # the loader's OWN verifier, in-process (identical code to
            # the image; the public key resolves from the committed pem)
            from medzen_model_loader.loader_v2 import (
                LoaderV2Refusal, _verify_promotion_bundle)
            saved = {k: os.environ.get(k) for k in (
                "MEDZEN_PROMOTION_BUNDLE_DIR",
                "MEDZEN_PROMOTION_BUNDLE_SHA256",
                "MEDZEN_HOLDOUT_GRADES_SHA256")}
            os.environ.update({
                "MEDZEN_PROMOTION_BUNDLE_DIR": str(out),
                "MEDZEN_PROMOTION_BUNDLE_SHA256": pins[
                    "MEDZEN_PROMOTION_BUNDLE_SHA256"],
                "MEDZEN_HOLDOUT_GRADES_SHA256": pins[
                    "MEDZEN_HOLDOUT_GRADES_SHA256"]})
            try:
                _verify_promotion_bundle(tree)
            except LoaderV2Refusal as exc:
                shutil.rmtree(out)
                raise PromotionCheckRefusal(
                    "the RUNTIME verifier refused the assembled bundle — "
                    f"deleted: {exc}") from exc
            finally:
                for key, value in saved.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
            pins["runtime_verified"] = True
        (out / "ADMISSION-PINS.json").write_text(
            json.dumps(pins, indent=1, sort_keys=True) + "\n")
    except PromotionCheckRefusal as exc:
        print(json.dumps({"status": "REFUSED", "detail": str(exc)}))
        return 1
    print(json.dumps({"status": "ASSEMBLED", **pins}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
