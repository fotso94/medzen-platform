"""ARCH-2026-001 governance: the records are machine-consumed (Codex
review #6: 'no tests or scripts consume the new protocol'). These tests
bind the architecture's load-bearing statements so silent drift fails CI."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCH = json.loads((ROOT / "platform/decisions/"
                   "ARCH-2026-001-one-multilingual-production-model.json").read_bytes())
PROTOCOL = json.loads((ROOT / "platform/decisions/"
                       "PROMOTION-PROTOCOL-2026-001.json").read_bytes())
PILOT = (ROOT / "platform/decisions/"
         "B5-UNIVERSAL-PILOT-DESIGN-2026-001.md").read_text()


def test_architecture_record_binds_the_owner_decision():
    assert ARCH["status"] == "OWNER_DECIDED"
    arch = ARCH["architecture"]
    assert "ONE active multilingual" in arch["production_artifact"]
    assert arch["language_routing"].startswith("NO external")
    assert "MANDATORY" in arch["language_hints"]
    assert "RESEARCH TEACHERS ONLY" in arch["per_language_models"]
    assert "no kinyarwanda-specific serving path" in (
        ARCH["kinyarwanda_reframe"]["serving_path"])


def test_the_two_variant_deployment_decision_is_superseded():
    base_model = (ROOT / "platform/decisions/"
                  "B6-ASR-BASE-MODEL-2026-001.md").read_text()
    assert "SUPERSESSION NOTE" in base_model
    assert "ARCH-2026-001" in base_model
    assert "OFFLINE accuracy comparator only" in base_model


def test_pilot_design_declares_gb6_and_two_tier_evaluation():
    assert "gb6" in PILOT, "the pilot must not claim gb5 suffices"
    assert "trainable for kinyarwanda ONLY" in PILOT
    assert "TIER 2 (promotion)" in PILOT
    assert "consumed EXACTLY ONCE" in PILOT
    for language in ("kinyarwanda", "english", "french", "swahili",
                     "lingala", "ewe"):
        assert language in PILOT


def test_promotion_protocol_gates_are_bound():
    assert any("PREDECLARES" in p for p in PROTOCOL["principles"])
    assert "MANDATORY FOR THE FIRST PRODUCTION PROMOTION" in (
        PROTOCOL["gates_phased_blocking_when_assets_exist"]["code_switch"])
    assert "aggregate WER alone can NEVER promote" in PROTOCOL["principles"][0]
    assert "test_arch_2026_001" in PROTOCOL["machine_enforcement"]


def test_trainer_enforces_what_the_pilot_promises():
    """The two machine gates the pilot leans on must exist in code, not
    prose: the multilingual-full ack and the coverage refusal."""
    trainer = (ROOT / "pipeline/omniasr_train.py").read_text()
    assert "MEDZEN_MULTILINGUAL_FULL_ACK" in trainer
    mix = (ROOT / "pipeline/train_asr.py").read_text()
    assert "a partial mixture" in mix


def test_no_per_language_serving_digest_may_enter_the_registry():
    """ARCH-2026-001 one-global-digest tripwire (Codex review #6 rec 6):
    language registry files must never grow per-language serving-artifact
    digest bindings — the production digest is bound once, globally, at
    the serving-contract step. Full enforcement arrives with that step;
    this trips any earlier drift."""
    import yaml
    # Codex review #7: the first version of this test forbade INVENTED
    # field names and missed the real ones. Real semantics: per-language
    # APPROVAL fields are allowed (approved_version — the A4 mechanism);
    # what must never diverge is a SERVING artifact digest. Collect every
    # digest-bearing artifact field across languages' asr sections — if
    # any exist, they must all be identical.
    digest_fields = {"artifact", "artifact_tree_sha256", "artifact_sha256",
                     "serving_artifact", "asr_digest", "model_digest",
                     "artifact_digest", "serving_model"}
    seen: dict[str, list[str]] = {}
    for path in sorted((ROOT / "registry/languages").glob("*.yaml")):
        doc = yaml.safe_load(path.read_text()) or {}
        def walk(node, trail):
            if isinstance(node, dict):
                for key, value in node.items():
                    if key in digest_fields and isinstance(value, str):
                        seen.setdefault(value, []).append(
                            f"{path.name}:{'.'.join(trail + [key])}")
                    walk(value, trail + [key])
            elif isinstance(node, list):
                for item in node:
                    walk(item, trail)
        walk(doc, [])
    assert len(seen) <= 1, (
        f"ARCH-2026-001: {len(seen)} DIFFERENT artifact digests bound across "
        f"language files — production binds ONE digest globally: {seen}")


def test_tier2_holdout_record_binds_every_pilot_language():
    holdouts = json.loads((ROOT / "platform/evidence/"
                           "B5-TIER2-HOLDOUTS-2026-001.json").read_bytes())
    assert set(holdouts["pools"]) == {"english", "ewe", "french", "lingala",
                                       "swahili"}
    for language, pools in holdouts["pools"].items():
        for pool in pools:
            assert pool["sealed_vs_gb6_overlap"]["byte"] == 0
            assert pool["sealed_vs_gb6_overlap"]["session"] == 0
            assert pool["tier2-sealed"]["rows"] > 0
    assert "french_aaf_text_overlap" in holdouts["honest_limitations"]


def test_gb6_covers_every_pilot_language_physically():
    gb6 = json.loads((ROOT / "platform/evidence/"
                      "B5-GB6-COMPLETE-2026-001.json").read_bytes())
    covered = {k.split("/")[0] for k in gb6["manifests"]}
    assert {"kinyarwanda", "english", "french", "swahili", "lingala",
            "ewe"} <= covered
    for entry in gb6["manifests"].values():
        assert "/gb6/manifest.jsonl" in entry["key"], (
            "gb6 entries must be PHYSICAL /gb6/ paths — the trainer "
            "resolves by path")


def test_promotion_checker_refuses_a_fabricated_bare_pass(tmp_path):
    """Codex reviews #7-#9: the gate now (a) uses per-method strict
    schemas, (b) only accepts AUTHORITATIVE holdout identities, and
    (c) RECOMPUTES statistics from hash-bound rows — a report that is not
    derived from real rows cannot pass."""
    import hashlib
    import subprocess
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    from noninferiority import (clustered_noninferiority,
                                 clustered_relative_improvement)

    def run(report_path, results_dir):
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts/b7_model_promotion_check.py"),
             "--gate-report", str(report_path),
             "--results-dir", str(results_dir)],
            capture_output=True, text=True)

    # authoritative sealed identities from the committed evidence
    tier2 = json.loads((ROOT / "platform/evidence/"
                        "B5-TIER2-HOLDOUTS-2026-001.json").read_bytes())
    bindings = json.loads((ROOT / "platform/evidence/"
                           "B5-IMMUTABILITY-BINDINGS-2026-001.json").read_bytes())
    sealed_sha = {lang: pools[0]["tier2-sealed"]["sha256"]
                  for lang, pools in tier2["pools"].items()}
    sealed_sha["kinyarwanda"] = (bindings["universal_kinyarwanda_holdout"]
                                  ["universal-sealed"]["sha256"])

    results = tmp_path / "results"
    results.mkdir()
    languages = {}
    for lang, sha in sorted(sealed_sha.items()):
        if lang == "kinyarwanda":
            rows = [{"cluster_id": f"s{i % 10}", "baseline_errors": 10,
                     "candidate_errors": 8, "reference_words": 25}
                    for i in range(200)]
            stats = clustered_relative_improvement(
                rows, min_relative_gain=0.093, iterations=2000)
            block_name = "improvement"
        else:
            rows = [{"cluster_id": f"s{i % 10}", "baseline_errors": 5,
                     "candidate_errors": 5, "reference_words": 20}
                    for i in range(200)]
            stats = clustered_noninferiority(rows, margin=0.02,
                                              iterations=2000)
            block_name = "non_inferiority"
        body = "".join(json.dumps(r) + "\n" for r in rows).encode()
        (results / f"{lang}.rows.jsonl").write_bytes(body)
        languages[lang] = {
            "state": "PASS", "holdout_manifest_sha256": sha,
            "rows_sha256": hashlib.sha256(body).hexdigest(),
            block_name: stats}
    report = {
        "schema_version": 1, "protocol_id": "PROMOTION-PROTOCOL-2026-001",
        "candidate_digest": "sha256:" + "a" * 64,
        "code_switch_evidence": {"state": "PASS", "set": "licensed-cs-1",
                                  "manifest_sha256": "c" * 64, "rows": 500},
        "operational_evidence": {"state": "PASS", "latency_p95_ms": 800,
                                  "vram_gb": 18},
        "languages": languages,
        "gate_state_counts": {"PASS": len(languages)}}
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report))
    proc = run(path, results)
    assert proc.returncode == 0, proc.stdout

    # (1) fabricated stats not derived from the rows -> refused
    poisoned = json.loads(json.dumps(report))
    poisoned["languages"]["english"]["non_inferiority"]["upper_ci"] = -0.5
    path.write_text(json.dumps(poisoned))
    assert run(path, results).returncode == 1
    # (2) unknown holdout identity -> refused
    poisoned = json.loads(json.dumps(report))
    poisoned["languages"]["french"]["holdout_manifest_sha256"] = "d" * 64
    path.write_text(json.dumps(poisoned))
    assert run(path, results).returncode == 1
    # (3) wrong-schema relative block (Codex #9: the field mismatch) ->
    # a LEGITIMATE relative result passes above; an absolute-field fake
    # in the improvement slot refuses
    poisoned = json.loads(json.dumps(report))
    poisoned["languages"]["kinyarwanda"]["improvement"] = {
        "margin": 0.093, "upper_ci": -0.2, "clusters": 10, "rows": 200,
        "method": "paired_clustered_bootstrap", "non_inferior": True,
        "seed": 1, "iterations": 2000, "alpha": 0.05}
    path.write_text(json.dumps(poisoned))
    assert run(path, results).returncode == 1
    # (4) bare evidence blocks -> refused
    poisoned = json.loads(json.dumps(report))
    poisoned["code_switch_evidence"] = {"state": "PASS"}
    path.write_text(json.dumps(poisoned))
    assert run(path, results).returncode == 1
    # (5) missing rows file -> refused
    proc = run(tmp_path / "report.json", tmp_path / "empty")
    assert proc.returncode == 1


def test_relative_improvement_validator_and_input_validation():
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import importlib
    import noninferiority
    importlib.reload(noninferiority)

    better = [{"cluster_id": f"s{i % 12}", "baseline_errors": 10,
               "candidate_errors": 8, "reference_words": 25}
              for i in range(240)]
    verdict = noninferiority.clustered_relative_improvement(
        better, min_relative_gain=0.093)
    assert verdict["improved"] is True          # uniform 20% gain > 9.3%
    barely = [{"cluster_id": f"s{i % 12}", "baseline_errors": 10,
               "candidate_errors": 9.5, "reference_words": 25}
              for i in range(240)]
    verdict = noninferiority.clustered_relative_improvement(
        barely, min_relative_gain=0.093)
    assert verdict["improved"] is False         # 5% gain < 9.3%
    import pytest as _pytest
    with _pytest.raises(ValueError, match="invalid paired row"):
        noninferiority.clustered_noninferiority(
            [{"cluster_id": "a", "baseline_errors": -1,
              "candidate_errors": 0, "reference_words": 10},
             {"cluster_id": "b", "baseline_errors": 1,
              "candidate_errors": 1, "reference_words": 10}], margin=0.01)


def test_clustered_noninferiority_validator_behaves():
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    from noninferiority import clustered_noninferiority

    equal = [{"cluster_id": f"s{i % 10}", "baseline_errors": 5,
              "candidate_errors": 5, "reference_words": 20}
             for i in range(200)]
    verdict = clustered_noninferiority(equal, margin=0.01)
    assert verdict["non_inferior"] is True
    assert verdict["clusters"] == 10
    worse = [{"cluster_id": f"s{i % 10}", "baseline_errors": 5,
              "candidate_errors": 9, "reference_words": 20}
             for i in range(200)]
    verdict = clustered_noninferiority(worse, margin=0.01)
    assert verdict["non_inferior"] is False, (
        "a uniform +20% error increase must fail a 1-point margin")
    # determinism under the declared seed
    again = clustered_noninferiority(worse, margin=0.01)
    assert again["upper_ci"] == verdict["upper_ci"]


def test_holdout_consumption_ledger_is_append_only_and_unique(tmp_path):
    """Codex review #9: the ledger is now an EXECUTABLE gate — hash-chained
    against history rewriting, refusing double consumption."""
    import shutil
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import holdout_ledger
    from holdout_ledger import (LedgerRefusal, record_consumption,
                                 require_available, verify_chain)

    entries = verify_chain()          # the committed ledger chain is valid
    assert entries[0]["event"] == "LEDGER_OPENED"
    assert any("cv17-test-v1-universal-sealed" in json.dumps(e)
               for e in entries)

    work = tmp_path / "ledger.jsonl"
    shutil.copy(ROOT / "platform/evidence/HOLDOUT-CONSUMPTION-LEDGER.jsonl",
                work)
    require_available("eval/x/sealed/manifest.jsonl", work)
    record_consumption("eval/x/sealed/manifest.jsonl", "e" * 64,
                        "test-gate-run", work)
    import pytest as _pytest
    with _pytest.raises(LedgerRefusal, match="already CONSUMED"):
        record_consumption("eval/x/sealed/manifest.jsonl", "e" * 64,
                            "second-attempt", work)
    # history rewriting breaks the chain
    lines = work.read_text().splitlines()
    tampered = json.loads(lines[1])
    tampered["reserved_for"] = "something else entirely"
    lines[1] = json.dumps(tampered, sort_keys=True)
    work.write_text("\n".join(lines) + "\n")
    with _pytest.raises(LedgerRefusal, match="chain broken"):
        verify_chain(work)


def test_registry_router_refuses_divergent_asr_digests():
    """Codex review #8 reproduction: RegistryRouter accepted english and
    french bound to DIFFERENT ASR digests. The load-time guard refuses."""
    import sys
    sys.path.insert(0, str(ROOT / "services/speech-orchestrator"))
    from medzen_speech_orchestrator.registry import (RegistryRefusal,
                                                      enforce_single_asr_digest)

    class Route:
        def __init__(self, digest):
            self.asr_artifact_tree_sha256 = digest

    import pytest as _pytest
    with _pytest.raises(RegistryRefusal, match="MULTIPLE ASR artifact"):
        enforce_single_asr_digest({"english": Route("a" * 64),
                                    "french": Route("b" * 64)})
    enforce_single_asr_digest({"english": Route("a" * 64),
                                "french": Route("a" * 64)})   # same digest OK
    enforce_single_asr_digest({"english": Route(None),
                                "french": Route(None)})       # local mode OK
