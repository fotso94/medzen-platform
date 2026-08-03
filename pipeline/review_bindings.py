"""Recompute the artefacts a review decision is bound to. Never trust a copy.

A binding copied into a document proves only what someone typed. Every value
here is recomputed from the artefact it names -- the audit file's bytes, the
completion record in S3, each of the 18 manifests, the tokenizer cache manifest,
the verifier's own source, and the git state -- so a decision that passes these
checks is bound to artefacts that still exist and still hash the same.

Used by both the review tool (before review may begin) and the finalizer (before
a decision may be approved), so the two cannot disagree about what was reviewed.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

BUCKET = "medzen-speech"
ROOT = Path(__file__).resolve().parent.parent
AUDIT = ROOT / "platform/evidence/label-length-audit-v2.json"
COMPLETE_KEY = "curated/_versions/v2/COMPLETE.json"


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# Review WRITES to the draft, so after any review the tree is dirty by design.
# Refusing all dirtiness would make an interrupted review unfinishable; allowing
# all of it would let code or evidence change under an approval. Exactly one
# path may differ.
REVIEWABLE_DIRTY = {"platform/decisions/DQ-2026-001-label-review.json"}


def git(*args: str) -> str:
    """Trailing newline only. `--porcelain` encodes status in the first TWO
    columns, so ' M path' begins with a significant space; stripping it shifts
    every offset and silently truncates the first path by one character."""
    r = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True)
    return r.stdout.rstrip("\n") if r.returncode == 0 else ""


def dirty_paths() -> list[str]:
    """Paths git reports as changed, including renames and untracked files."""
    out = git("status", "--porcelain")
    paths: list[str] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        rest = line[3:] if len(line) > 3 else line.lstrip()
        # a rename is reported as "old -> new"; both sides are changes
        if " -> " in rest:
            paths += [p.strip().strip('"') for p in rest.split(" -> ")]
        else:
            paths.append(rest.strip().strip('"'))
    return sorted(set(paths))


def recompute(cli, version: str = "v2", audit_path: Path | None = None) -> dict:
    """Recompute every binding from its source artefact."""
    audit_file = audit_path or AUDIT
    audit_raw = audit_file.read_bytes()
    audit = json.loads(audit_raw)

    comp_raw = cli.get_object(Bucket=BUCKET, Key=COMPLETE_KEY)["Body"].read()
    comp = json.loads(comp_raw)

    # Every manifest listed in the completion record must still hash the same.
    manifest_status: dict[str, dict] = {}
    for label, meta in (comp.get("manifests") or {}).items():
        body = cli.get_object(Bucket=BUCKET, Key=meta["key"])["Body"].read()
        got = sha256_bytes(body)
        manifest_status[label] = {"declared": meta["sha256"], "actual": got,
                                  "matches": got == meta["sha256"]}

    tok_prefix = (f"models/base/whisper-large-v3/"
                  f"{audit['tokenizer']['revision']}")
    tok_man = json.loads(cli.get_object(Bucket=BUCKET,
                                        Key=f"{tok_prefix}/MANIFEST.json")["Body"].read())

    return {
        "audit_path": str(audit_file.relative_to(ROOT)),
        "audit_sha256": sha256_bytes(audit_raw),
        "audit_verifier_git_commit": audit["verifier"]["git_commit"],
        "audit_verifier_git_dirty": audit["verifier"]["git_dirty"],
        "audit_verifier_file_sha256": sha256_bytes(
            (ROOT / "scripts/audit_label_lengths.py").read_bytes()),
        "audit_declared_verifier_sha256": audit["verifier"]["sha256"],
        "complete_key": COMPLETE_KEY,
        "complete_sha256": sha256_bytes(comp_raw),
        "complete_adopted": comp.get("adopted"),
        "manifests": manifest_status,
        "manifests_total": len(manifest_status),
        "manifests_matching": sum(1 for m in manifest_status.values() if m["matches"]),
        "tokenizer_revision": audit["tokenizer"]["revision"],
        "tokenizer_cache_manifest_sha256": sha256_bytes(
            json.dumps(tok_man, sort_keys=True).encode()),
        "audit_declared_tokenizer_cache_sha256":
            audit["tokenizer"]["cache_manifest_sha256"],
        "scope": audit["scope"],
        "repo_git_commit": git("rev-parse", "HEAD"),
        "repo_dirty_paths": dirty_paths(),
        "repo_git_dirty": bool(dirty_paths()),
        "_audit": audit,
    }


def verify(b: dict, expect: dict | None = None) -> list[str]:
    """Return the reasons this state is unusable. Empty means usable."""
    bad: list[str] = []
    if b["audit_verifier_git_dirty"]:
        bad.append("the bound audit was produced from a dirty working tree")
    # The repository must be clean NOW apart from the draft itself: a decision
    # approved against uncommitted CODE cannot be reproduced from the commit it
    # names, but the draft is expected to differ -- review is what changed it.
    disallowed = [p for p in b.get("repo_dirty_paths", []) if p not in REVIEWABLE_DIRTY]
    if disallowed:
        bad.append("uncommitted changes outside the review draft: "
                   + ", ".join(disallowed[:8])
                   + (f" (+{len(disallowed) - 8} more)" if len(disallowed) > 8 else "")
                   + " — commit code and evidence before approving")
    if b["audit_verifier_file_sha256"] != b["audit_declared_verifier_sha256"]:
        bad.append("scripts/audit_label_lengths.py has changed since the audit ran; "
                   "the audit no longer describes what the current code would produce")
    if b["tokenizer_cache_manifest_sha256"] != b["audit_declared_tokenizer_cache_sha256"]:
        bad.append("the tokenizer cache manifest changed since the audit ran")
    if b["manifests_total"] != 18:
        bad.append(f"expected 18 manifests, completion record lists {b['manifests_total']}")
    if b["manifests_matching"] != b["manifests_total"]:
        for label, m in b["manifests"].items():
            if not m["matches"]:
                bad.append(f"manifest {label} sha256 {m['actual'][:16]} != "
                           f"declared {m['declared'][:16]}")
    scope = b["scope"]
    if (scope.get("manifest_version"), scope.get("split"),
            scope.get("require_allowed_use")) != ("v2", "train", "asr_train"):
        bad.append(f"audit scope is {scope}, expected v2/train/asr_train")

    if expect:
        for k, v in expect.items():
            if b.get(k) != v:
                bad.append(f"binding {k} is {b.get(k)!r}, decision recorded {v!r}")
    return bad
