#!/usr/bin/env python3
"""Create a local, content-addressed, explicitly non-promotable B5 manifest."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.b5_gates import ROOT  # noqa: E402
from pipeline.b5_promotion import (  # noqa: E402
    build_current_artifact_dry_run_manifest,
    write_dry_run_manifest,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument(
        "--output-dir", type=Path,
        default=ROOT / "platform/evidence/b5/dry-run-manifests")
    args = parser.parse_args()
    report = json.loads(args.report.read_bytes())
    manifest = build_current_artifact_dry_run_manifest(
        report, args.report.resolve(), ROOT)
    receipt = write_dry_run_manifest(manifest, args.output_dir)
    print(json.dumps({
        **receipt,
        "decision": "BLOCKED",
        "publication": "BLOCKED_NOT_APPLICABLE_FOR_CURRENT_ARTIFACT",
        "production_signature": False,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
