#!/usr/bin/env python3
"""Evaluation-manifest validator — Codex review #19 finding 1.

The first pidgin held-out manifests were carved from TRAINING rows and
carried split=train + allowed_use=["asr_train"] verbatim into eval keys:
physically speaker-separated, but labeled as training data. Any future
automation trusting the labels (ingest joins, curated-path discovery,
mixture builders) could have folded a sealed set back into training.

Every row of an evaluation manifest MUST satisfy:
  - split == "test"
  - "asr_eval" in allowed_use
  - "asr_train" NOT in allowed_use

`validate_rows` refuses on the first violation. Curation tooling calls
this BEFORE upload; the promotion checker requires holdout records to
declare the validator's verdict; tests exercise both directions.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

REQUIRED_SPLIT = "test"
REQUIRED_USE = "asr_eval"
FORBIDDEN_USE = "asr_train"


class EvalManifestViolation(ValueError):
    pass


def validate_rows(rows: Iterable[dict], source: str = "<rows>") -> int:
    count = 0
    for i, row in enumerate(rows):
        count += 1
        split = row.get("split")
        allowed = row.get("allowed_use")
        # Codex review #20: a string ("asr_eval") or dict here satisfied
        # the membership checks by substring/key accident — the field's
        # TYPE is part of the contract
        if not (isinstance(allowed, list)
                and all(isinstance(x, str) for x in allowed)):
            raise EvalManifestViolation(
                f"{source} row {i}: allowed_use must be a list of "
                f"strings, got {type(allowed).__name__}")
        if split != REQUIRED_SPLIT:
            raise EvalManifestViolation(
                f"{source} row {i}: split={split!r} — evaluation rows must "
                f"be split={REQUIRED_SPLIT!r}; a train-labeled row inside "
                f"an eval manifest is how a sealed set leaks back into "
                f"training")
        if FORBIDDEN_USE in allowed:
            raise EvalManifestViolation(
                f"{source} row {i}: allowed_use contains "
                f"{FORBIDDEN_USE!r} — an evaluation row must never grant "
                f"training use")
        if REQUIRED_USE not in allowed:
            raise EvalManifestViolation(
                f"{source} row {i}: allowed_use {allowed!r} lacks "
                f"{REQUIRED_USE!r}")
    if count == 0:
        raise EvalManifestViolation(f"{source}: empty manifest")
    return count


def validate_file(path: Path) -> int:
    with open(path) as f:
        return validate_rows(
            (json.loads(line) for line in f if line.strip()), str(path))


if __name__ == "__main__":
    import sys
    n = validate_file(Path(sys.argv[1]))
    print(json.dumps({"status": "PASS_EVAL_ONLY", "rows": n}))
