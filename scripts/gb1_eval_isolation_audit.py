#!/usr/bin/env python3
"""gb1 training vs frozen-eval isolation and licence audit (plan §6 items 1-2).

Fine-tuning gates are only meaningful if the training pool cannot see the
evaluation pool. This audit proves, per language, that the gb1 training
manifests and the frozen eval manifests share:

  - no audio content (exact audio_checksum_sha256 intersection),
  - no speaker within the same source family ((source_id, speaker_id)),
  - no session within the same source family ((source_id, session_id)),
  - and it counts exact normalized-text collisions (same sentence read in
    both pools — a near-dup risk surface, reported not auto-failed, since
    common phrases legitimately recur).

It also inventories every licence/consent declaration on both sides so the
training-permission review reads from one table, and flags training
manifests whose licence policy is research/non-commercial.

Read-only: consumes local manifest copies; changes nothing.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

RESEARCH_ONLY_MARKERS = ("nc", "non_commercial", "noncommercial", "research")


def load_rows(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open() as stream:
        for line in stream:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _key_fields(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "checksum": row.get("audio_checksum_sha256"),
        "speaker": (row.get("source_id"), row.get("speaker_id")),
        "session": (row.get("source_id"), row.get("session_id")),
        "text": row.get("text_normalized"),
        "language": row.get("primary_language"),
        "license": row.get("license_policy"),
        "consent": row.get("consent_id"),
        "release": row.get("dataset_release"),
    }


def audit(train_manifests: dict[str, Path], eval_manifests: dict[str, Path]) -> dict[str, Any]:
    train = defaultdict(lambda: {"checksums": set(), "speakers": set(), "sessions": set(),
                                 "texts": defaultdict(int), "rows": 0})
    licences: dict[str, dict[str, Any]] = {}
    for name, path in sorted(train_manifests.items()):
        rows = load_rows(path)
        seen_lic = defaultdict(int)
        for row in rows:
            k = _key_fields(row)
            bucket = train[k["language"]]
            bucket["rows"] += 1
            if k["checksum"]:
                bucket["checksums"].add(k["checksum"])
            if k["speaker"][1] is not None:
                bucket["speakers"].add(k["speaker"])
            if k["session"][1] is not None:
                bucket["sessions"].add(k["session"])
            if k["text"]:
                bucket["texts"][k["text"]] += 1
            seen_lic[(str(k["license"]), str(k["consent"]), str(k["release"]))] += 1
        licences[name] = {
            "zone": "train",
            "rows": len(rows),
            "declarations": [
                {"license_policy": lic, "consent_id": consent, "dataset_release": release, "rows": count}
                for (lic, consent, release), count in sorted(seen_lic.items())
            ],
            "research_only_flag": any(
                any(marker in lic.lower() for marker in RESEARCH_ONLY_MARKERS)
                for (lic, _, _) in seen_lic
            ),
        }

    languages: dict[str, Any] = {}
    text_collisions_total = 0
    violations = []
    for name, path in sorted(eval_manifests.items()):
        rows = load_rows(path)
        seen_lic = defaultdict(int)
        for row in rows:
            k = _key_fields(row)
            seen_lic[(str(k["license"]), str(k["consent"]), str(k["release"]))] += 1
            language = k["language"]
            bucket = train.get(language)
            entry = languages.setdefault(language, {
                "eval_rows": 0,
                "train_rows": bucket["rows"] if bucket else 0,
                "audio_checksum_overlap": 0,
                "speaker_overlap": [],
                "session_overlap": [],
                "exact_text_collisions": 0,
            })
            entry["eval_rows"] += 1
            if bucket is None:
                continue
            if k["checksum"] in bucket["checksums"]:
                entry["audio_checksum_overlap"] += 1
                violations.append({"kind": "AUDIO_CONTENT", "language": language,
                                   "manifest": name, "checksum": k["checksum"]})
            if k["speaker"][1] is not None and k["speaker"] in bucket["speakers"]:
                if k["speaker"] not in [tuple(s) for s in entry["speaker_overlap"]]:
                    entry["speaker_overlap"].append(list(k["speaker"]))
                    violations.append({"kind": "SPEAKER", "language": language,
                                       "manifest": name, "speaker": list(k["speaker"])})
            if k["session"][1] is not None and k["session"] in bucket["sessions"]:
                if k["session"] not in [tuple(s) for s in entry["session_overlap"]]:
                    entry["session_overlap"].append(list(k["session"]))
                    violations.append({"kind": "SESSION", "language": language,
                                       "manifest": name, "session": list(k["session"])})
            if k["text"] and bucket["texts"].get(k["text"]):
                entry["exact_text_collisions"] += 1
                text_collisions_total += 1
        licences[name] = {
            "zone": "eval",
            "rows": len(rows),
            "declarations": [
                {"license_policy": lic, "consent_id": consent, "dataset_release": release, "rows": count}
                for (lic, consent, release), count in sorted(seen_lic.items())
            ],
            "research_only_flag": any(
                any(marker in lic.lower() for marker in RESEARCH_ONLY_MARKERS)
                for (lic, _, _) in seen_lic
            ),
        }

    hard = [v for v in violations if v["kind"] in ("AUDIO_CONTENT", "SPEAKER", "SESSION")]
    return {
        "record": "GB1_EVAL_ISOLATION_AND_LICENCE_AUDIT",
        "schema_version": 1,
        "status": "PASS_ISOLATED" if not hard else "ISOLATION_VIOLATIONS_FOUND",
        "train_manifests": len(train_manifests),
        "eval_manifests": len(eval_manifests),
        "languages": {k: languages[k] for k in sorted(languages)},
        "violations": hard,
        "exact_text_collisions_total": text_collisions_total,
        "licence_inventory": licences,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train-dir", type=Path, required=True,
                        help="directory of gb1 manifest copies (curated__*.jsonl)")
    parser.add_argument("--eval-dir", type=Path, required=True,
                        help="root of the frozen eval manifest tree")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    train = {p.name: p for p in sorted(args.train_dir.glob("*.jsonl"))}
    evals = {str(p.relative_to(args.eval_dir)): p
             for p in sorted(args.eval_dir.rglob("manifest*.jsonl"))}
    report = audit(train, evals)
    body = json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode() + b"\n"
    if args.output:
        args.output.write_bytes(body)
    print(f"{report['status']}: {report['train_manifests']} train x {report['eval_manifests']} eval manifests, "
          f"{len(report['violations'])} hard violations, "
          f"{report['exact_text_collisions_total']} exact text collisions")
    for v in report["violations"][:10]:
        print("  ", v)
    return 0 if report["status"] == "PASS_ISOLATED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
