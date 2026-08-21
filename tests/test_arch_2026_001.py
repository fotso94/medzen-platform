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
                       "PROMOTION-PROTOCOL-2026-002.json").read_bytes())
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
        "schema_version": 1, "protocol_id": "PROMOTION-PROTOCOL-2026-002",
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
    """v4 semantics: reservation-gated consumption, double-consume refusal,
    chain-tamper detection."""
    import hashlib
    import shutil
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import importlib
    import holdout_ledger
    importlib.reload(holdout_ledger)
    import pytest as _pytest

    entries = holdout_ledger.verify_chain()
    assert entries[0]["event"] == "LEDGER_OPENED"

    work = tmp_path / "ledger.jsonl"
    shutil.copy(ROOT / "platform/evidence/HOLDOUT-CONSUMPTION-LEDGER.jsonl",
                work)
    lines = work.read_text().splitlines()
    reserve = {"entry": len(lines) + 1, "utc": "2026-08-21T18:00:00Z",
               "event": "RESERVED", "holdout": "eval/x/sealed/manifest.jsonl",
               "sha256": "e" * 64,
               "prev_sha256": hashlib.sha256(lines[-1].encode()).hexdigest()}
    with work.open("a") as fh:
        fh.write(json.dumps(reserve, sort_keys=True) + "\n")
    holdout_ledger.record_consumption("eval/x/sealed/manifest.jsonl",
                                       "e" * 64, "gate-run", work)
    with _pytest.raises(holdout_ledger.LedgerRefusal, match="already CONSUMED"):
        holdout_ledger.record_consumption("eval/x/sealed/manifest.jsonl",
                                           "e" * 64, "second", work)
    # history rewriting breaks the chain
    lines = work.read_text().splitlines()
    tampered = json.loads(lines[1])
    tampered["reserved_for"] = "something else entirely"
    lines[1] = json.dumps(tampered, sort_keys=True)
    work.write_text("\n".join(lines) + "\n")
    with _pytest.raises(holdout_ledger.LedgerRefusal, match="chain broken"):
        holdout_ledger.verify_chain(work)


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


def test_ledger_void_requires_no_observation_attestation(tmp_path):
    """Superseded by v3 semantics (Codex review #11): the v2 sealed half is
    QUARANTINED (blocking wins over the old entry-6 void), and loose voids
    never release — covered exhaustively by test_void_adjudication_is_strict."""
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import importlib
    import holdout_ledger
    importlib.reload(holdout_ledger)
    import pytest as _pytest
    with _pytest.raises(holdout_ledger.LedgerRefusal, match="QUARANTINED"):
        holdout_ledger.require_available(
            "eval/kinyarwanda/asr/cv17-test-v1-sealed/manifest.jsonl")


def test_void_adjudication_is_strict(tmp_path):
    """Ledger v4 (Codex review #12): owner approval must be a COMMITTED
    authorization record verified by bytes; reserved-sha matching; strict
    field typing. Adversarial set: forged owner string, empty evidence
    lists, wrong sha, nonexistent target, duplicate void, malformed
    release — all fail; the complete chain releases."""
    import hashlib
    import shutil
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import importlib
    import holdout_ledger
    importlib.reload(holdout_ledger)
    import pytest as _pytest

    import subprocess as _sp
    repo = tmp_path / "repo"
    (repo / "platform/decisions").mkdir(parents=True)
    approval_doc = {"record": "AUTH-TEST-001",
                     "authorizes": "CONSUMPTION_VOIDED",
                     "holdout": "eval/Z/sealed/manifest.jsonl",
                     "owner_verbatim": "approved in chat on <date>"}
    approval_path = repo / "platform/decisions/AUTH-TEST-001.json"
    approval_path.write_text(json.dumps(approval_doc, sort_keys=True))
    # v5 reads approval bytes from GIT HEAD — commit it for real
    for cmd in (["git", "init", "-q"], ["git", "add", "-A"],
                ["git", "-c", "user.email=t@t", "-c", "user.name=t",
                 "commit", "-q", "-m", "auth"]):
        _sp.run(cmd, cwd=repo, check=True, capture_output=True)
    approval_ref = {"path": "platform/decisions/AUTH-TEST-001.json",
                     "record_id": "AUTH-TEST-001",
                     "sha256": hashlib.sha256(
                         approval_path.read_bytes()).hexdigest()}

    def fresh():
        work = tmp_path / f"l{fresh.n}.jsonl"
        fresh.n += 1
        shutil.copy(ROOT / "platform/evidence/HOLDOUT-CONSUMPTION-LEDGER.jsonl",
                    work)
        # reserve + consume holdout Z
        lines = work.read_text().splitlines()
        for doc in (
            {"event": "RESERVED", "holdout": "eval/Z/sealed/manifest.jsonl",
             "sha256": "9" * 64, "utc": "2026-08-21T17:00:00Z"},):
            doc = dict(doc, entry=len(lines) + 1,
                       prev_sha256=hashlib.sha256(lines[-1].encode()).hexdigest())
            lines.append(json.dumps(doc, sort_keys=True))
        work.write_text("\n".join(lines) + "\n")
        holdout_ledger.record_consumption(
            "eval/Z/sealed/manifest.jsonl", "9" * 64, "run", work,
            repo_root=repo)
        return work, len(work.read_text().splitlines())
    fresh.n = 0

    def append(work, doc):
        lines = work.read_text().splitlines()
        doc = dict(doc, entry=len(lines) + 1,
                   prev_sha256=hashlib.sha256(lines[-1].encode()).hexdigest())
        with work.open("a") as fh:
            fh.write(json.dumps(doc, sort_keys=True) + "\n")

    good = {"utc": "2026-08-21T17:10:00Z", "event": "CONSUMPTION_VOIDED",
            "holdout": "eval/Z/sealed/manifest.jsonl",
            "holdout_sha256": "9" * 64,
            "evaluator_instance_ids": ["i-0abc"],
            "userdata_sha256": "a" * 64,
            "results_prefix_object_count": 0,
            "log_version_ids": ["ver1"],
            "no_inference_attestation": ("no model inference started and no "
                                          "results were produced"),
            "approval_record": approval_ref}

    # the complete owner-record-backed schema releases
    work, n = fresh()
    append(work, dict(good, voids_entry=n))
    holdout_ledger.require_available("eval/Z/sealed/manifest.jsonl", work,
                                      repo_root=repo)

    adversarial = [
        dict(good, approval_record=None),                       # no record
        dict(good, approval_record=dict(approval_ref,
                                         sha256="0" * 64)),     # wrong bytes
        dict(good, evaluator_instance_ids=[]),                  # empty list
        dict(good, evaluator_instance_ids=["not-an-instance"]),
        dict(good, log_version_ids=[]),
        dict(good, userdata_sha256="ZZZ"),                      # invalid hash
        dict(good, holdout_sha256="8" * 64),                    # sha mismatch
        dict(good, results_prefix_object_count=2),
    ]
    for bad in adversarial:
        work, n = fresh()
        append(work, dict(bad, voids_entry=n))
        with _pytest.raises(holdout_ledger.LedgerRefusal):
            holdout_ledger.require_available("eval/Z/sealed/manifest.jsonl",
                                              work, repo_root=repo)

    # forged free-text owner approval (the review #12 reproduction)
    work, n = fresh()
    forged = {k: v for k, v in good.items() if k != "approval_record"}
    forged["approved_by"] = "owner forged by anyone"
    append(work, dict(forged, voids_entry=n))
    with _pytest.raises(holdout_ledger.LedgerRefusal):
        holdout_ledger.require_available("eval/Z/sealed/manifest.jsonl",
                                          work, repo_root=repo)


def test_acquisition_verifies_reserved_sha_and_is_atomic(tmp_path):
    """Codex review #12: an all-zero sha acquired the universal holdout,
    and concurrent acquisitions both succeeded."""
    import hashlib
    import shutil
    import sys
    import threading
    sys.path.insert(0, str(ROOT / "scripts"))
    import importlib
    import holdout_ledger
    importlib.reload(holdout_ledger)
    import pytest as _pytest

    work = tmp_path / "ledger.jsonl"
    shutil.copy(ROOT / "platform/evidence/HOLDOUT-CONSUMPTION-LEDGER.jsonl",
                work)

    # wrong sha vs the RESERVED sha refuses (universal holdout, entry 3)
    with _pytest.raises(holdout_ledger.LedgerRefusal, match="RESERVED sha"):
        holdout_ledger.record_consumption(
            "eval/kinyarwanda/asr/cv17-test-v1-universal-sealed/manifest.jsonl",
            "0" * 64, "attack", work)
    # unreserved holdout refuses
    with _pytest.raises(holdout_ledger.LedgerRefusal, match="no RESERVED"):
        holdout_ledger.record_consumption(
            "eval/never-reserved/manifest.jsonl", "1" * 64, "x", work)

    # concurrency: exactly ONE of N concurrent acquisitions may win
    correct_sha = "5ca3ef62e6f7447c5b8a2479e51b1f245ed792795240ac46022de4d6391805df"
    outcomes = []
    def attempt():
        try:
            holdout_ledger.record_consumption(
                "eval/kinyarwanda/asr/cv17-test-v1-universal-sealed/manifest.jsonl",
                correct_sha, "racer", work)
            outcomes.append("won")
        except holdout_ledger.LedgerRefusal:
            outcomes.append("refused")
    threads = [threading.Thread(target=attempt) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert outcomes.count("won") == 1, outcomes
    holdout_ledger.verify_chain(work)   # chain intact after the race


def test_sealed_launcher_acquires_before_any_aws_call():
    """Superseded by launcher v2 (packet-bound): refusal-means-zero-AWS is
    asserted in test_sealed_launcher_verifies_packet_identity_and_environment."""


def test_untracked_or_escaping_approval_records_are_refused(tmp_path):
    """Codex review #13 reproduction: an untracked working-tree file in a
    non-git directory passed as a 'committed' approval. v5 reads approval
    bytes from GIT HEAD only, path-constrained to platform/decisions/."""
    import hashlib
    import shutil
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import importlib
    import holdout_ledger
    importlib.reload(holdout_ledger)
    import pytest as _pytest

    work = tmp_path / "ledger.jsonl"
    shutil.copy(ROOT / "platform/evidence/HOLDOUT-CONSUMPTION-LEDGER.jsonl",
                work)
    lines = work.read_text().splitlines()
    def append(doc):
        nonlocal lines
        doc = dict(doc, entry=len(lines) + 1,
                   prev_sha256=hashlib.sha256(lines[-1].encode()).hexdigest())
        lines.append(json.dumps(doc, sort_keys=True))
        work.write_text("\n".join(lines) + "\n")
    append({"event": "RESERVED", "holdout": "eval/W/manifest.jsonl",
            "sha256": "7" * 64, "utc": "2026-08-21T19:00:00Z"})
    holdout_ledger.record_consumption("eval/W/manifest.jsonl", "7" * 64,
                                       "run", work)
    lines = work.read_text().splitlines()

    # forge an UNTRACKED approval in the real repo working tree
    forged = ROOT / "platform/decisions/AUTH-FORGED-UNTRACKED.json"
    doc = {"record": "AUTH-FORGED-UNTRACKED",
           "authorizes": "CONSUMPTION_VOIDED",
           "holdout": "eval/W/manifest.jsonl",
           "owner_verbatim": "forged"}
    forged.write_text(json.dumps(doc, sort_keys=True))
    try:
        void = {"event": "CONSUMPTION_VOIDED", "voids_entry": len(lines),
                "holdout": "eval/W/manifest.jsonl", "holdout_sha256": "7" * 64,
                "evaluator_instance_ids": ["i-0abc"],
                "userdata_sha256": "a" * 64,
                "results_prefix_object_count": 0, "log_version_ids": ["v"],
                "no_inference_attestation": ("no model inference started and "
                                              "no results were produced"),
                "utc": "2026-08-21T19:01:00Z",
                "approval_record": {
                    "path": "platform/decisions/AUTH-FORGED-UNTRACKED.json",
                    "record_id": "AUTH-FORGED-UNTRACKED",
                    "sha256": hashlib.sha256(
                        forged.read_bytes()).hexdigest()}}
        append(void)
        with _pytest.raises(holdout_ledger.LedgerRefusal):
            holdout_ledger.require_available("eval/W/manifest.jsonl", work)
    finally:
        forged.unlink()
    # traversal + absolute paths refuse structurally
    for bad_path in ("platform/decisions/../../evil.json",
                      "/tmp/evil.json", "somewhere/else.json"):
        assert holdout_ledger._verify_owner_approval(
            {"event": "CONSUMPTION_VOIDED", "holdout": "x",
             "approval_record": {"path": bad_path, "record_id": "X",
                                  "sha256": "0" * 64}}, ROOT) is False


def test_conflicting_reservations_refuse(tmp_path):
    """Codex review #13: a later conflicting reservation silently overrode
    the original — acquisition under EITHER sha must now refuse."""
    import hashlib
    import shutil
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import importlib
    import holdout_ledger
    importlib.reload(holdout_ledger)
    import pytest as _pytest

    work = tmp_path / "ledger.jsonl"
    shutil.copy(ROOT / "platform/evidence/HOLDOUT-CONSUMPTION-LEDGER.jsonl",
                work)
    lines = work.read_text().splitlines()
    for sha in ("1" * 64, "2" * 64):
        doc = {"event": "RESERVED", "holdout": "eval/C/manifest.jsonl",
               "sha256": sha, "utc": "2026-08-21T19:05:00Z",
               "entry": len(lines) + 1,
               "prev_sha256": hashlib.sha256(lines[-1].encode()).hexdigest()}
        lines.append(json.dumps(doc, sort_keys=True))
    work.write_text("\n".join(lines) + "\n")
    for sha in ("1" * 64, "2" * 64):
        with _pytest.raises(holdout_ledger.LedgerRefusal, match="CONFLICTING"):
            holdout_ledger.record_consumption("eval/C/manifest.jsonl", sha,
                                               "x", work)


def test_sealed_launch_is_not_implemented_and_refuses():
    """Codex review #14 disposition: a generic launcher cannot be secured
    by bolted-on validation. Launch capability is REMOVED until an
    evaluator meets SEALED-EVALUATOR-SPEC-2026-001; the refusal IS the
    hold, in code."""
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import importlib
    import launch_sealed_eval
    importlib.reload(launch_sealed_eval)
    calls = []
    def spy_runner(cmd, **kwargs):
        calls.append(cmd)
        class R: returncode, stdout, stderr = 0, "", ""
        return R()
    rc = launch_sealed_eval.main(["launch"], runner=spy_runner)
    assert rc == 1
    assert calls == [], "launch refusal must make ZERO external calls"
    spec = (ROOT / "platform/decisions/SEALED-EVALUATOR-SPEC-2026-001.md")
    text = spec.read_text()
    for requirement in ("STRUCTURED COMPOSITION", "GIT-BLOB",
                         "OWNER-AUTHORIZED PACKET", "WATCHDOG",
                         "KMS-encrypted", "EXACTLY-ONCE", "REHEARSAL"):
        assert requirement in text


def test_v2_sealed_holdout_is_quarantined_and_unciteable():
    """RESTORED (Codex review #14: dropped in the #13 test rewrite —
    range-replace carelessness). The quarantined v2 sealed half must stay
    blocked in the ledger AND absent from the checker's language map."""
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import importlib
    import holdout_ledger
    importlib.reload(holdout_ledger)
    import pytest as _pytest
    with _pytest.raises(holdout_ledger.LedgerRefusal, match="QUARANTINED"):
        holdout_ledger.require_available(
            "eval/kinyarwanda/asr/cv17-test-v1-sealed/manifest.jsonl")
    import b7_model_promotion_check as checker
    importlib.reload(checker)
    mapping = checker._authoritative_holdouts_by_language()
    v2_sealed = "f6f50bcfc473a12026efefe94b1fbbebcf42e6006623860c18be21e6583e70b9"
    assert all(v2_sealed not in shas for shas in mapping.values())


def test_checker_refuses_cross_language_holdout_substitution_restored():
    """RESTORED (Codex review #14) with real assertions only."""
    import importlib
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import b7_model_promotion_check as checker
    importlib.reload(checker)
    mapping = checker._authoritative_holdouts_by_language()
    swahili_sha = next(iter(mapping["swahili"]))
    assert swahili_sha not in mapping["english"]
    assert swahili_sha not in mapping["french"]
    assert swahili_sha in mapping["swahili"]


def test_no_sealed_command_works_and_no_stray_launch_paths_exist():
    """Codex reviews #15-#16: (a) both sealed commands refuse; (b) the
    launch-path guard scans the ENTIRE tracked repository for BOTH the CLI
    (run-instances) and SDK (run_instances) forms. Every file that may
    contain them is individually allowlisted — adding launch capability
    anywhere else fails the suite, and extending the allowlist is a
    reviewed diff by construction."""
    import subprocess
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import importlib
    import launch_sealed_eval
    importlib.reload(launch_sealed_eval)
    for command in ("acquire", "launch"):
        calls = []
        def spy(cmd, **kwargs):
            calls.append(cmd)
            class R: returncode, stdout, stderr = 0, "", ""
            return R()
        rc = launch_sealed_eval.main([command], runner=spy)
        assert rc == 1 and calls == [], command

    tracked = subprocess.run(
        ["git", "-C", str(ROOT), "grep", "-l", "-e", "run-instances",
         "-e", "run_instances"],
        capture_output=True, text=True).stdout.split()
    allowed = {
        # governed B6a EC2 stage-execution system (its own packet gates)
        "pipeline/budget.py", "pipeline/builder_adapter.py",
        "pipeline/builder_userdata.sh", "pipeline/container_userdata.sh",
        "pipeline/ec2_stage_adapter.py", "pipeline/eval_userdata.sh",
        "pipeline/trainer_userdata.sh",
        # documentation / evidence records (non-executable)
        "platform/decisions/PLAN-2026-001-reproduce-failed-eval.md",
        "platform/evidence/CAMPAIGNRUN-2026-001-failed.json",
        # tests of the above + this guard itself
        "tests/test_arch_2026_001.py", "tests/test_builder_adapter.py",
        "tests/test_stage_execution.py",
    }
    stray = sorted(set(tracked) - allowed)
    assert stray == [], (
        f"EC2 launch capability appeared outside the reviewed allowlist: "
        f"{stray} — sealed/eval launches must go through the spec-built "
        "evaluator (SEALED-EVALUATOR-SPEC-2026-001)")


def test_durable_commit_refuses_a_mismatched_committed_entry(tmp_path):
    """Codex review #16: the full-entry comparison had no direct
    regression. A committed tail differing in ANY field must refuse."""
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import importlib
    import launch_sealed_eval
    importlib.reload(launch_sealed_eval)
    import pytest as _pytest

    entry = {"entry": 99, "event": "CONSUMED", "holdout": "eval/x",
             "sha256": "a" * 64, "consumed_by": "run", "utc": "t",
             "prev_sha256": "b" * 64}
    tampered = dict(entry, consumed_by="someone else")
    def runner(cmd, **kwargs):
        class R: pass
        r = R()
        r.returncode = 0
        r.stderr = ""
        r.stdout = json.dumps(tampered, sort_keys=True) + "\n"
        return r
    with _pytest.raises(launch_sealed_eval.LaunchRefusal, match="FULL"):
        launch_sealed_eval.durable_commit(entry, runner)
