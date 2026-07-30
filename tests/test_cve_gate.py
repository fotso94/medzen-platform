"""The exception-aware scan gate.

Two full images were built, scanned and REJECTED before this gate existed in its
current form, and every one of the 15 findings had no fix available. The response
was named, justified, expiring exceptions -- not a raised threshold.

That distinction is only real if the gate still fails. These tests run the gate's
actual logic, extracted from build_image.sh, against the committed scan evidence:
it must waive exactly the 15 known findings and still fail on a new finding, a
finding that has since gained a fix, an expired waiver, or a waiver naming the
wrong package.
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "pipeline" / "build_image.sh"
ALLOWLIST = ROOT / "platform" / "cve_allowlist.json"
EVIDENCE = ROOT / "platform" / "evidence"
TRIXIE_SCAN = EVIDENCE / "scan-trixie-20befc20.json"


@pytest.fixture(scope="module")
def gate(tmp_path_factory) -> Path:
    """The gate as it actually ships, lifted out of the build script.

    Testing a copy would prove nothing about what runs on the builder.
    """
    src = BUILD.read_text()
    body = src.split("<<'CHECK_SCAN'\n", 1)[1].split("\nCHECK_SCAN", 1)[0]
    p = tmp_path_factory.mktemp("gate") / "check_scan.py"
    p.write_text(body)
    return p


def run(gate: Path, scan: dict, allow: dict, tmp_path: Path,
        max_crit: int = 0, max_high: int = 0):
    scan_p = tmp_path / "scan.json"
    scan_p.write_text(json.dumps(scan))
    allow_p = tmp_path / "allow.json"
    allow_p.write_text(json.dumps(allow))
    # the gate reads /tmp/scan.json by contract with build_image.sh
    real = Path("/tmp/scan.json")
    backup = real.read_bytes() if real.exists() else None
    try:
        real.write_text(json.dumps(scan))
        return subprocess.run([sys.executable, str(gate), str(max_crit), str(max_high),
                               str(allow_p)], capture_output=True, text=True)
    finally:
        if backup is not None:
            real.write_bytes(backup)
        elif real.exists():
            real.unlink()


@pytest.fixture
def scan() -> dict:
    return json.loads(TRIXIE_SCAN.read_text())


@pytest.fixture
def allow() -> dict:
    return json.loads(ALLOWLIST.read_text())


def _attr(f, key, value):
    f.setdefault("attributes", []).append({"key": key, "value": value})


def test_committed_evidence_matches_the_allowlist(scan, allow):
    """Every finding in the recorded scan must be accounted for by name."""
    found = {f["name"] for f in scan["imageScanFindings"]["findings"]}
    waived = {e["cve"] for e in allow["entries"]}
    assert found <= waived, f"unaccounted findings: {sorted(found - waived)}"
    assert len(found) == 15, f"expected the 15 recorded findings, got {len(found)}"


def test_every_entry_is_justified_and_expiring(allow):
    for e in allow["entries"]:
        assert e["cve"].startswith("CVE-")
        assert e["package"] and e["severity"]
        assert len(e["justification"]) > 80, f"{e['cve']} needs a real justification"
        assert e["expires"], f"{e['cve']} must expire"
        assert "reachable" in e, f"{e['cve']} must state reachability"
    assert allow["review_by"]
    assert allow["base_image"].count("@sha256:") == 1, "base must be digest-pinned"


def test_thresholds_are_still_zero():
    """Exceptions are per-CVE. The thresholds must not have been raised."""
    s = BUILD.read_text()
    assert 'SCAN_MAX_CRITICAL="${SCAN_MAX_CRITICAL:-0}"' in s
    assert 'SCAN_MAX_HIGH="${SCAN_MAX_HIGH:-0}"' in s


def test_known_findings_are_waived(gate, scan, allow, tmp_path):
    r = run(gate, scan, allow, tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "15 finding(s) waived by named exception" in r.stdout


def test_a_new_finding_still_fails(gate, scan, allow, tmp_path):
    s = copy.deepcopy(scan)
    f = {"name": "CVE-2026-99999", "severity": "CRITICAL"}
    _attr(f, "package_name", "openssl")
    s["imageScanFindings"]["findings"].append(f)
    r = run(gate, s, allow, tmp_path)
    assert r.returncode == 1
    assert "CVE-2026-99999" in r.stdout
    assert "1 unwaived" in r.stdout


def test_a_waiver_is_void_once_a_fix_exists(gate, scan, allow, tmp_path):
    """Every justification rests on 'nothing to upgrade to'."""
    s = copy.deepcopy(scan)
    for f in s["imageScanFindings"]["findings"]:
        if f["name"] == "CVE-2026-5450":
            _attr(f, "fixed_in_version", "2.41-13")
    r = run(gate, s, allow, tmp_path)
    assert r.returncode == 1
    assert "FIX NOW AVAILABLE" in r.stdout
    assert "invalid waiver" in r.stdout


def test_an_expired_waiver_fails(gate, scan, allow, tmp_path):
    a = copy.deepcopy(allow)
    for e in a["entries"]:
        if e["cve"] == "CVE-2026-5450":
            e["expires"] = "2026-01-01"
    r = run(gate, scan, a, tmp_path)
    assert r.returncode == 1
    assert "EXPIRED" in r.stdout


def test_a_waiver_for_the_wrong_package_fails(gate, scan, allow, tmp_path):
    """A CVE id is not enough: the waiver must match the package it was written
    for, or a same-id finding elsewhere would inherit the justification."""
    a = copy.deepcopy(allow)
    for e in a["entries"]:
        if e["cve"] == "CVE-2026-5450":
            e["package"] = "openssl"
    r = run(gate, scan, a, tmp_path)
    assert r.returncode == 1
    assert "waiver names package" in r.stdout


def test_failure_message_names_invalid_waivers_distinctly(gate, scan, allow, tmp_path):
    """"CRITICAL 0 unwaived > 0" reads as a bug in the gate rather than a real
    failure; invalid waivers must be reported as such."""
    a = copy.deepcopy(allow)
    for e in a["entries"]:
        if e["cve"] == "CVE-2026-5450":
            e["expires"] = "2026-01-01"
    r = run(gate, scan, a, tmp_path)
    assert "with an invalid waiver" in r.stdout
    assert "0 unwaived > limit" not in r.stdout


def test_missing_allowlist_fails_the_build():
    s = BUILD.read_text()
    assert 'FATAL: no CVE allowlist' in s
    assert "exit 36" in s


def test_allowlist_travels_in_the_bundle():
    """The gate reads it from the verified bundle, so platform/ must ship."""
    s = (ROOT / "scripts" / "publish_bundle.py").read_text()
    assert '"platform"' in s, "platform/ must be in the bundle PATHS"
    assert 'ALLOWLIST="$SRC/platform/cve_allowlist.json"' in BUILD.read_text()


def test_scan_provenance_is_recorded():
    rec = json.loads((EVIDENCE / "scan_records.json").read_text())
    assert len(rec["ecr_scans"]) == 2, "both rejected images must be recorded"
    for s in rec["ecr_scans"]:
        assert s["scanner"] == "aws-ecr-basic" and s["authoritative"] is True
        assert s["image_digest"].startswith("sha256:")
        assert s["scan_completed_utc"], "each scan needs a timestamp"
        assert s["vulnerability_db_updated_utc"]
        assert s["all_findings_unfixed"] is True
    scout = rec["docker_scout_base_comparison"]
    assert scout["authoritative"] is False, "ECR must remain the authority"
    assert rec["python_dependency_audit"]["packages_audited"] == 163


# --------------------------------------------------------------------------- #
# an allowlist is only a record of what was looked at if it is exact
# --------------------------------------------------------------------------- #
def test_every_entry_pins_a_package_version(allow):
    """A waiver is written against a specific package build. Without a version,
    a base-image bump silently carries every justification forward onto
    artifacts nobody reviewed."""
    for e in allow["entries"]:
        assert e.get("package_version"), f"{e['cve']} has no package_version"


def test_entry_versions_and_severities_match_the_recorded_scan(allow, scan):
    by_cve = {}
    for f in scan["imageScanFindings"]["findings"]:
        a = {x["key"]: x["value"] for x in f.get("attributes", [])}
        by_cve[f["name"]] = (f["severity"], a.get("package_name"), a.get("package_version"))
    for e in allow["entries"]:
        sev, pkg, ver = by_cve[e["cve"]]
        assert e["severity"] == sev, f"{e['cve']}: allowlist {e['severity']} != scan {sev}"
        assert e["package"] == pkg
        assert e["package_version"] == ver


def test_a_waiver_for_a_different_package_version_fails(gate, scan, allow, tmp_path):
    a = copy.deepcopy(allow)
    for e in a["entries"]:
        if e["cve"] == "CVE-2026-5450":
            e["package_version"] = "2.41-99+deb13u9"     # a build nobody reviewed
    r = run(gate, scan, a, tmp_path)
    assert r.returncode == 1
    assert "waiver is for glibc" in r.stdout
    assert "scan reports" in r.stdout


def test_a_severity_escalation_voids_the_waiver(gate, scan, allow, tmp_path):
    """A CVE re-rated upward must not stay waived under a justification written
    when it was less severe."""
    s = copy.deepcopy(scan)
    for f in s["imageScanFindings"]["findings"]:
        if f["name"] == "CVE-2026-7010":          # MEDIUM in the allowlist
            f["severity"] = "CRITICAL"
    r = run(gate, s, allow, tmp_path)
    assert r.returncode == 1
    assert "severity changed" in r.stdout


def test_a_passed_review_by_fails_the_whole_gate(gate, scan, allow, tmp_path):
    """Past review_by every waiver is stale by definition, whatever its own
    expiry says."""
    a = copy.deepcopy(allow)
    a["review_by"] = "2026-01-01"
    r = run(gate, scan, a, tmp_path)
    assert r.returncode == 1
    assert "review_by" in r.stdout and "has passed" in r.stdout


def test_a_missing_review_by_fails(gate, scan, allow, tmp_path):
    a = copy.deepcopy(allow)
    del a["review_by"]
    r = run(gate, scan, a, tmp_path)
    assert r.returncode == 1
    assert "no review_by" in r.stdout


def test_stale_entries_fail_rather_than_warn(gate, scan, allow, tmp_path):
    """Leaving entries that match nothing is how an allowlist quietly becomes a
    list of things nobody checks."""
    a = copy.deepcopy(allow)
    a["entries"].append({
        "cve": "CVE-2020-00000", "package": "openssl", "package_version": "1.0",
        "severity": "HIGH", "reachable": False, "expires": "2026-10-28",
        "justification": "x" * 100,
    })
    r = run(gate, scan, a, tmp_path)
    assert r.returncode == 1
    assert "stale allowlist" in r.stdout
    assert "CVE-2020-00000" in r.stdout


def test_a_new_unlisted_medium_fails(gate, scan, allow, tmp_path):
    """Gating only CRITICAL and HIGH let a brand-new MEDIUM through silently."""
    s = copy.deepcopy(scan)
    f = {"name": "CVE-2026-88888", "severity": "MEDIUM"}
    _attr(f, "package_name", "zlib")
    _attr(f, "package_version", "1.3")
    s["imageScanFindings"]["findings"].append(f)
    r = run(gate, s, allow, tmp_path)
    assert r.returncode == 1
    assert "CVE-2026-88888" in r.stdout
    assert "MEDIUM: 1 unwaived" in r.stdout


def test_a_new_unlisted_low_fails(gate, scan, allow, tmp_path):
    s = copy.deepcopy(scan)
    f = {"name": "CVE-2026-77777", "severity": "LOW"}
    _attr(f, "package_name", "zlib")
    _attr(f, "package_version", "1.3")
    s["imageScanFindings"]["findings"].append(f)
    r = run(gate, s, allow, tmp_path)
    assert r.returncode == 1
    assert "LOW: 1 unwaived" in r.stdout


def test_informational_findings_are_reported_but_not_gated(gate, scan, allow, tmp_path):
    """These are not findings that call for action, and gating them would train
    people to raise limits."""
    s = copy.deepcopy(scan)
    f = {"name": "CVE-2026-66666", "severity": "INFORMATIONAL"}
    _attr(f, "package_name", "zlib")
    _attr(f, "package_version", "1.3")
    s["imageScanFindings"]["findings"].append(f)
    r = run(gate, s, allow, tmp_path)
    assert r.returncode == 0, r.stdout
    assert "CVE-2026-66666" in r.stdout, "it must still be visible"


def test_all_gated_severities_default_to_zero():
    s = BUILD.read_text()
    for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
        assert f'"{sev}"' in s, f"{sev} must be gated"
    assert 'SCAN_MAX_MEDIUM", "0"' in s
    assert 'SCAN_MAX_LOW", "0"' in s
