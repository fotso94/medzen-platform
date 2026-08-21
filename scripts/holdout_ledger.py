#!/usr/bin/env python3
"""Executable sealed-holdout consumption gate, v4 (Codex reviews #9-#12).

Chain rule: every entry embeds prev_sha256 of the previous line's exact
bytes. Consumption rule: a holdout must be RESERVED (with its manifest
sha) before it can be CONSUMED; acquisition verifies the requested sha
against the reservation and is ATOMIC (exclusive file lock around
read+append). Voids and releases are honored only as strict, schema-
complete adjudications whose owner approval is a COMMITTED authorization
record verified by path + sha256 — free-text 'owner ...' strings are
worthless (Codex review #12: they were forgeable)."""
from __future__ import annotations

import datetime
import fcntl
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "platform/evidence/HOLDOUT-CONSUMPTION-LEDGER.jsonl"


class LedgerRefusal(RuntimeError):
    pass


def _lines(path: Path = LEDGER) -> list[str]:
    return [l for l in path.read_text().splitlines() if l.strip()]


def verify_chain(path: Path = LEDGER) -> list[dict]:
    lines = _lines(path)
    entries = []
    for index, line in enumerate(lines):
        entry = json.loads(line)
        if entry["entry"] != index + 1:
            raise LedgerRefusal(f"entry numbering broken at line {index + 1}")
        if index > 0:
            want = hashlib.sha256(lines[index - 1].encode()).hexdigest()
            if entry.get("prev_sha256") != want:
                raise LedgerRefusal(
                    f"hash chain broken at entry {entry['entry']} — the "
                    "ledger history was rewritten")
        entries.append(entry)
    return entries


def _verify_owner_approval(entry: dict, repo_root: Path) -> bool:
    """Approval = an authorization record COMMITTED AT GIT HEAD (Codex
    review #13: a working-tree file in a non-git directory passed the v4
    check). The bytes are read from `git show HEAD:<path>` — untracked,
    staged-only, absolute, traversal and symlinked paths all fail. Trust
    model: the project's standing authorization pattern is committed
    records carrying the owner's verbatim words, reviewed in git history
    (same as training packets); cryptographic signing is not in use."""
    import subprocess
    ref = entry.get("approval_record")
    if not isinstance(ref, dict):
        return False
    raw_path = str(ref.get("path", ""))
    if (not raw_path.startswith("platform/decisions/")
            or raw_path != str(Path(raw_path))
            or ".." in Path(raw_path).parts or Path(raw_path).is_absolute()):
        return False
    on_disk = repo_root / raw_path
    if on_disk.is_symlink():
        return False
    got = subprocess.run(["git", "-C", str(repo_root), "show",
                          f"HEAD:{raw_path}"],
                         capture_output=True)
    if got.returncode != 0:
        return False
    raw = got.stdout
    if hashlib.sha256(raw).hexdigest() != ref.get("sha256"):
        return False
    try:
        doc = json.loads(raw)
    except ValueError:
        return False
    return (doc.get("record") == ref.get("record_id")
            and doc.get("authorizes") == entry["event"]
            and doc.get("holdout") == entry.get("holdout")
            and bool(str(doc.get("owner_verbatim", "")).strip()))


VOID_REQUIRED = {"voids_entry", "holdout", "holdout_sha256",
                 "evaluator_instance_ids", "userdata_sha256",
                 "results_prefix_object_count", "log_version_ids",
                 "no_inference_attestation", "approval_record"}


def _valid_voids(entries: list[dict], repo_root: Path) -> set[int]:
    by_number = {e["entry"]: e for e in entries}
    seen: set[int] = set()
    valid: set[int] = set()
    for e in entries:
        if e["event"] != "CONSUMPTION_VOIDED":
            continue
        if not VOID_REQUIRED <= set(e):
            continue
        target = by_number.get(e["voids_entry"])
        if (target is None or target["event"] != "CONSUMED"
                or target["entry"] >= e["entry"]
                or target.get("holdout") != e.get("holdout")
                or target.get("sha256") != e.get("holdout_sha256")):
            continue
        ids = e.get("evaluator_instance_ids")
        logs = e.get("log_version_ids")
        if not (isinstance(ids, list) and ids and all(
                isinstance(x, str) and x.startswith("i-") for x in ids)):
            continue
        if not (isinstance(logs, list) and logs
                and all(isinstance(x, str) and x for x in logs)):
            continue
        userdata = str(e.get("userdata_sha256", ""))
        if not (len(userdata) == 64
                and all(c in "0123456789abcdef" for c in userdata.lower())):
            continue
        if e.get("results_prefix_object_count") != 0:
            continue
        if "no model inference started and no results were produced" not in \
                str(e.get("no_inference_attestation", "")):
            continue
        if not _verify_owner_approval(e, repo_root):
            continue
        if e["voids_entry"] in seen:
            valid.discard(e["voids_entry"])
            continue
        seen.add(e["voids_entry"])
        valid.add(e["voids_entry"])
    return valid


def _active_quarantines(entries: list[dict], repo_root: Path) -> set[str]:
    by_number = {e["entry"]: e for e in entries}
    quarantined: dict[str, int] = {}
    for e in entries:
        if e["event"] == "QUARANTINED":
            quarantined[e.get("holdout")] = e["entry"]
        elif e["event"] == "RELEASED":
            target = by_number.get(e.get("releases_entry"))
            if (target is not None and target["event"] == "QUARANTINED"
                    and target["entry"] < e["entry"]
                    and target.get("holdout") == e.get("holdout")
                    and quarantined.get(e.get("holdout")) == target["entry"]
                    and _verify_owner_approval(e, repo_root)):
                quarantined.pop(e.get("holdout"), None)
    return set(quarantined)


def require_available(holdout_key: str, path: Path = LEDGER,
                       repo_root: Path = ROOT) -> None:
    entries = verify_chain(path)
    if holdout_key in _active_quarantines(entries, repo_root):
        raise LedgerRefusal(
            f"{holdout_key} is QUARANTINED — release requires an "
            "owner-approved RELEASED entry targeting the exact quarantine")
    voided = _valid_voids(entries, repo_root)
    for entry in entries:
        if (entry.get("holdout") == holdout_key
                and entry["event"] == "CONSUMED"
                and entry["entry"] not in voided):
            raise LedgerRefusal(
                f"{holdout_key} was already CONSUMED (entry "
                f"{entry['entry']}) — the seal is spent")


def _withdrawn_reservations(entries: list[dict]) -> set[int]:
    """Codex review #19: the first pidgin sealed reservation pointed at a
    manifest whose rows carried TRAINING labels — the object had to be
    superseded, not consumed. RESERVATION_WITHDRAWN removes a reservation
    (a capability-REMOVING event, so it needs no approval record — like
    a refusal). Strict schema: it must cite the exact RESERVED entry by
    number, matching holdout AND sha, plus a reason and the successor
    key. A withdrawal that has ever been CONSUMED cannot be withdrawn.
    Malformed withdrawals are INERT — they never suppress anything."""
    by_number = {e["entry"]: e for e in entries}
    withdrawn: set[int] = set()
    for e in entries:
        if e["event"] != "RESERVATION_WITHDRAWN":
            continue
        target = by_number.get(e.get("withdraws_entry"))
        if (target is None or target["event"] != "RESERVED"
                or target.get("holdout") != e.get("holdout")
                or target.get("sha256") != e.get("sha256")
                or not e.get("reason") or not e.get("successor_key")):
            continue
        if any(c["event"] == "CONSUMED"
               and c.get("holdout") == target.get("holdout")
               and c.get("sha256") == target.get("sha256")
               for c in entries):
            continue
        withdrawn.add(target["entry"])
    return withdrawn


def _reserved_sha(entries: list[dict], holdout_key: str) -> str | None:
    """Codex review #13: a later CONFLICTING reservation silently overrode
    the original. Multiple reservations must agree, or the ledger refuses
    until an owner-approved amendment resolves them. Withdrawn
    reservations (review #19) no longer count."""
    withdrawn = _withdrawn_reservations(entries)
    shas = [e.get("sha256") for e in entries
            if e["event"] == "RESERVED" and e.get("holdout") == holdout_key
            and e["entry"] not in withdrawn]
    if not shas:
        return None
    if len(set(shas)) > 1:
        raise LedgerRefusal(
            f"{holdout_key} carries CONFLICTING reservations "
            f"({[x[:12] for x in dict.fromkeys(shas)]}) — refuse until an "
            "owner-approved amendment resolves them")
    return shas[-1]


def record_consumption(holdout_key: str, sha256: str, consumed_by: str,
                        path: Path = LEDGER,
                        repo_root: Path = ROOT) -> dict:
    """ATOMIC acquire (exclusive flock around read+verify+append). The
    requested sha must equal the holdout's RESERVED sha (Codex review #12:
    an all-zero sha acquired the universal holdout)."""
    import tempfile
    lock_path = Path(tempfile.gettempdir()) / (
        "medzen-ledger-" + hashlib.sha256(
            str(path.resolve()).encode()).hexdigest()[:16] + ".lock")
    with lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            entries = verify_chain(path)
            reserved = _reserved_sha(entries, holdout_key)
            if reserved is None:
                raise LedgerRefusal(
                    f"{holdout_key} has no RESERVED entry — unreserved "
                    "holdouts cannot be consumed")
            if sha256 != reserved:
                raise LedgerRefusal(
                    f"requested sha {sha256[:16]}… does not match the "
                    f"RESERVED sha {reserved[:16]}… for {holdout_key}")
            require_available(holdout_key, path, repo_root)
            lines = _lines(path)
            entry = {
                "entry": len(lines) + 1,
                "utc": datetime.datetime.now(datetime.timezone.utc)
                        .strftime("%Y-%m-%dT%H:%M:%SZ"),
                "event": "CONSUMED",
                "holdout": holdout_key,
                "sha256": sha256,
                "consumed_by": consumed_by,
                "prev_sha256": hashlib.sha256(lines[-1].encode()).hexdigest(),
            }
            with path.open("a") as fh:
                fh.write(json.dumps(entry, sort_keys=True) + "\n")
            return entry
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
