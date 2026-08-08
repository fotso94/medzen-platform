from __future__ import annotations

import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / "platform/evidence/GIT-UNIFICATION-2026-001.json"


def value() -> dict:
    return json.loads(RECORD.read_bytes())


def test_unification_record_has_two_distinct_merged_pull_requests():
    record = value()
    pulls = record["pull_requests"]
    assert [item["number"] for item in pulls] == [1, 2]
    assert all(item["state"] == "MERGED" for item in pulls)
    assert len({item["merge_commit"] for item in pulls}) == 2


def test_both_branch_heads_are_ancestors_of_unified_master():
    record = value()
    master = record["unified_master_commit"]
    for pull in record["pull_requests"]:
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", pull["head_commit"], master],
            cwd=ROOT,
            check=True,
        )
