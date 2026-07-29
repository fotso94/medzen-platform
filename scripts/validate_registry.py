#!/usr/bin/env python3
"""A4 registry validator — the enforcement behind "the registry is the only
path to production".

Schema validation is the easy half. The half that matters is the READINESS
LADDER: a language may only hold a status it has earned evidence for. This is
what makes it impossible to switch a language on by accident, enthusiasm or a
demo deadline.

    declared        named, nothing built
    in_development  data acquired, licences recorded
    gated           artifact + evidence-backed decode strategy + gates passed
    production      everything above, plus an approved consented voice and
                    commercially clear licences on every data source

    python scripts/validate_registry.py
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
LANG_DIR = ROOT / "registry" / "languages"
GATE_DIR = ROOT / "registry" / "gates"
SCHEMA = ROOT / "schemas" / "language.schema.json"

# Licence values that permit shipping a commercial product
COMMERCIAL_OK = {"commercial_ok", "own_consented"}

fails: list[str] = []
warns: list[str] = []


def fail(m: str) -> None: fails.append(m)
def warn(m: str) -> None: warns.append(m)


def load_gates(ref: str) -> dict | None:
    """Resolve gates/<file>.yaml and merge it over _defaults.yaml."""
    path = ROOT / "registry" / ref
    if not path.exists():
        return None
    doc = yaml.safe_load(path.read_text()) or {}
    merged: dict = {}
    if doc.get("inherits"):
        base = GATE_DIR / doc["inherits"]
        if not base.exists():
            return None
        merged = yaml.safe_load(base.read_text()) or {}
    for section, vals in (doc.get("overrides") or {}).items():
        merged.setdefault(section, {}).update(vals)
    return merged


def readiness(lang: dict, name: str, gates: dict | None) -> None:
    """The ladder. Each status asserts what must already be true."""
    st = lang["status"]
    asr, tts, prov = lang["asr"], lang["tts"], lang["provenance"]
    ds = asr["decode_strategy"]
    lic = prov["licence_status"]

    # --- every status ---------------------------------------------------
    if gates is None:
        fail(f"{name}: thresholds_ref '{lang['thresholds_ref']}' does not resolve")
    for src in prov["data_sources"]:
        if src not in lic:
            fail(f"{name}: data source '{src}' has no licence_status entry")

    if st == "declared":
        return

    # --- in_development and above ---------------------------------------
    if all(v == "pending_written_terms" for v in lic.values()):
        fail(f"{name}: status '{st}' but every data source is still "
             f"pending_written_terms — nothing may be ingested yet")

    if st == "in_development":
        return

    # --- gated and above --------------------------------------------------
    if not asr["artifact"]:
        fail(f"{name}: status '{st}' requires an ASR artifact")
    if not asr["approved_version"]:
        fail(f"{name}: status '{st}' requires approved_version")
    if ds["mode"] == "pending_experiment":
        fail(f"{name}: status '{st}' but decode_strategy is still "
             f"pending_experiment — run the B3 experiment first (correction 3)")
    if ds["mode"] != "pending_experiment" and not ds["chosen_by_run"]:
        fail(f"{name}: decode_strategy.mode='{ds['mode']}' with no chosen_by_run "
             f"— a decode strategy without evidence is a guess (correction 3)")
    if ds["chosen_by_run"] and not ds["frozen_eval"]:
        fail(f"{name}: decode_strategy has a run but no frozen_eval — the choice "
             f"is not reproducible")

    if st == "gated":
        return

    # --- production only ---------------------------------------------------
    if not tts["approved"]:
        fail(f"{name}: status 'production' requires tts.approved=true")
    if tts["approved"] and not tts["consent_record"]:
        fail(f"{name}: tts.approved=true with no consent_record — a production "
             f"voice needs a contract, and marketplace voices never graduate")
    bad = {s: v for s, v in lic.items() if v not in COMMERCIAL_OK}
    if bad:
        fail(f"{name}: status 'production' but these sources are not "
             f"commercially clear: {bad}")


def main() -> int:
    try:
        import jsonschema
        validator = jsonschema.Draft202012Validator(json.loads(SCHEMA.read_text()))
    except ImportError:
        validator = None
        warn("jsonschema not installed — schema validation skipped")

    files = sorted(LANG_DIR.glob("*.yaml"))
    if not files:
        print("FAIL  registry/languages/ is empty")
        return 1

    langs: dict[str, dict] = {}
    for f in files:
        doc = yaml.safe_load(f.read_text())
        name = f.stem
        if validator:
            for e in sorted(validator.iter_errors(doc), key=lambda e: list(e.path)):
                loc = "/".join(str(p) for p in e.path) or "(root)"
                fail(f"{name} schema [{loc}]: {e.message}")
        if doc.get("alias") != name:
            fail(f"{name}: alias '{doc.get('alias')}' must match filename")
        langs[name] = doc

    # aliases and ISO codes unique
    for key in ("alias", "iso_code"):
        seen: dict[str, str] = {}
        for n, d in langs.items():
            v = d.get(key)
            if v in seen:
                fail(f"duplicate {key} '{v}' in {seen[v]} and {n}")
            seen[v] = n

    # fallbacks resolve and do not cycle
    for n, d in langs.items():
        fb = d.get("fallback_language")
        if fb is None:
            continue
        if fb not in langs:
            fail(f"{n}: fallback_language '{fb}' is not a registered language")
            continue
        seen_chain, cur = {n}, fb
        while cur:
            if cur in seen_chain:
                fail(f"{n}: fallback chain cycles through '{cur}'")
                break
            seen_chain.add(cur)
            cur = langs.get(cur, {}).get("fallback_language")

    # readiness ladder
    for n, d in langs.items():
        readiness(d, n, load_gates(d["thresholds_ref"]))

    # ---- report -----------------------------------------------------------
    print(f"registry: {len(langs)} language(s)\n")
    print(f"  {'LANGUAGE':<10} {'ISO':<5} {'CAPABILITY':<14} {'STATUS':<12} "
          f"{'DECODE':<20} COMMERCIAL")
    print("  " + "-" * 78)
    counts = collections.Counter()
    for n, d in sorted(langs.items()):
        cap = d.get("capabilities", {})
        a_ok = cap.get("asr", {}).get("available")
        t_ok = cap.get("tts", {}).get("available")
        profile = ("full turn" if a_ok and t_ok else
                   "listen only" if a_ok else
                   "speak only" if t_ok else "NEITHER")
        counts[profile] += 1
        lic = d["provenance"]["licence_status"].values()
        licsum = "clear" if all(v in COMMERCIAL_OK for v in lic) else "blocked"
        counts[f"licence:{licsum}"] += 1
        print(f"  {n:<10} {d['iso_code']:<5} {profile:<14} {d['status']:<12} "
              f"{d['asr']['decode_strategy']['mode']:<20} {licsum}")
    print(f"\n  capability: {counts['full turn']} full turn · "
          f"{counts['listen only']} listen only · {counts['speak only']} speak only"
          f"{f' · {counts[chr(78)+chr(69)+chr(73)+chr(84)+chr(72)+chr(69)+chr(82)]} NEITHER' if counts['NEITHER'] else ''}")
    print(f"  licence:    {counts['licence:clear']} commercially clear · "
          f"{counts['licence:blocked']} blocked")
    print()
    for w in warns:
        print(f"  WARN  {w}")
    for f_ in fails:
        print(f"  FAIL  {f_}")
    if fails:
        print(f"\n{len(fails)} violation(s) — registry REJECTED")
        return 1
    print(f"\nOK — registry consistent"
          f"{f' ({len(warns)} warning(s))' if warns else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
