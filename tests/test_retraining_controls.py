"""Behavioural tests for the Option B retraining controls.

Every test calls the thing it describes. The defect that started this
investigation was a guard that was written correctly and never fired, so a
guard that has only been read is not a guard.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import budget, orchestrate, smoke                    # noqa: E402
from pipeline import generation as G                               # noqa: E402

LANGS = orchestrate.VALIDATION_LANGUAGES
POLICY3 = ROOT / "platform/decisions/DQ-2026-003-policy-deferral-corrected.json"


def wers(**over):
    d = {l: 0.90 for l in LANGS}
    d.update(over)
    return d


def perfect(**over):
    d = {l: 1.0 for l in LANGS}
    d.update(over)
    return d


def zeros(**over):
    d = {l: 0.0 for l in LANGS}
    d.update(over)
    return d


# --------------------------------------------------------------------------- #
# policy authorization
# --------------------------------------------------------------------------- #
def test_policy_carries_the_authorising_instruction():
    d = json.loads(POLICY3.read_bytes())
    assert d["authorization_note"], "the instruction must be recorded verbatim"
    assert "Option B" in d["authorization_note"]
    assert "does NOT constitute human listening" in d["authorization_note"]
    assert d["human_review_performed"] is False
    assert d["counts"]["defects"] == 0
    prov = d["authorization_provenance"]
    assert "No authorization metadata from any earlier" in prov
    assert "is retained" in prov


def test_loader_refuses_a_policy_with_no_authorization(tmp_path):
    from pipeline.train_asr import load_exclusions
    d = json.loads(POLICY3.read_bytes())
    d["status"] = "draft"                       # unauthorised
    p = tmp_path / "unauth.json"
    p.write_text(json.dumps(d))
    with pytest.raises(SystemExit, match="not approved"):
        load_exclusions(str(p))


def test_loader_refuses_a_policy_claiming_review_it_did_not_do(tmp_path):
    from pipeline.train_asr import load_exclusions
    d = json.loads(POLICY3.read_bytes())
    d["human_review_performed"] = True          # false claim
    p = tmp_path / "false.json"
    p.write_text(json.dumps(d))
    with pytest.raises(SystemExit, match="human_review_performed=false"):
        load_exclusions(str(p))


def test_scope_decision_does_not_classify_anything():
    d = json.loads((ROOT / "platform/decisions/"
                    "DQ-2026-001-scope-decision-2026-07-31.json").read_bytes())
    assert d["in_scope"]["count"] == 19
    assert d["out_of_scope"]["audio_checksum_sha256_prefix"] == "d0ffd52881d0b074"
    assert "NOT a quality endorsement" in d["statement"]
    assert d["draft_state"]["classified"] == 0
    draft = json.loads((ROOT / "platform/decisions/"
                        "DQ-2026-001-label-review.json").read_bytes())
    assert draft["status"] == "draft"
    assert all(e["classification"] is None for e in draft["entries"])


# --------------------------------------------------------------------------- #
# old adoption / fingerprint rejection
# --------------------------------------------------------------------------- #
OLD_FP = "77c7ce61edba96c806fa22a0d50792fc4976a3cebe01a3322493583d94cb9b7c"
NEW_FP = "ad8c63d157419cbdbadc1d6a2cf8790c0766d76b848152dbd1be4a1373288275"


def test_new_fingerprint_differs_from_the_failed_run():
    assert OLD_FP != NEW_FP


def test_adoption_bound_to_the_old_policy_refuses_the_new_one():
    """The published adoption binds the 20-row policy by sha256."""
    src = (ROOT / "pipeline/train_asr.py").read_text()
    assert "does not transfer" in src
    assert 'want_policy != exclusions_sha256' in src


def test_old_twenty_row_policy_is_the_wrong_count_now(tmp_path):
    from pipeline.train_asr import load_exclusions
    old = ROOT / "platform/decisions/DQ-2026-002-policy-deferral.json"
    with pytest.raises(SystemExit, match="expected exactly 19"):
        load_exclusions(str(old), expect=19)
    out, _, _ = load_exclusions(str(POLICY3), expect=19)
    assert len(out) == 19


# --------------------------------------------------------------------------- #
# all FOUR validation gates
# --------------------------------------------------------------------------- #
def test_all_four_gates_pass_on_a_clean_result():
    g = orchestrate.evaluate_gates(wers(), wers(**{l: 0.95 for l in LANGS}),
                                   perfect(), zeros())
    assert g["passed"] and all(g["gates"].values())


def test_gate_eos_rate():
    g = orchestrate.evaluate_gates(wers(), wers(), perfect(shona=0.99), zeros())
    assert not g["passed"] and g["gates"]["eos_rate"] is False
    assert "EOS rate below" in " ".join(g["failures"])


def test_gate_cap_hit_rate():
    g = orchestrate.evaluate_gates(wers(), wers(), perfect(), zeros(oromo=0.01))
    assert not g["passed"] and g["gates"]["cap_hit_rate"] is False


def test_gate_per_language_regression_catches_a_hidden_collapse():
    """Eight languages improve a lot, one collapses. The macro average looks
    fine; the run must still fail."""
    base = wers(**{l: 0.90 for l in LANGS})
    cand = wers(**{l: 0.50 for l in LANGS})
    cand["shona"] = 0.99                       # +0.09 regression
    g = orchestrate.evaluate_gates(cand, base, perfect(), zeros())
    assert g["macro_wer"] < g["base_macro_wer"], "aggregate looks like an improvement"
    assert not g["passed"]
    assert g["gates"]["per_language_regression"] is False
    assert g["worst_language"] == "shona"
    assert "hide one language collapsing" in " ".join(g["failures"])


def test_regression_exactly_at_the_cap_passes():
    base = wers()
    cand = wers(shona=0.95)                     # exactly +0.05
    g = orchestrate.evaluate_gates(cand, base, perfect(), zeros())
    assert g["gates"]["per_language_regression"] is True


def test_gate_macro_not_worse():
    g = orchestrate.evaluate_gates(wers(**{l: 0.93 for l in LANGS}), wers(),
                                   perfect(), zeros())
    assert not g["passed"] and g["gates"]["macro_not_worse"] is False


def test_macro_average_is_unweighted_and_requires_every_language():
    part = {l: 0.5 for l in LANGS[:5]}
    with pytest.raises(SystemExit, match="no result for"):
        orchestrate.macro_wer(part)
    d = {l: 0.0 for l in LANGS}
    d["shona"] = 0.9
    assert orchestrate.macro_wer(d) == pytest.approx(0.9 / 9)


# --------------------------------------------------------------------------- #
# LR selection and tie-breaking
# --------------------------------------------------------------------------- #
def _gate(macro, worst=0.0, passed=True):
    return {"macro_wer": macro, "worst_language_regression": worst,
            "passed": passed, "failures": [] if passed else ["x"]}


def test_lr_selection_picks_the_lowest_macro():
    sel = orchestrate.select_lr([
        {"lr": 1e-4, "gate": _gate(0.80)},
        {"lr": 3e-4, "gate": _gate(0.70)},
        {"lr": 5e-4, "gate": _gate(0.75)}])
    assert sel["selected_lr"] == 3e-4 and sel["tie_broken"] is False


def test_tie_on_macro_breaks_on_worst_language_regression():
    sel = orchestrate.select_lr([
        {"lr": 1e-4, "gate": _gate(0.70, worst=0.04)},
        {"lr": 5e-4, "gate": _gate(0.70, worst=0.01)}])
    assert sel["selected_lr"] == 5e-4 and sel["tie_broken"] is True


def test_full_tie_breaks_on_the_lower_learning_rate():
    sel = orchestrate.select_lr([
        {"lr": 5e-4, "gate": _gate(0.70, worst=0.02)},
        {"lr": 1e-4, "gate": _gate(0.70, worst=0.02)},
        {"lr": 3e-4, "gate": _gate(0.70, worst=0.02)}])
    assert sel["selected_lr"] == 1e-4 and sel["tie_broken"] is True


def test_a_failing_candidate_is_never_selected():
    sel = orchestrate.select_lr([
        {"lr": 1e-4, "gate": _gate(0.10, passed=False)},
        {"lr": 3e-4, "gate": _gate(0.70)}])
    assert sel["selected_lr"] == 3e-4
    assert sel["candidates_passing_all_four_gates"] == 1


def test_no_candidate_passing_refuses_rather_than_picking_least_bad():
    with pytest.raises(SystemExit, match="no learning rate passed all four"):
        orchestrate.select_lr([{"lr": 1e-4, "gate": _gate(0.1, passed=False)},
                               {"lr": 3e-4, "gate": _gate(0.2, passed=False)}])


def test_final_run_does_not_resume_the_sweep_checkpoint():
    assert orchestrate.FINAL_RUN_RESUMES_SWEEP is False
    sel = orchestrate.select_lr([{"lr": 1e-4, "gate": _gate(0.7)}])
    assert sel["final_run_resumes_this_checkpoint"] is False
    assert "head start" in sel["final_run_note"]


def test_one_e_minus_three_is_not_a_candidate():
    assert 1e-3 not in orchestrate.LR_CANDIDATES


# --------------------------------------------------------------------------- #
# base arm evaluated first, reused only on an exact match
# --------------------------------------------------------------------------- #
def _key(**over):
    kw = dict(image_digest="sha256:aa", gen_fingerprint="gf",
              evaluator_sha="ev", manifest_hashes={"shona": "h"},
              normalization={"shona": "v1"})
    kw.update(over)
    return orchestrate.base_arm_key(**kw)


def test_identical_configuration_reuses_the_base_arm():
    k = _key()
    assert orchestrate.may_reuse_base({"base_arm_key": k}, k) is True


@pytest.mark.parametrize("field,val", [
    ("image_digest", "sha256:bb"), ("gen_fingerprint", "other"),
    ("evaluator_sha", "other"), ("manifest_hashes", {"shona": "different"}),
    ("normalization", {"shona": "v2"}),
])
def test_any_change_forbids_reusing_the_base_arm(field, val):
    with pytest.raises(SystemExit, match="does not match"):
        orchestrate.may_reuse_base({"base_arm_key": _key()}, _key(**{field: val}))


# --------------------------------------------------------------------------- #
# adapter must have an inference effect
# --------------------------------------------------------------------------- #
def test_adapter_with_a_real_effect_passes():
    torch = pytest.importorskip("torch")
    on = torch.tensor([[1.0, 2.0, 3.0]])
    off = torch.tensor([[1.0, 2.0, 2.5]])
    v = smoke.adapter_effect_verdict(on, off, {"lora_B": 0.3},
                                     checkpoint_sha256="d" * 64,
                                     tested_artifact_sha256="d" * 64)
    assert v["passed"] and v["max_abs_logit_delta"] == pytest.approx(0.5)


def test_inert_adapter_is_refused_even_though_it_is_present():
    """A LoRA with a zero B matrix is wrapped, targeted and trainable -- and
    changes nothing. It would produce base numbers that look like a fix."""
    torch = pytest.importorskip("torch")
    x = torch.tensor([[1.0, 2.0, 3.0]])
    v = smoke.adapter_effect_verdict(x, x.clone(), {"lora_B": 0.0},
                                     checkpoint_sha256="d" * 64,
                                     tested_artifact_sha256="d" * 64)
    assert not v["passed"]
    joined = " ".join(v["reasons"])
    assert "inert" in joined and "zero B matrix" in joined


def test_bit_identical_logits_are_refused():
    torch = pytest.importorskip("torch")
    x = torch.tensor([[1.0, 2.0]])
    v = smoke.adapter_effect_verdict(x, x, {"lora_B": 5.0},
                                     checkpoint_sha256="d" * 64,
                                     tested_artifact_sha256="d" * 64)
    assert not v["passed"]
    assert any("bit-identical" in r for r in v["reasons"])


def test_effect_below_threshold_is_refused():
    torch = pytest.importorskip("torch")
    on = torch.tensor([[1.0]])
    off = torch.tensor([[1.0 + 1e-6]])
    v = smoke.adapter_effect_verdict(on, off, {"lora_B": 1.0},
                                     checkpoint_sha256="d" * 64,
                                     tested_artifact_sha256="d" * 64)
    assert not v["passed"]


# --------------------------------------------------------------------------- #
# overfit criterion
# --------------------------------------------------------------------------- #
def test_overfit_passes_on_a_genuinely_learned_batch():
    v = smoke.overfit_verdict(l0=2.0, l_final=0.05, steps=120,
                              all_grads_finite=True)
    assert v["passed"] and v["ratio"] == pytest.approx(0.025)


def test_the_failed_runs_loss_curve_would_not_pass():
    """22.53 -> 4.00 is an 82% decrease and must still fail: the ratio is far
    above 5% and the absolute is far above 0.5."""
    v = smoke.overfit_verdict(l0=22.53, l_final=4.00, steps=200,
                              all_grads_finite=True)
    assert not v["passed"]
    assert any("L_final/L0" in r for r in v["reasons"])
    assert any("0.5" in r for r in v["reasons"])


def test_big_ratio_but_high_absolute_fails():
    v = smoke.overfit_verdict(l0=100.0, l_final=2.0, steps=50,
                              all_grads_finite=True)
    assert not v["passed"]                      # ratio 0.02 passes, absolute does not


def test_step_budget_and_finite_gradients_are_enforced():
    assert not smoke.overfit_verdict(2.0, 0.01, 201, True)["passed"]
    assert not smoke.overfit_verdict(2.0, 0.01, 10, False)["passed"]
    assert not smoke.overfit_verdict(float("nan"), 0.01, 10, True)["passed"]


# --------------------------------------------------------------------------- #
# generation smoke
# --------------------------------------------------------------------------- #
PROMPT = [50258, 50259, 50360, 50364]
EOT = 50257


def test_generation_smoke_passes_with_eos_and_no_cap():
    acct = G.account(PROMPT + [100, 101, EOT], PROMPT, EOT)
    v = smoke.generation_smoke_verdict(acct, True, True)
    assert v["passed"] and v["eos_emitted"] and not v["hit_length_cap"]


def test_generation_smoke_fails_without_eos():
    acct = G.account(PROMPT + [100] * G.MAX_NEW_TOKENS, PROMPT, EOT)
    v = smoke.generation_smoke_verdict(acct, True, True)
    assert not v["passed"] and v["stop_reason"] == "max_new_tokens"


def test_generation_smoke_fails_on_non_finite():
    acct = G.account(PROMPT + [100, EOT], PROMPT, EOT)
    assert not smoke.generation_smoke_verdict(acct, False, True)["passed"]
    assert not smoke.generation_smoke_verdict(acct, True, False)["passed"]


def test_require_raises_with_the_numbers_attached():
    with pytest.raises(SystemExit) as e:
        smoke.require({"passed": False, "reasons": ["because"]}, "the check")
    assert "the check failed" in str(e.value) and "because" in str(e.value)


# --------------------------------------------------------------------------- #
# budget enforcement
# --------------------------------------------------------------------------- #
def test_worst_case_includes_watchdog_and_ec2_lifecycle():
    expected = round(
        budget.RATES["g6.xlarge"]
        * (budget.WATCHDOG_S["final_run"]
           + budget.EC2_LIFECYCLE_OVERHEAD_S)
        / 3600,
        4,
    )
    assert budget.worst_case_usd("final_run") == expected


class FakeS3:
    def __init__(self):
        self.objects = {}

    def _etag(self, k):
        import hashlib
        return '"' + hashlib.md5(self.objects[k]).hexdigest() + '"'

    def get_object(self, Bucket, Key):
        from botocore.exceptions import ClientError
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        body = self.objects[Key]

        class B:
            def read(self_inner):
                return body
        return {"Body": B(), "ETag": self._etag(Key)}

    def put_object(self, Bucket, Key, Body, ContentType=None,
                   IfNoneMatch=None, IfMatch=None):
        from botocore.exceptions import ClientError
        if IfNoneMatch == "*" and Key in self.objects:
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        if IfMatch is not None and Key in self.objects and self._etag(Key) != IfMatch:
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        self.objects[Key] = Body
        return {"ETag": self._etag(Key)}


def test_reservation_is_durable_before_launch():
    """The worst case is committed BEFORE the instance exists."""
    s3 = FakeS3()
    r = budget.reserve(s3, "final_run", "1")
    assert r["state"] == "reserved"
    assert r["worst_case_usd"] == budget.worst_case_usd("final_run")
    assert r["watchdog_s"] == budget.WATCHDOG_S["final_run"]
    assert r["ec2_lifecycle_overhead_s"] == 600
    assert r["reserved_seconds"] == (
        r["watchdog_s"] + r["ec2_lifecycle_overhead_s"])
    ledger, _ = budget.load(s3)
    assert budget.committed_usd(ledger) == r["worst_case_usd"]


def test_reservation_ids_are_idempotent():
    """A retry after an ambiguous failure re-reserves the same slot."""
    s3 = FakeS3()
    a = budget.reserve(s3, "sweep_run", "attempt-1")
    b = budget.reserve(s3, "sweep_run", "attempt-1")
    assert b["already_held"] is True
    assert a["reservation_id"] == b["reservation_id"]
    ledger, _ = budget.load(s3)
    assert len(ledger["reservations"]) == 1


def test_crash_after_launch_leaves_the_worst_case_counted():
    s3 = FakeS3()
    budget.reserve(s3, "final_run", "crashed")
    ledger, _ = budget.load(s3)
    assert budget.committed_usd(ledger) == budget.worst_case_usd("final_run")
    assert budget.unresolved(ledger)


def test_reconciliation_replaces_worst_case_with_actual():
    s3 = FakeS3()
    budget.reserve(s3, "final_run", "1")
    before = budget.committed_usd(budget.load(s3)[0])
    budget.reconcile(s3, "final_run", "1", actual_seconds=600)
    ledger, _ = budget.load(s3)
    after = budget.committed_usd(ledger)
    assert after < before
    assert budget.unresolved(ledger) == []


def test_an_unresolved_reservation_blocks_the_next_reservation():
    s3 = FakeS3()
    budget.reserve(s3, "sweep_run", "orphan")
    with pytest.raises(SystemExit, match="still unresolved"):
        budget.reserve(s3, "final_run", "next")


def test_ceiling_refuses_the_reservation_that_would_breach_it():
    s3 = FakeS3()
    completed = [
        "final_run",
        "sweep_run",
        "sweep_run",
        "sweep_run",
        "base_and_preflight",
    ]
    for i, stage in enumerate(completed):
        budget.reserve(s3, stage, f"a{i}")
        budget.reconcile(s3, stage, f"a{i}",
                         actual_seconds=budget.WATCHDOG_S[stage])
    with pytest.raises(SystemExit) as e:
        budget.reserve(s3, "final_run", "one-too-many")
    assert "over the $6.00 ceiling" in str(e.value)
    assert "cannot afford to fail" in str(e.value)


def test_four_sequential_full_length_final_runs_are_refused():
    """The exact case a per-instance watchdog cannot catch."""
    s3 = FakeS3()
    launched = 0
    for i in range(4):
        try:
            budget.reserve(s3, "final_run", f"r{i}")
        except SystemExit:
            break
        budget.reconcile(s3, "final_run", f"r{i}",
                         actual_seconds=budget.WATCHDOG_S["final_run"])
        launched += 1
    assert launched < 4, "a $6 ceiling must not permit 4 x $3 runs"


def test_reconciling_without_a_reservation_refuses():
    with pytest.raises(SystemExit, match="an instance ran without one"):
        budget.reconcile(FakeS3(), "final_run", "never-reserved", 100)


def test_concurrent_writer_is_refused_by_the_conditional_write():
    s3 = FakeS3()
    budget.reserve(s3, "sweep_run", "a")
    budget.reconcile(s3, "sweep_run", "a", 100)
    ledger, stale_etag = budget.load(s3)
    budget.reserve(s3, "sweep_run", "b")            # someone else moves first
    with pytest.raises(SystemExit, match="ledger changed under this operation"):
        budget._put(s3, ledger, stale_etag)


def test_unreadable_ledger_is_not_treated_as_empty():
    from botocore.exceptions import ClientError

    class DeniedS3:
        def get_object(self, Bucket, Key):
            raise ClientError({"Error": {"Code": "AccessDenied"}}, "GetObject")

    with pytest.raises(SystemExit) as e:
        budget.load(DeniedS3())
    assert "restart the budget at zero" in str(e.value)


def test_missing_ledger_is_an_empty_one():
    s3 = FakeS3()
    ledger, etag = budget.load(s3)
    assert ledger["reservations"] == {} and etag is None
    assert budget.remaining_usd(ledger) == budget.CEILING_USD


def test_reservation_is_verified_by_readback():
    src = (ROOT / "pipeline/budget.py").read_text()
    assert "read-back differs from what was" in src
    assert "back, _ = load(cli)" in src


# --------------------------------------------------------------------------- #
# unique, fail-closed artifact prefixes
# --------------------------------------------------------------------------- #
def test_evaluation_prefix_is_unique_per_run_and_step():
    a = orchestrate.evaluation_prefix("run-a", 100)
    b = orchestrate.evaluation_prefix("run-a", 200)
    c = orchestrate.evaluation_prefix("run-b", 100)
    assert len({a, b, c}) == 3
    assert a == "candidates/evaluations/run-a/checkpoint-100/"


def test_prefix_rejects_a_path_injecting_run_id():
    with pytest.raises(ValueError):
        orchestrate.evaluation_prefix("run/../other", 100)


def test_occupied_prefix_refuses():
    class FakeS3:
        def list_objects_v2(self, **kw):
            return {"KeyCount": 1}
    with pytest.raises(SystemExit, match="already contains objects"):
        orchestrate.require_absent(FakeS3(), "b", "candidates/evaluations/x/")


def test_empty_prefix_is_accepted():
    class FakeS3:
        def list_objects_v2(self, **kw):
            return {"KeyCount": 0}
    orchestrate.require_absent(FakeS3(), "b", "candidates/evaluations/x/")


def test_listing_error_is_not_read_as_absence():
    from botocore.exceptions import ClientError

    class FakeS3:
        def list_objects_v2(self, **kw):
            raise ClientError({"Error": {"Code": "AccessDenied"}}, "ListObjectsV2")
    with pytest.raises(SystemExit, match="An error is not an"):
        orchestrate.require_absent(FakeS3(), "b", "candidates/evaluations/x/")


# --------------------------------------------------------------------------- #
# one generation configuration everywhere
# --------------------------------------------------------------------------- #
def test_generation_config_is_frozen():
    with pytest.raises(TypeError):
        G.GENERATION["max_new_tokens"] = 1


def test_generation_fingerprint_is_stable_and_changes_with_content():
    assert G.config_fingerprint() == G.config_fingerprint()
    assert len(G.config_fingerprint()) == 64


def test_every_consumer_uses_the_shared_config():
    train = (ROOT / "pipeline/train_asr.py").read_text()
    assert "from pipeline.generation import config_fingerprint" in train
    assert "generation_config_fingerprint" in train


def test_structured_flags_are_present_in_the_shared_config():
    kw = G.generation_kwargs("en")
    assert kw["return_dict_in_generate"] is True
    assert kw["force_unique_generate_call"] is True
    assert kw["task"] == "transcribe" and kw["do_sample"] is False


def test_lora_wrapper_preserves_whisper_input_features():
    src = (ROOT / "pipeline/train_asr.py").read_text()
    assert "task_type=None" in src
    assert "PeftModelForSeq2SeqLM.forward()" in src
    assert "input_features" in src
    assert "lora_task_type" in src


# --------------------------------------------------------------------------- #
# CVE gate reporting and dependency provenance
# --------------------------------------------------------------------------- #
BUILD = ROOT / "pipeline/build_image.sh"


def test_raw_counts_are_never_zeroed_by_waivers():
    """A waived CVE is still in the image. A record reporting zero because
    everything has a waiver hides an exception the day it expires."""
    s = BUILD.read_text()
    assert '"raw_severity_counts": raw' in s
    assert '"unwaived_severity_counts": unwaived_counts' in s
    assert "These are NEVER" in s and "reduced to zero because a finding has a waiver" in s
    assert '"gate_requires": "zero unwaived findings at any gated severity"' in s


def test_waivers_match_on_cve_package_and_version():
    s = BUILD.read_text()
    assert 'wset = {(w["cve"], w.get("package"), w.get("package_version"))' in s
    assert '(f["cve"], f["package"], f["package_version"]) not in wset' in s


def test_expired_or_mismatched_exceptions_hard_fail():
    s = BUILD.read_text()
    assert "review_by" in s and "has passed; re-review required" in s
    assert "severity changed: waiver says" in s
    assert "stale allowlist entr" in s


def test_gate_emits_the_waived_set_for_the_record():
    s = BUILD.read_text()
    assert 'open("/tmp/waived.json", "w")' in s
    i_emit = s.index('open("/tmp/waived.json", "w")')
    i_read = s.index('open("/tmp/waived.json")')
    assert i_emit < i_read, "the gate must write it before the record reads it"


def test_dependency_provenance_is_recorded():
    s = BUILD.read_text()
    for field in ("requirements_sha256", "base_image", "runtime_versions",
                  "installed_packages", "installed_package_count"):
        assert field in s, field
    assert "pip freeze inside the built image" in s
    assert '"dependencies": deps' in s


def test_allowlist_review_by_is_still_in_the_future():
    """An exception set that has expired fails the gate; this catches it before
    a builder is launched rather than after."""
    import datetime
    allow = json.loads((ROOT / "platform/cve_allowlist.json").read_bytes())
    review_by = datetime.date.fromisoformat(allow["review_by"])
    assert review_by > datetime.date(2026, 7, 31), \
        f"allowlist review_by {review_by} must be renewed before building"


# --------------------------------------------------------------------------- #
# validation-input hardening  (item 4)
# --------------------------------------------------------------------------- #
def test_nan_wer_cannot_pass_a_gate():
    """NaN comparisons are always False, so a NaN would slip past `> cap` and
    be reported as no regression at all."""
    bad = wers(shona=float("nan"))
    with pytest.raises(SystemExit, match="shona is NaN"):
        orchestrate.evaluate_gates(bad, wers(), perfect(), zeros())


def test_infinite_metric_is_refused():
    with pytest.raises(SystemExit, match="is infinite"):
        orchestrate.evaluate_gates(wers(oromo=float("inf")), wers(),
                                   perfect(), zeros())


def test_missing_language_cannot_pass():
    partial = {l: 0.9 for l in LANGS if l != "shona"}
    with pytest.raises(SystemExit, match="missing \\['shona'\\]"):
        orchestrate.evaluate_gates(partial, wers(), perfect(), zeros())


def test_extra_language_is_refused():
    extra = dict(wers(), klingon=0.5)
    with pytest.raises(SystemExit, match="unexpected \\['klingon'\\]"):
        orchestrate.evaluate_gates(extra, wers(), perfect(), zeros())


def test_non_numeric_metric_is_refused():
    with pytest.raises(SystemExit, match="not numeric"):
        orchestrate.evaluate_gates(wers(fula="0.9"), wers(), perfect(), zeros())


def test_boolean_is_not_accepted_as_numeric():
    with pytest.raises(SystemExit, match="not numeric"):
        orchestrate.evaluate_gates(wers(ewe=True), wers(), perfect(), zeros())


def test_negative_wer_is_refused():
    with pytest.raises(SystemExit, match="below 0.0"):
        orchestrate.evaluate_gates(wers(akan=-0.1), wers(), perfect(), zeros())


@pytest.mark.parametrize("bad", [1.5, -0.01])
def test_rates_outside_zero_one_are_refused(bad):
    with pytest.raises(SystemExit):
        orchestrate.evaluate_gates(wers(), wers(), perfect(shona=bad), zeros())
    with pytest.raises(SystemExit):
        orchestrate.evaluate_gates(wers(), wers(), perfect(), zeros(shona=bad))


def test_a_non_mapping_is_refused():
    with pytest.raises(SystemExit, match="not a mapping"):
        orchestrate.evaluate_gates([0.9] * 9, wers(), perfect(), zeros())


# --------------------------------------------------------------------------- #
# saved-artifact verification is MANDATORY  (item 6)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("saved,reloaded", [
    (None, None), ("d" * 64, None), (None, "d" * 64),
])
def test_absent_artifact_hashes_are_refused(saved, reloaded):
    """'we did not check' must not pass like 'we checked and it matched'."""
    torch = pytest.importorskip("torch")
    on, off = torch.tensor([[1.0]]), torch.tensor([[2.0]])
    v = smoke.adapter_effect_verdict(on, off, {"lora_B": 1.0},
                                     checkpoint_sha256=saved,
                                     tested_artifact_sha256=reloaded)
    assert not v["passed"]
    assert "both REQUIRED" in " ".join(v["reasons"])


def test_mismatched_artifact_hashes_are_refused():
    torch = pytest.importorskip("torch")
    on, off = torch.tensor([[1.0]]), torch.tensor([[2.0]])
    v = smoke.adapter_effect_verdict(on, off, {"lora_B": 1.0},
                                     checkpoint_sha256="a" * 64,
                                     tested_artifact_sha256="b" * 64)
    assert not v["passed"]
    assert "is not the saved checkpoint" in " ".join(v["reasons"])


def test_matching_hashes_record_that_the_saved_artifact_was_tested():
    torch = pytest.importorskip("torch")
    on, off = torch.tensor([[1.0]]), torch.tensor([[2.0]])
    v = smoke.adapter_effect_verdict(on, off, {"lora_B": 1.0},
                                     checkpoint_sha256="d" * 64,
                                     tested_artifact_sha256="d" * 64)
    assert v["passed"] and v["tested_the_saved_checkpoint"] is True


# --------------------------------------------------------------------------- #
# stage topology  (item 2)
# --------------------------------------------------------------------------- #
def test_worst_case_sequence_fits_under_the_ceiling():
    """The complete topology, including EC2 lifecycle overhead, fits."""
    total = (budget.worst_case_usd("builder")
             + budget.worst_case_usd("base_and_preflight")
             + 3 * budget.worst_case_usd("sweep_run")
             + budget.worst_case_usd("final_run"))
    assert total <= budget.CEILING_USD, f"worst case ${total} > ceiling"
    assert total == pytest.approx(5.5943, abs=0.0001)


def test_declared_instance_count_matches_the_topology():
    gpu = ["base_and_preflight", "sweep_run", "sweep_run", "sweep_run",
           "final_run"]
    assert len(gpu) == budget.MAX_GPU_INSTANCES == 5
    assert budget.MAX_INSTANCES == budget.MAX_GPU_INSTANCES + 1
    assert all(budget.STAGE_INSTANCE[s] == "g6.xlarge" for s in set(gpu))
    assert budget.STAGE_INSTANCE["builder"] == "c6i.2xlarge"


def test_base_and_preflight_share_one_reservation():
    assert "base_eval" not in budget.WATCHDOG_S
    assert "preflight" not in budget.WATCHDOG_S
    assert "base_and_preflight" in budget.WATCHDOG_S
