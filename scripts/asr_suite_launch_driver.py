#!/usr/bin/env python3
"""Launch-cycle driver v2: the full reviewed per-attempt stack (task: driver v2).

v1 (scripts/asr_suite_shard_driver.py, attempts 32-44) proved
selection -> prestage -> proof -> fixtures from one command and stopped at
the review checklist; bindings/registry/boundary/packet and the whole
launch phase stayed hand-scripted. v2 absorbs them, and generalizes
selection beyond shard numbers — the attempts 39-44 re-covers were
hand-driven precisely because their selections were not plain shards, and
the coming akan/serer re-eval and post-training T6 re-runs need
language-scoped unit lists.

DOCTRINE (unchanged from v1): the driver never invents authority. Every
validator stays in the loop; the review decision and the numbered approval
phrase are written by the engineer into the shared file — the driver only
checks for them. Write-once artifacts refuse to overwrite. All per-attempt
surgery is copy-forward from the latest committed instance with explicit
field updates, so nothing is synthesized from memory.

  prepare --attempt N (--shard S | --units u1,u2 --expected-rows R |
                       --selection-file PATH)
          --proof-number XXX --capture-number XXX --revision-suffix SFX
      selection -> prestage -> proof -> fixtures        (v1, imported)
      -> cost-registry revision (copy-forward, prior attempt closed)
      -> attempt-boundary bump (3 executor sites, exact-count asserted)
      -> commit bump
      -> bindings revision (copy-forward: executor hashes recomputed
         AFTER the bump, proof/capture/registry/packet/history rebound)
      -> packet draft from the latest packet (attempt ids substituted;
         the engineer reviews it — the review gate is unchanged)
      -> commit bindings + packet
      -> cold rehearsal TWICE, byte-identity asserted
      -> emits the review checklist and STOPS.

  launch --attempt N --revision-suffix SFX
      refuses unless the shared-file review for this attempt exists
      -> AUTH revision (copy-forward; reviewed_repository_commit = HEAD,
         packet sha rebound) committed ALONE
      -> previous dry receipt removed + committed
      -> deadline-identity dry run, receipt committed
      -> caffeinate -ims runner launch.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.asr_suite_shard_driver import (  # noqa: E402
    CANON_RISK,
    DriverRefusal,
    SCRATCH,
    SHARED_REVIEWS,
    _git_commit,
    _run,
    _sha,
    review_is_recorded,
    step_fixtures,
    step_prestage,
    step_proof,
    step_selection,
)
from scripts.asr_base_model_pilot_assets import (  # noqa: E402
    select_suite_rows,
    suite_bundle_identity,
)

META_PREFIX = (
    "research/asr-base-model/pilot/"
    "1cdca3e75195c7c7417550154e36a1f372715e17efd13c835c87ee503fa84eee/"
)

BOUNDARY_FILES = (
    "scripts/asr_base_model_pilot_runner.py",
    "scripts/asr_base_model_pilot_k8s.py",
    "scripts/asr_base_model_pilot_plan.py",
)
BOUNDARY_RE = re.compile(r"range\(1, (\d+)\)")

MANIFEST_DIR = ROOT / "platform/manifests"
FINANCE_DIR = ROOT / "platform/finance"
DECISIONS_DIR = ROOT / "platform/decisions"
EVIDENCE_DIR = ROOT / "platform/evidence"


# --------------------------------------------------------------------------- #
# pure surgery functions (unit-tested without AWS)
# --------------------------------------------------------------------------- #
def bump_boundary_text(text: str, attempt: int, sites_expected: int = 1) -> str:
    """Rewrite the attempt window upper bound to admit exactly `attempt`.

    Refuses when the file does not carry exactly `sites_expected` boundary
    sites, when the current bound is not `attempt` (already bumped is a
    no-op, anything else is drift), or when the bump would WIDEN the window
    by more than one attempt.
    """
    matches = BOUNDARY_RE.findall(text)
    if len(matches) != sites_expected:
        raise DriverRefusal(
            f"expected {sites_expected} boundary site(s), found {len(matches)}")
    current = int(matches[0])
    if current == attempt + 1:
        return text  # already admits this attempt — idempotent
    if current != attempt:
        raise DriverRefusal(
            f"boundary is range(1, {current}) — cannot jump to attempt "
            f"{attempt}; attempts are bumped one at a time")
    return BOUNDARY_RE.sub(f"range(1, {attempt + 1})", text, count=sites_expected)


def next_registry_document(previous: dict, attempt: int, suffix: str,
                           now_utc: str) -> dict:
    """Copy-forward the cost registry: close the prior reservation, reserve
    this attempt under the same ceiling. Only fields that exist in the
    previous document are touched."""
    doc = json.loads(json.dumps(previous))
    prior = doc.get("id", "")
    match = re.search(r"(\d+)$", prior)
    if not match:
        raise DriverRefusal(f"cannot derive successor id from {prior!r}")
    doc["id"] = prior[: match.start()] + f"{int(match.group(1)) + 1:03d}"
    doc["supersedes"] = prior
    doc["recorded_utc"] = now_utc
    doc["attempt_reservation"] = {
        "attempt": attempt,
        "revision_suffix": suffix,
        "state": "RESERVED",
        "previous_attempt_state": "CLOSED_SEE_TERMINAL_EVIDENCE",
    }
    return doc


def next_bindings_document(previous: dict, updates: dict) -> dict:
    """Copy-forward bindings with explicit field surgery. Every top-level
    key in `updates` must already exist in the previous bindings — new
    sections are a design change, not an attempt revision."""
    doc = json.loads(json.dumps(previous))
    for key, value in updates.items():
        if key not in doc:
            raise DriverRefusal(
                f"bindings surgery touches unknown section {key!r} — new "
                "sections need a reviewed design change, not a revision")
        doc[key] = value
    return doc


def next_auth_document(previous: dict, *, attempt: int, head_commit: str,
                       packet_path: str, packet_sha: str, auth_id: str,
                       auth_path: str, dry_run_id: str,
                       now_utc: str) -> dict:
    doc = json.loads(json.dumps(previous))
    doc["supersedes"] = doc.get("id")
    doc["id"] = auth_id
    doc["recorded_utc"] = now_utc
    doc["attempt"] = attempt
    doc["reviewed_repository_commit"] = head_commit
    doc["packet"] = {"path": packet_path, "sha256": packet_sha}
    doc["deadline_dry_run_id"] = dry_run_id
    if doc.get("status") not in ("owner-approved", "owner_approved"):
        raise DriverRefusal("previous AUTH is not owner-approved — refusing "
                            "to inherit an unapproved authority")
    return doc


def executor_module_hashes(paths: dict[str, str], root: Path = ROOT) -> dict:
    """Recompute the bound executor hashes for the SAME module set the
    previous bindings carried — membership changes are design changes."""
    return {rel: hashlib.sha256((root / rel).read_bytes()).hexdigest()
            for rel in sorted(paths)}


# --------------------------------------------------------------------------- #
# selection generalization
# --------------------------------------------------------------------------- #
def generalized_selection(args) -> dict:
    """Shard-derived (v1 path), explicit unit list, or a prebuilt
    selection file. Row counts are always asserted."""
    if args.shard is not None:
        return step_selection(args.shard)
    if args.selection_file:
        selection = json.loads(Path(args.selection_file).read_bytes())
        rows = len(selection["rows"])
        if args.expected_rows is not None and rows != args.expected_rows:
            raise DriverRefusal(
                f"selection file holds {rows} rows, expected {args.expected_rows}")
        out = SCRATCH / f"driver-custom-a{args.attempt}-selection.json"
        out.write_bytes(json.dumps(selection, sort_keys=True,
                                   separators=(",", ":"),
                                   ensure_ascii=False).encode())
        identity = suite_bundle_identity(
            selection["public_row_list_sha256"], META_PREFIX)
        return {"entry": {"rows": rows, "units": selection.get("units", [])},
                "selection_path": out, "selection": selection,
                "bundle_identity": identity["sha256"]}
    if args.units:
        units = [u.strip() for u in args.units.split(",") if u.strip()]
        if args.expected_rows is None:
            raise DriverRefusal("--units requires --expected-rows (the count "
                                "must be asserted against an external truth, "
                                "never inferred from what selection returns)")
        selection = select_suite_rows(SCRATCH / "eval-manifests", units)
        if len(selection["rows"]) != args.expected_rows:
            raise DriverRefusal(
                f"selection produced {len(selection['rows'])} rows, "
                f"--expected-rows says {args.expected_rows}")
        out = SCRATCH / f"driver-custom-a{args.attempt}-selection.json"
        out.write_bytes(json.dumps(selection, sort_keys=True,
                                   separators=(",", ":"),
                                   ensure_ascii=False).encode())
        identity = suite_bundle_identity(
            selection["public_row_list_sha256"], META_PREFIX)
        return {"entry": {"rows": len(selection["rows"]), "units": units},
                "selection_path": out, "selection": selection,
                "bundle_identity": identity["sha256"]}
    raise DriverRefusal("one of --shard, --units or --selection-file is required")


# --------------------------------------------------------------------------- #
# copy-forward steps (repo-mutating; every write-once path refuses reuse)
# --------------------------------------------------------------------------- #
def _latest(pattern: str, directory: Path) -> Path:
    matches = sorted(directory.glob(pattern))
    if not matches:
        raise DriverRefusal(f"no files match {pattern} under {directory}")
    return matches[-1]


def _write_once(path: Path, payload: bytes) -> None:
    if path.exists():
        raise DriverRefusal(f"{path.name} already exists (write-once)")
    path.write_bytes(payload)


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def step_registry_revision(attempt: int, suffix: str) -> Path:
    latest = _latest("COST-REGISTRY-2026-*.json", FINANCE_DIR)
    doc = next_registry_document(json.loads(latest.read_bytes()),
                                 attempt, suffix, _now())
    path = FINANCE_DIR / f"{doc['id']}.json"
    _write_once(path, json.dumps(doc, indent=1, sort_keys=True).encode() + b"\n")
    return path


def step_boundary_bump(attempt: int) -> list[str]:
    changed = []
    for rel in BOUNDARY_FILES:
        path = ROOT / rel
        text = path.read_text()
        new = bump_boundary_text(text, attempt)
        if new != text:
            path.write_text(new)
            changed.append(rel)
    return changed


def step_bindings_revision(state: dict, attempt: int, suffix: str,
                           proof_path: Path, capture_path: Path,
                           registry_path: Path, packet_path: Path) -> Path:
    latest = _latest("ASR-BASE-MODEL-PILOT-BINDINGS-2026-*.json", MANIFEST_DIR)
    previous = json.loads(latest.read_bytes())
    if previous.get("risk_acceptance_sha256") != CANON_RISK:
        raise DriverRefusal("previous bindings do not carry the canonical "
                            "risk-acceptance sha — refusing to copy forward")
    head = _run(["git", "rev-parse", "HEAD"]).stdout.strip()
    history = dict(previous["write_once_history"])
    prior_auth = previous["authorization"]
    prior_attempt = previous["attempts"]["authorized_numbers"][0]
    history[f"attempt_{prior_attempt}_authorization"] = {
        "path": prior_auth["path"],
        "sha256": _sha(ROOT / prior_auth["path"]),
    }
    auth_id = f"ASR-BASE-MODEL-AWS-AUTH-2026-{suffix}"
    updates = {
        "attempts": dict(previous["attempts"],
                         authorized_numbers=[attempt],
                         **{f"attempts_1_through_{attempt - 1}_reuse_permitted":
                            False}),
        "artifact_prestage_proof": {
            "path": str(proof_path.relative_to(ROOT)),
            "sha256": _sha(proof_path)},
        "aws_read_fixtures": dict(previous["aws_read_fixtures"],
                                  capture_record=str(capture_path.relative_to(ROOT)),
                                  capture_sha256=_sha(capture_path)),
        "cost_registry": {"path": str(registry_path.relative_to(ROOT)),
                          "sha256": _sha(registry_path)},
        "successor_packet": {"path": str(packet_path.relative_to(ROOT)),
                             "sha256": _sha(packet_path)},
        "executor_modules": executor_module_hashes(
            previous["executor_modules"]),
        "executor_source_commit": head,
        "pilot_bundle": {"sha256": state["bundle_identity"]},
        "suite_shard": dict(previous["suite_shard"],
                            rows=state["entry"]["rows"],
                            units=state["entry"]["units"],
                            row_list_sha256=state["selection"]
                            ["public_row_list_sha256"]),
        "authorization": dict(prior_auth,
                              id=auth_id,
                              path=f"platform/decisions/{auth_id}.json",
                              deadline_dry_run_id=(
                                  f"ASR-BASE-MODEL-DEADLINE-IDENTITY-DRY-RUN-"
                                  f"2026-{suffix}"),
                              deadline_dry_run_path=(
                                  f"platform/evidence/ASR-BASE-MODEL-DEADLINE-"
                                  f"IDENTITY-DRY-RUN-2026-{suffix}.json")),
        "write_once_history": history,
    }
    # drop the stale per-attempt reuse key carried from the previous revision
    stale_keys = [k for k in updates["attempts"]
                  if k.startswith("attempts_1_through_")
                  and k != f"attempts_1_through_{attempt - 1}_reuse_permitted"]
    for k in stale_keys:
        del updates["attempts"][k]
    doc = next_bindings_document(previous, updates)
    path = MANIFEST_DIR / f"ASR-BASE-MODEL-PILOT-BINDINGS-2026-{suffix}.json"
    _write_once(path, json.dumps(doc, indent=1, sort_keys=True).encode() + b"\n")
    return path


def step_packet_draft(attempt: int, suffix: str) -> Path:
    latest = _latest("ASR-BASE-MODEL-AWS-CHANGE-PACKET-2026-*-attempt-*.md",
                     DECISIONS_DIR)
    text = latest.read_text()
    prior_attempt = re.search(r"attempt-(\d+)\.md$", latest.name).group(1)
    text = text.replace(f"attempt {prior_attempt}", f"attempt {attempt}")
    text = text.replace(f"attempt-{prior_attempt}", f"attempt-{attempt}")
    header = (f"<!-- DRAFT generated by driver v2 from {latest.name}; the\n"
              f"     reviewer edits estimates/rationale before approval -->\n")
    path = (DECISIONS_DIR /
            f"ASR-BASE-MODEL-AWS-CHANGE-PACKET-2026-{suffix}-attempt-{attempt}.md")
    _write_once(path, (header + text).encode())
    return path


def step_rehearsal(attempt: int, bindings_path: Path) -> None:
    """Cold rehearsal twice against the NEW bindings; the receipt must be
    byte-identical across rounds. Receipts are written INSIDE the repo
    (attempt-38 lesson) into an untracked scratch subdir, then removed."""
    outputs = []
    scratch = ROOT / ".driver-v2-rehearsal"
    scratch.mkdir(exist_ok=True)
    try:
        for round_number in (1, 2):
            out = scratch / f"a{attempt}-round{round_number}.json"
            _run([sys.executable,
                  "scripts/asr_base_model_pilot_cold_rehearsal.py",
                  "--output", str(out), "--bindings", str(bindings_path)])
            outputs.append(_sha(out))
    finally:
        import shutil
        shutil.rmtree(scratch, ignore_errors=True)
    if outputs[0] != outputs[1]:
        raise DriverRefusal(
            f"cold rehearsal is not byte-identical: {outputs[0][:16]} != "
            f"{outputs[1][:16]}")


def step_auth_revision(attempt: int, suffix: str, bindings_path: Path) -> Path:
    bindings = json.loads(bindings_path.read_bytes())
    packet = bindings["successor_packet"]
    latest_auth = _latest("ASR-BASE-MODEL-AWS-AUTH-2026-*.json", DECISIONS_DIR)
    head = _run(["git", "rev-parse", "HEAD"]).stdout.strip()
    auth_id = bindings["authorization"]["id"]
    doc = next_auth_document(
        json.loads(latest_auth.read_bytes()), attempt=attempt,
        head_commit=head, packet_path=packet["path"],
        packet_sha=packet["sha256"], auth_id=auth_id,
        auth_path=bindings["authorization"]["path"],
        dry_run_id=bindings["authorization"]["deadline_dry_run_id"],
        now_utc=_now())
    path = ROOT / bindings["authorization"]["path"]
    _write_once(path, json.dumps(doc, indent=1, sort_keys=True).encode() + b"\n")
    return path


# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("prepare", "launch"))
    parser.add_argument("--attempt", type=int, required=True)
    parser.add_argument("--shard", type=int)
    parser.add_argument("--units")
    parser.add_argument("--selection-file")
    parser.add_argument("--expected-rows", type=int)
    parser.add_argument("--proof-number")
    parser.add_argument("--capture-number")
    parser.add_argument("--revision-suffix", required=True,
                        help="e.g. 003C — names the bindings/AUTH/packet "
                             "revision family")
    args = parser.parse_args()
    suffix = args.revision_suffix

    if args.mode == "launch":
        if not review_is_recorded(args.attempt):
            raise DriverRefusal(
                f"no APPROVED review with the attempt-{args.attempt} approval "
                f"phrase found in {SHARED_REVIEWS} — record the review first")
        bindings_path = (MANIFEST_DIR /
                         f"ASR-BASE-MODEL-PILOT-BINDINGS-2026-{suffix}.json")
        if not bindings_path.exists():
            raise DriverRefusal(f"{bindings_path.name} does not exist — "
                                "prepare first")
        auth_path = step_auth_revision(args.attempt, suffix, bindings_path)
        _git_commit(f"AUTH revision for attempt {args.attempt} (driver v2)",
                    [str(auth_path.relative_to(ROOT))])
        bindings = json.loads(bindings_path.read_bytes())
        for stale in sorted(EVIDENCE_DIR.glob(
                "ASR-BASE-MODEL-DEADLINE-IDENTITY-DRY-RUN-2026-*.json")):
            if stale.name != Path(
                    bindings["authorization"]["deadline_dry_run_path"]).name:
                _run(["git", "rm", "-q", str(stale.relative_to(ROOT))])
        _git_commit("remove superseded dry receipts (driver v2)", [])
        packet_rel = bindings["successor_packet"]["path"]
        receipt = ROOT / bindings["authorization"]["deadline_dry_run_path"]
        _run([sys.executable, "scripts/asr_base_model_deadline_dry_run.py",
              "--root", str(ROOT),
              "--bindings", str(bindings_path),
              "--authorization", str(auth_path),
              "--packet", str(ROOT / packet_rel),
              "--attempt", str(args.attempt),
              "--output", str(receipt)])
        if not receipt.exists():
            raise DriverRefusal("dry run did not write the bound receipt")
        _git_commit(f"deadline-identity dry receipt, attempt {args.attempt} "
                    "(driver v2)", [str(receipt.relative_to(ROOT))])
        print(json.dumps({
            "status": "READY_TO_LAUNCH",
            "launch_command": (
                "caffeinate -ims python3 "
                "scripts/asr_base_model_pilot_runner.py "
                f"--bindings {bindings_path.relative_to(ROOT)} "
                f"--authorization {auth_path.relative_to(ROOT)} "
                f"--packet {packet_rel} "
                f"--attempt {args.attempt} "
                f"--workdir {SCRATCH}/attempt-{args.attempt}-workdir"),
        }, indent=1))
        return 0

    if not args.proof_number or not args.capture_number:
        raise DriverRefusal("prepare requires --proof-number and "
                            "--capture-number")
    state = generalized_selection(args)
    shard_label = args.shard if args.shard is not None else 0
    state["prestage_workdir"] = step_prestage(shard_label, state)
    proof_path = step_proof(shard_label, state, args.proof_number)
    capture_path = step_fixtures(shard_label, state, args.capture_number)
    _git_commit(f"attempt {args.attempt} evidence: proof + fixtures "
                "(driver v2)",
                [str(proof_path.relative_to(ROOT)),
                 str(capture_path.relative_to(ROOT)), "tests/fixtures"])
    registry_path = step_registry_revision(args.attempt, suffix)
    changed = step_boundary_bump(args.attempt)
    _git_commit(f"attempt {args.attempt} boundary bump + registry "
                "(driver v2)",
                [str(registry_path.relative_to(ROOT))] + changed)
    packet_path = step_packet_draft(args.attempt, suffix)
    bindings_path = step_bindings_revision(
        state, args.attempt, suffix, proof_path, capture_path,
        registry_path, packet_path)
    _git_commit(f"attempt {args.attempt} bindings {suffix} + packet draft "
                "(driver v2)",
                [str(bindings_path.relative_to(ROOT)),
                 str(packet_path.relative_to(ROOT))])
    step_rehearsal(args.attempt, bindings_path)
    print(json.dumps({
        "status": "PREPARED_STOPPED_AT_REVIEW",
        "attempt": args.attempt,
        "rows": state["entry"]["rows"],
        "bindings": str(bindings_path.relative_to(ROOT)),
        "packet": str(packet_path.relative_to(ROOT)),
        "review_checklist": [
            "read the packet draft end to end and fix estimates/rationale",
            "verify bindings diff against the previous revision",
            "verify the rehearsal byte-identity line above",
            f"write the review with 'authorizing numbered attempt "
            f"{args.attempt} ' + DECISION: APPROVED into the shared file",
            f"then: {Path(__file__).name} launch --attempt {args.attempt} "
            f"--revision-suffix {suffix}",
        ],
    }, indent=1))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DriverRefusal as exc:
        print(json.dumps({"status": "REFUSED", "detail": str(exc)}))
        raise SystemExit(2)
