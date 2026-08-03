#!/usr/bin/env python3
"""Generate the deterministic, five-state B5 report for the current artifact."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.b5_gates import (  # noqa: E402
    ROOT,
    evaluate_current_artifact,
    write_content_addressed_report,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir", type=Path,
        default=ROOT / "platform/evidence/b5/gate-reports",
        help="local immutable report directory")
    parser.add_argument(
        "--check-only", action="store_true",
        help="evaluate and print canonical JSON without writing")
    args = parser.parse_args()

    report = evaluate_current_artifact()
    if args.check_only:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        receipt = write_content_addressed_report(report, args.output_dir)
        print(json.dumps({"overall": report["overall"], **receipt},
                         indent=2, sort_keys=True))
    return 1 if report["overall"] == "BLOCKED" else 0


if __name__ == "__main__":
    sys.exit(main())
