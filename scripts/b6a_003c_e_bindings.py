#!/usr/bin/env python3
"""Persist the immutable local-bindings receipt before any 003C-E mutation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from pipeline.runtime_receipts_v2 import ReceiptStore
from scripts import b6a_003c_e_common as common


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "platform/runtime-receipt-policy-v2.yaml"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--packet-sha256", required=True)
    parser.add_argument("--workload", type=Path, required=True)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--receipts-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        record = common.authorization(args.authorization, args.packet_sha256, ROOT)
        if common.sha256_file(args.workload) != common.WORKLOAD_SHA256:
            raise common.BindingRefusal("workload render SHA-256 differs")
        if common.sha256_file(args.audio) != common.AUDIO_SHA256:
            raise common.BindingRefusal("synthetic audio SHA-256 differs")
        receipt = ReceiptStore(args.receipts_dir, policy_path=POLICY).persist(
            "local_bindings",
            "PASS",
            {
                "authorization_id": record["id"],
                "authorization_sha256": common.sha256_file(args.authorization),
                "packet_sha256": args.packet_sha256,
                "workload_render_sha256": common.WORKLOAD_SHA256,
                "synthetic_audio_sha256": common.AUDIO_SHA256,
                "source_binding_count": len(record["source_bindings"]),
                "independent_review": record["independent_review"],
            },
            dependencies=(),
        )
        print(json.dumps(receipt, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "REFUSED", "error": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
