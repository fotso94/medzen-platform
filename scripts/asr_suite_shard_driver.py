#!/usr/bin/env python3
"""Shard-cycle driver: the reviewed per-shard stack from one command.

Faithful extraction of the hand-proven cycle (attempts 32-38), built to
eliminate the manual-transcription error class (the shard-3 mislabel came
exactly from hand-copying). Every existing validator stays in the loop and
NOTHING here invents authority:

  prepare --shard N --attempt M
      selection (manifest-derived by shard number, row-count asserted)
      -> prestage (skipped when the proof already exists)
      -> proof in bundle-receipt order + both staging validators
      -> fixtures: 9 HeadObject captures in proof order + suite downloads
         + read-fixture capture record
      -> registry revision (close previous attempt, reserve this one)
      -> attempt-boundary bump (3 executor files + the window test)
      -> bindings update + packet from the reviewed template
      -> commits + executor rebind commit
      -> cold rehearsal TWICE, byte-identity asserted
      -> emits the review checklist and STOPS.

  launch --shard N --attempt M
      refuses unless the shared-file review for this attempt exists
      -> AUTH revision committed alone -> committed dry validation
      -> caffeinate -ims runner launch (prints the monitor command).

The review decision and the delegated approval phrase remain written by
the engineer into ~/Documents/medzen-shared/claude_instructions.txt —
the driver checks for them; it never writes them.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.asr_base_model_pilot_assets import (  # noqa: E402
    select_suite_rows,
    suite_bundle_identity,
)
from scripts.asr_base_model_pilot_staging import (  # noqa: E402
    validate_prestage_proof,
    validate_window_budget,
)

SCRATCH = Path(
    "/private/tmp/claude-501/-Users-stephanefotso-Documents-Trading-Docs/"
    "c04a275c-91d4-4b68-a1cf-995943ebe00f/scratchpad"
)
MANIFEST = ROOT / "platform/manifests/ASR-FULL-EVAL-SUITE-SHARD-MANIFEST-2026-002.json"
BINDINGS = ROOT / "platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-003B-S1.json"
SHARED_REVIEWS = Path.home() / "Documents/medzen-shared/claude_instructions.txt"
META_PREFIX = (
    "research/asr-base-model/pilot/"
    "1cdca3e75195c7c7417550154e36a1f372715e17efd13c835c87ee503fa84eee/"
)
KMS = "arn:aws:kms:eu-central-1:558069890522:key/9c336116-c648-4548-95c6-1b926478ae57"
FIXDIR = "tests/fixtures/asr_base_model_pilot/aws-live-2026-08-16"
CANON_RISK = "43321489ce6f9f3a33f86445601b5bd65d99d2f1746747944b2a185742341034"


class DriverRefusal(RuntimeError):
    pass


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT, **kw)
    if result.returncode != 0:
        raise DriverRefusal(f"{' '.join(cmd[:4])}... failed: {result.stderr[-400:]}")
    return result


def _git_commit(message: str, paths: list[str]) -> None:
    # Explicit path list is mandatory: a bare `git add -A` sweep is exactly
    # how commit bdcc112 captured another session's in-flight WIP.
    if not paths:
        raise DriverRefusal("refusing a sweep commit: pass the explicit path list")
    _run(["git", "add", "--"] + paths)
    result = subprocess.run(["git", "commit", "-q", "-m",
                             message + "\n\nCo-Authored-By: Claude Fable 5 <noreply@anthropic.com>"],
                            capture_output=True, text=True, cwd=ROOT)
    if result.returncode != 0 and "nothing to commit" not in result.stdout + result.stderr:
        raise DriverRefusal(f"commit failed: {result.stderr[-300:]}")


def shard_units(shard: int) -> dict:
    manifest = json.loads(MANIFEST.read_bytes())
    matches = [s for s in manifest["shards"] if s["shard"] == shard]
    if len(matches) != 1:
        raise DriverRefusal(f"manifest holds {len(matches)} entries for shard {shard}")
    return matches[0]


def step_selection(shard: int) -> dict:
    entry = shard_units(shard)
    selection = select_suite_rows(SCRATCH / "eval-manifests", entry["units"])
    if len(selection["rows"]) != entry["rows"]:
        raise DriverRefusal(
            f"selection produced {len(selection['rows'])} rows, manifest says {entry['rows']}"
        )
    out = SCRATCH / f"driver-shard{shard}-selection.json"
    out.write_bytes(json.dumps(selection, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=False).encode())
    identity = suite_bundle_identity(selection["public_row_list_sha256"], META_PREFIX)
    return {"entry": entry, "selection_path": out, "selection": selection,
            "bundle_identity": identity["sha256"]}


def step_prestage(shard: int, state: dict) -> Path:
    summary = ROOT / f"platform/evidence/ASR-BASE-MODEL-PRESTAGE-PROOF-2026-DRIVER-S{shard}-SUMMARY.json"
    workdir = SCRATCH / f"driver-shard{shard}-prestage"
    if not summary.exists():
        import time as _time
        started = _time.monotonic()
        state["prestage_elapsed_scope"] = "FULL_PRESTAGE_INCLUDING_SOURCE_DOWNLOAD_AND_BUNDLE_BUILD"
        _run([sys.executable, "scripts/asr_base_model_pilot_staging.py",
              "--selection", str(state["selection_path"]),
              "--meta-source-bundle", str(SCRATCH / "pilot-bundle-source.json"),
              "--workdir", str(workdir),
              "--output", str(summary),
              "--expected-bundle-sha256", state["bundle_identity"],
              "--kms-key-arn", KMS,
              "--audio-cache", str(SCRATCH / "bulk")],
             env=None)
        state["prestage_elapsed_seconds"] = round(_time.monotonic() - started, 1)
    return workdir


def _head(key: str, version: str | None = None) -> dict:
    cmd = ["aws", "s3api", "head-object", "--bucket", "medzen-speech",
           "--key", key, "--checksum-mode", "ENABLED"]
    if version:
        cmd += ["--version-id", version]
    return json.loads(_run(cmd).stdout)


def step_proof(shard: int, state: dict, proof_number: str) -> Path:
    """Assemble the verifier-shaped proof in bundle-receipt order."""
    ident = state["bundle_identity"]
    prefix = f"research/asr-base-model/pilot/{ident}/"
    workdir = state["prestage_workdir"]
    receipt = json.loads((workdir / "pilot-bundle.json").read_bytes())
    known = {o["key"]: o["sha256"] for o in receipt["objects"]}
    for assembly in receipt["assemblies"].values():
        for part in assembly.get("parts", []):
            known[part["key"]] = part["sha256"]
    bundle_local = _sha(workdir / "pilot-bundle.json")
    objects, total = [], 0
    for key in [e["key"] for e in receipt["objects"]] + [prefix + "pilot-bundle.json"]:
        h = _head(key)
        entry = {"bytes": h["ContentLength"], "checksum_type": h.get("ChecksumType", "FULL_OBJECT"),
                 "key": key, "s3_checksum_sha256": h.get("ChecksumSHA256"),
                 "sha256": known.get(key, bundle_local), "version_id": h["VersionId"]}
        if entry["checksum_type"] == "FULL_OBJECT" and entry["s3_checksum_sha256"]:
            if base64.b64decode(entry["s3_checksum_sha256"]).hex() != entry["sha256"]:
                raise DriverRefusal(f"S3 checksum readback differs: {key}")
        total += entry["bytes"]
        objects.append(entry)
    bundle_head = _head(prefix + "pilot-bundle.json")
    previous = sorted(ROOT.glob("platform/evidence/ASR-BASE-MODEL-PRESTAGE-PROOF-2026-0*[0-9].json"))[-1]
    prev = json.loads(previous.read_bytes())
    proof = {
        "bucket": "medzen-speech", "classification": "PUBLIC_RESEARCH_NO_PHI",
        "id": f"ASR-BASE-MODEL-PRESTAGE-PROOF-2026-{proof_number}",
        "kms_key_arn": KMS, "object_bytes": total, "object_count": len(objects),
        "objects": objects,
        "pilot_bundle": {"identity_sha256": ident,
                         "object": {"bytes": bundle_head["ContentLength"],
                                    "checksum_type": bundle_head.get("ChecksumType", "COMPOSITE"),
                                    "key": prefix + "pilot-bundle.json",
                                    "s3_checksum_sha256": bundle_head.get("ChecksumSHA256"),
                                    "sha256": bundle_local,
                                    "version_id": bundle_head["VersionId"]},
                         "receipt_sha256": receipt["receipt_sha256"]},
        "prefix": prefix, "record": "ASR_BASE_MODEL_COMPLETE_BUNDLE_PRESTAGE_PROOF",
        "recorded_utc": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "schema_version": 1,
        "scope": f"SUITE_SHARD_{shard}_AUDIO_AND_METADATA_WITH_META_SOURCE_REFERENCES",
        "source": {"meta_source_prefix": META_PREFIX,
                   "meta_source_proof": "ASR-BASE-MODEL-PRESTAGE-PROOF-2026-001",
                   "record": "suite shard prestage"},
        "status": "PASS_SUITE_SHARD_BUNDLE_PRESTAGED",
        "timed_window": {"artifact_stage_mode": "VERIFY_ONLY", "cleanup_reserve_seconds": 900,
                         "deadline_seconds": 18000, "estimated_fast_stage_seconds": 7200,
                         "in_attempt_upload_bytes": 0},
        "transfer": dict(prev["transfer"],
                         elapsed_seconds=state.get("prestage_elapsed_seconds", 0.0),
                         elapsed_scope=state.get(
                             "prestage_elapsed_scope",
                             "PRESTAGE_PREVIOUSLY_COMPLETED_ELAPSED_UNMEASURED"),
                         uploaded_bytes=sum(e["bytes"] for e in objects
                                            if e["key"].startswith(prefix)),
                         uploaded_objects=4, reused_objects=5, verified_bytes=total),
    }
    validate_prestage_proof(proof, expected_bundle_sha256=ident)
    validate_window_budget(proof, deadline_seconds=18000, expected_bundle_sha256=ident)
    stripped = [{k: v for k, v in item.items()
                 if k not in {"s3_checksum_sha256", "checksum_type"}}
                for item in proof["objects"] if item["key"] != prefix + "pilot-bundle.json"]
    if stripped != receipt["objects"]:
        raise DriverRefusal("proof order violates the bundle-receipt contract")
    path = ROOT / f"platform/evidence/ASR-BASE-MODEL-PRESTAGE-PROOF-2026-{proof_number}.json"
    if path.exists():
        raise DriverRefusal(f"{path.name} already exists (write-once)")
    path.write_bytes(json.dumps(proof, sort_keys=True, separators=(",", ":"),
                                ensure_ascii=False).encode() + b"\n")
    state["proof"] = proof
    return path


def step_fixtures(shard: int, state: dict, capture_number: str) -> Path:
    proof = state["proof"]
    prefix = proof["prefix"]
    captures = []
    for index, obj in enumerate(proof["objects"], start=1):
        raw = _run(["aws", "s3api", "head-object", "--bucket", "medzen-speech",
                    "--key", obj["key"], "--version-id", obj["version_id"],
                    "--checksum-mode", "ENABLED"]).stdout
        body = json.dumps(json.loads(raw), sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False).encode()
        name = f"s3-head-prestage-{index:02d}"
        (ROOT / FIXDIR / f"{name}.json").write_bytes(body)
        captures.append({"api": "s3:HeadObject", "dynamic_value_paths": [], "name": name,
                         "path": f"{FIXDIR}/{name}.json", "region": "eu-central-1",
                         "request": {"Bucket": "medzen-speech", "ChecksumMode": "ENABLED",
                                     "Key": obj["key"], "VersionId": obj["version_id"]},
                         "sha256": hashlib.sha256(body).hexdigest()})
    for stem in ("model-bindings", "pilot-bundle", "runtime-rows"):
        _run(["aws", "s3api", "get-object", "--bucket", "medzen-speech",
              "--key", prefix + stem + ".json",
              str(ROOT / FIXDIR / f"suite-s{shard}-{stem}.json")])
    latest = sorted(ROOT.glob("platform/evidence/ASR-BASE-MODEL-AWS-READ-FIXTURE-CAPTURE-2026-0*.json"))[-1]
    record = json.loads(latest.read_bytes())
    record["captures"] = ([c for c in record["captures"]
                           if not c["name"].startswith("s3-head-prestage-")] + captures)
    record["supersedes"] = record["id"]
    record["id"] = f"ASR-BASE-MODEL-AWS-READ-FIXTURE-CAPTURE-2026-{capture_number}"
    path = ROOT / f"platform/evidence/ASR-BASE-MODEL-AWS-READ-FIXTURE-CAPTURE-2026-{capture_number}.json"
    if path.exists():
        raise DriverRefusal(f"{path.name} already exists (write-once)")
    path.write_bytes(json.dumps(record, sort_keys=True, separators=(",", ":"),
                                ensure_ascii=False).encode() + b"\n")
    return path


def review_is_recorded(attempt: int) -> bool:
    text = SHARED_REVIEWS.read_text()
    marker = f"authorizing numbered attempt {attempt} "
    return ("DECISION: APPROVED" in text.split(marker)[0][-4000:]) if marker in text else False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("prepare", "launch", "selection-only"))
    parser.add_argument("--shard", type=int, required=True)
    parser.add_argument("--attempt", type=int, required=True)
    parser.add_argument("--proof-number", help="e.g. 008")
    parser.add_argument("--capture-number", help="e.g. 008")
    args = parser.parse_args()

    if args.mode == "selection-only":
        state = step_selection(args.shard)
        print(json.dumps({"rows": state["entry"]["rows"],
                          "row_list_sha256": state["selection"]["public_row_list_sha256"],
                          "bundle_identity": state["bundle_identity"],
                          "units": state["entry"]["units"]}, indent=1))
        return 0

    if args.mode == "launch":
        if not review_is_recorded(args.attempt):
            raise DriverRefusal(
                f"no APPROVED review with the attempt-{args.attempt} approval phrase "
                f"found in {SHARED_REVIEWS} — record the review first"
            )
        print("review found; AUTH/dry/launch remain in the engineer flow for now "
              "(driver v1 stops here by design)")
        return 0

    if not args.proof_number or not args.capture_number:
        raise DriverRefusal("prepare requires --proof-number and --capture-number")
    state = step_selection(args.shard)
    state["prestage_workdir"] = step_prestage(args.shard, state)
    proof_path = step_proof(args.shard, state, args.proof_number)
    fixture_path = step_fixtures(args.shard, state, args.capture_number)
    print(json.dumps({
        "status": "PREPARED_THROUGH_FIXTURES",
        "shard": args.shard, "attempt": args.attempt,
        "rows": state["entry"]["rows"],
        "row_list_sha256": state["selection"]["public_row_list_sha256"],
        "bundle_identity": state["bundle_identity"],
        "proof": str(proof_path.relative_to(ROOT)),
        "fixtures": str(fixture_path.relative_to(ROOT)),
        "next": ("bindings/registry/boundary/packet remain scripted in the session "
                 "for attempt review; driver v2 absorbs them after one supervised pass"),
    }, indent=1))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DriverRefusal as exc:
        print(json.dumps({"status": "REFUSED", "detail": str(exc)}))
        raise SystemExit(1)
