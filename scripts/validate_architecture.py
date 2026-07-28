#!/usr/bin/env python3
"""Consistency checks for A1/A2. Run in CI before Terraform (B1) or deploy (B6).

Catches the failure mode this repo exists to prevent: services.yaml, the
generated IAM/k8s, and the architecture document drifting apart silently.

    python scripts/validate_architecture.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "platform" / "services.yaml"
IAM = ROOT / "platform" / "iam"
K8S = ROOT / "platform" / "k8s" / "base"

fails: list[str] = []
warns: list[str] = []


def check(cond: bool, msg: str, warn: bool = False) -> None:
    if not cond:
        (warns if warn else fails).append(msg)


def main() -> int:
    spec = yaml.safe_load(SPEC.read_text())
    meta, svcs = spec["meta"], spec["services"]
    inits = spec.get("init_containers", {})
    offline = spec.get("offline", {})

    # ---- placeholders must be resolved before Terraform ------------------
    check("REPLACE" not in meta["account_id"],
          f"meta.account_id is unresolved ({meta['account_id']}) — close B0.1 "
          f"before running Terraform")

    # ---- every service has an artifact set --------------------------------
    for name, s in svcs.items():
        check((IAM / f"{s['pod_identity']['role_name']}.json").exists(),
              f"{name}: missing generated IAM policy")
        check((K8S / f"{name}.yaml").exists(),
              f"{name}: missing generated k8s manifest")
        check("readiness" in s["probes"], f"{name}: no readiness probe")
        check("limits" in s["resources"] and "requests" in s["resources"],
              f"{name}: resources must set both requests and limits")

    # ---- exactly one public service ---------------------------------------
    public = [n for n, s in svcs.items() if s.get("public")]
    check(public == ["speech-orchestrator"],
          f"exactly one public service expected (speech-orchestrator), got {public} "
          f"— A1 invariant 1")

    # ---- GPU services are correctly constrained ---------------------------
    for name, s in svcs.items():
        if s["node_pool"] == "gpu":
            check(bool(s.get("gpu", {}).get("toleration")),
                  f"{name}: GPU pool service without a toleration will not schedule")
            check("nvidia.com/gpu" in s["resources"]["limits"],
                  f"{name}: GPU service must request nvidia.com/gpu")
            check("startup" in s["probes"],
                  f"{name}: GPU service needs a startupProbe (model load 30-60s)")
            sp = s["probes"].get("startup", {})
            budget = sp.get("period", 0) * sp.get("failure_threshold", 0)
            check(budget >= 240,
                  f"{name}: startupProbe budget {budget}s is under the 4-min "
                  f"minimum for a 6-8 GB image + model load")
            check(s["hpa"] is None,
                  f"{name}: GPU autoscaling is out of scope for the MVP", warn=True)

    # ---- dependencies resolve ---------------------------------------------
    for name, s in svcs.items():
        for dep in s.get("depends_on", []):
            check(dep in svcs, f"{name}: depends_on unknown service '{dep}'")

    # ---- training isolation (A1 invariant 2) ------------------------------
    for name, o in offline.items():
        pol = IAM / f"{o['iam_role']}.json"
        check(pol.exists(), f"{name}: missing IAM policy")
        if pol.exists():
            doc = json.loads(pol.read_text())
            denied = {a for st in doc["Statement"] if st["Effect"] == "Deny"
                      for a in st["Action"]}
            for must in ("secretsmanager:*", "bedrock:*"):
                check(must in denied,
                      f"{name}: missing explicit Deny on {must} — A1 invariant 2")
            # eval/* must be write-denied
            evaldeny = any(st["Effect"] == "Deny" and "s3:PutObject" in st["Action"]
                           and any("eval/" in r for r in st["Resource"])
                           for st in doc["Statement"])
            check(evaldeny, f"{name}: eval/ prefix is not write-denied — A3")

    # ---- init containers referenced exist ---------------------------------
    for name, s in svcs.items():
        ic = s.get("init_container")
        if ic:
            check(ic in inits, f"{name}: unknown init_container '{ic}'")

    # ---- no orphan generated files ----------------------------------------
    expected_iam = {f"{s['pod_identity']['role_name']}.json"
                    for s in list(svcs.values()) + list(inits.values())}
    expected_iam |= {f"{o['iam_role']}.json" for o in offline.values()}
    for f in IAM.glob("*.json"):
        check(f.name in expected_iam,
              f"orphan IAM policy {f.name} — not in services.yaml", warn=True)

    # ---- report ------------------------------------------------------------
    for w in warns:
        print(f"  WARN  {w}")
    for f in fails:
        print(f"  FAIL  {f}")
    n = len(svcs) + len(inits) + len(offline)
    if fails:
        print(f"\n{len(fails)} failure(s) across {n} components")
        return 1
    print(f"\nOK — {n} components consistent"
          f"{f', {len(warns)} warning(s)' if warns else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
