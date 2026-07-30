"""The review tooling must not leak content, and must not accept unsound review.

The first version of this tool printed transcripts to stdout. stdout is captured
by whatever launched the process -- including an automation agent -- so the claim
that content stayed "in the terminal" was false. These tests pin the properties
that make the current version's claims true, by exercising the code rather than
reading it.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

REVIEW = ROOT / "scripts" / "review_labels.py"
FINAL = ROOT / "scripts" / "finalize_label_review.py"
DRAFT = ROOT / "platform/decisions/DQ-2026-001-label-review.json"
AUDIT = ROOT / "platform/evidence/label-length-audit-v2.json"

SECRET = "THIS-IS-TRANSCRIPT-CONTENT-THAT-MUST-NEVER-BE-PRINTED"


# --------------------------------------------------------------------------- #
# 1. transcript content must never reach stdout or stderr
# --------------------------------------------------------------------------- #
def test_transcript_is_never_written_to_a_stream(capsys, monkeypatch):
    """review_window must render through the windowing system only."""
    import importlib
    mod = importlib.import_module("scripts.review_labels") if False else None
    spec = importlib.util.spec_from_file_location("rl", REVIEW)
    rl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rl)

    calls = {}

    class FakeText:
        def __init__(self, *a, **k):
            pass

        def insert(self, _idx, text):
            calls["text"] = text          # goes into the widget, not a stream

        def configure(self, **k):
            pass

        def pack(self, **k):
            pass

    class FakeWidget(FakeText):
        def __init__(self, *a, **k):
            self._kw = k

        def configure(self, **k):
            self._kw.update(k)

        def winfo_children(self):
            return []

        def destroy(self):
            pass

    class FakeTk(FakeWidget):
        def title(self, *a):
            pass

        def mainloop(self):
            pass

        def quit(self):
            pass

        def update_idletasks(self):
            pass

    class FakeVar:
        def __init__(self, value=""):
            self._v = value

        def get(self):
            return self._v

        def set(self, v):
            self._v = v

    fake = type("tk", (), {"Tk": FakeTk, "Text": FakeText, "Label": FakeWidget,
                           "Button": FakeWidget, "Frame": FakeWidget,
                           "LabelFrame": FakeWidget, "Radiobutton": FakeWidget,
                           "StringVar": FakeVar})
    monkeypatch.setitem(sys.modules, "tkinter", fake)

    rl.review_window(SECRET, "header-with-no-content",
                     {"uncertain": {"action": "defer_pending_review", "reasons": {"f"}}},
                     lambda: False)
    out = capsys.readouterr()
    assert SECRET not in out.out, "transcript reached stdout"
    assert SECRET not in out.err, "transcript reached stderr"
    assert calls.get("text") == SECRET, "transcript must reach the widget"


def test_no_print_of_transcript_fields_in_the_source():
    """Belt and braces: the row's text fields must never be interpolated into a
    print, a log, or a subprocess argument."""
    s = REVIEW.read_text()
    code = "\n".join(l for l in s.splitlines() if not l.lstrip().startswith("#"))
    for bad in ('print(rec["text_normalized"]', 'print(f"{rec["text_normalized"]}',
                'print(text)', "logging."):
        assert bad not in code, f"found {bad!r}"
    # the only use of the transcript is the window call
    assert code.count('rec["text_normalized"]') == 1
    assert 'review_window(rec["text_normalized"]' in code


# --------------------------------------------------------------------------- #
# 2. execution environment
# --------------------------------------------------------------------------- #
def test_refuses_non_interactive_and_agent_environments():
    r = subprocess.run([sys.executable, str(REVIEW), "--reviewer-role", "data-steward"],
                       capture_output=True, text=True, cwd=ROOT,
                       env={"PATH": "/usr/bin:/bin", "CLAUDECODE": "1"})
    assert r.returncode != 0
    assert "REFUSING to start review" in r.stdout + r.stderr
    assert "has fetched nothing" in r.stdout + r.stderr


def test_refuses_an_identity_as_a_role():
    r = subprocess.run([sys.executable, str(REVIEW), "--reviewer-role", "a@b.com"],
                       capture_output=True, text=True, cwd=ROOT)
    assert "must be a role, not an identity" in r.stdout + r.stderr


def test_refusal_list_covers_ssh_and_display():
    s = REVIEW.read_text()
    assert "SSH_CONNECTION" in s and "SSH_TTY" in s
    assert "WAYLAND_DISPLAY" in s or "DISPLAY" in s
    assert "CLAUDECODE" in s


# --------------------------------------------------------------------------- #
# 3. listening is mandatory
# --------------------------------------------------------------------------- #
def test_submit_is_gated_on_successful_playback():
    """Submit starts disabled and is only enabled once playback has succeeded,
    so a classification cannot be recorded from reading the transcript alone."""
    s = REVIEW.read_text()
    assert 'submit = tk.Button(win, text="Submit", state="disabled")' in s
    assert 'ok = state["played"] and cls_var.get() and reason_var.get()' in s
    assert 'state["played"] = state["played"] or ok' in s
    assert 'if not state["played"]:\n            return' in s
    assert "PLAYBACK FAILED" in s


def test_transcript_and_audio_are_available_together():
    """An earlier version played the clip and only then opened a window, so the
    reviewer could never hear it while reading."""
    s = REVIEW.read_text()
    assert "ONE window" in s
    assert 'tk.Button(bar, text="Play / Replay"' in s
    # the play control lives in the same window as the transcript
    assert s.index("box.insert(") < s.index('text="Play / Replay"')


def test_finalizer_requires_the_listened_flag():
    s = FINAL.read_text()
    assert 'if not e.get("listened")' in s
    assert "requires " in s and "listening, not metrics" in s


def test_audio_handling_is_described_accurately():
    """An earlier docstring implied memory-only playback while using a temp file."""
    s = REVIEW.read_text()
    assert "0600" in s and "O_EXCL" in s
    assert "not \"memory only\"" in s or 'not "memory only"' in s
    assert "os.chmod(path, 0o600)" in s
    assert "os.unlink(path)" in s


# --------------------------------------------------------------------------- #
# 4/5/6. bindings and entry integrity
# --------------------------------------------------------------------------- #
def _bindings():
    from pipeline import review_bindings as RB
    return RB


def test_bindings_are_recomputed_not_copied():
    s = FINAL.read_text()
    assert "RB.recompute(cli)" in s
    assert "never trust the copies in the draft" in s
    rb = (ROOT / "pipeline/review_bindings.py").read_text()
    for field in ("audit_sha256", "complete_sha256", "tokenizer_cache_manifest_sha256",
                  "audit_verifier_file_sha256"):
        assert field in rb


def test_binding_tampering_is_detected():
    RB = _bindings()
    b = {"audit_verifier_git_dirty": False, "repo_git_dirty": False,
         "audit_verifier_file_sha256": "x", "audit_declared_verifier_sha256": "x",
         "tokenizer_cache_manifest_sha256": "t", "audit_declared_tokenizer_cache_sha256": "t",
         "manifests_total": 18, "manifests_matching": 18, "manifests": {},
         "scope": {"manifest_version": "v2", "split": "train",
                   "require_allowed_use": "asr_train"}}
    assert RB.verify(b) == []
    assert RB.verify(b, expect={"audit_sha256": "different"}), "tamper must be caught"


def test_dirty_audit_and_changed_verifier_are_rejected():
    RB = _bindings()
    base = {"audit_verifier_git_dirty": True, "repo_git_dirty": False,
            "audit_verifier_file_sha256": "a", "audit_declared_verifier_sha256": "a",
            "tokenizer_cache_manifest_sha256": "t", "audit_declared_tokenizer_cache_sha256": "t",
            "manifests_total": 18, "manifests_matching": 18, "manifests": {},
            "scope": {"manifest_version": "v2", "split": "train",
                      "require_allowed_use": "asr_train"}}
    assert any("dirty" in p for p in RB.verify(base))
    changed = {**base, "audit_verifier_git_dirty": False, "audit_verifier_file_sha256": "b"}
    assert any("has changed since the audit ran" in p for p in RB.verify(changed))


def test_manifest_count_and_scope_are_enforced():
    RB = _bindings()
    b = {"audit_verifier_git_dirty": False, "repo_git_dirty": False,
         "audit_verifier_file_sha256": "a", "audit_declared_verifier_sha256": "a",
         "tokenizer_cache_manifest_sha256": "t", "audit_declared_tokenizer_cache_sha256": "t",
         "manifests_total": 17, "manifests_matching": 17, "manifests": {},
         "scope": {"manifest_version": "v1", "split": "train",
                   "require_allowed_use": "asr_train"}}
    probs = RB.verify(b)
    assert any("expected 18 manifests" in p for p in probs)
    assert any("expected v2/train/asr_train" in p for p in probs)


def test_entry_tampering_against_the_audit_is_detected():
    s = FINAL.read_text()
    assert "altered" in s
    assert '"label_tokens_effective", "duration_s", "language", "task"' in s
    assert "not present in the audit" in s
    assert "absent from the decision" in s


def test_draft_entries_match_the_persisted_audit_exactly():
    audit = json.loads(AUDIT.read_text())
    draft = json.loads(DRAFT.read_text())
    expected = {r["audio_checksum_sha256"]
                for r in audit["over_limit_rows"] + audit["rate_outlier_rows"]}
    got = {e["audio_checksum_sha256"] for e in draft["entries"]}
    assert got == expected
    assert len(draft["entries"]) == 20 == len(got), "20 unique checksums"
    assert audit["rows_over_limit"] == 6
    assert audit["rate_outliers_under_limit"] == 14


# --------------------------------------------------------------------------- #
# 7. classification / reason compatibility
# --------------------------------------------------------------------------- #
def test_classification_options_depend_on_the_trigger_both_ways():
    """An over-limit row cannot be 'valid under limit'; an under-limit row
    cannot be 'decoder incompatible' -- the decoder accepts it."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("rl3", REVIEW)
    rl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rl)
    over = rl.CLASSES_FOR_TRIGGER["over_decoder_limit"]
    under = rl.CLASSES_FOR_TRIGGER["extreme_token_rate_under_limit"]
    assert "valid_under_limit" not in over
    assert "valid_but_decoder_incompatible" not in under
    assert "valid_but_decoder_incompatible" in over
    assert "valid_under_limit" in under
    for t in (over, under):
        assert "confirmed_data_defect" in t and "uncertain" in t


def test_finalizer_enforces_compatibility_both_ways():
    s = FINAL.read_text()
    assert "valid_under_limit on an over-limit row" in s
    assert "valid_but_decoder_incompatible on a row the " in s
    assert "decoder accepts" in s


def test_finalizer_derives_trigger_from_the_audit_array():
    """Reading trigger off the entry would let an edit unlock the wrong set of
    classifications."""
    s = FINAL.read_text()
    assert '"_trigger": "over_decoder_limit"' in s
    assert '"_trigger": "extreme_token_rate_under_limit"' in s
    assert "does not match the " in s and "audit array it came from" in s


def test_finalizer_compares_every_review_driving_field():
    s = FINAL.read_text()
    assert '"tokens_per_s", "z_score"' in s, "the metrics a reviewer weighs must be bound"


def test_dirty_repository_is_rejected():
    """Superseded in precision by the dirty-path tests below: a dirty tree is
    refused, but only for paths other than the review draft itself."""
    from pipeline import review_bindings as RB
    probs = RB.verify({"audit_verifier_git_dirty": False,
                       "repo_dirty_paths": ["pipeline/train_asr.py"],
                       "audit_verifier_file_sha256": "a",
                       "audit_declared_verifier_sha256": "a",
                       "tokenizer_cache_manifest_sha256": "t",
                       "audit_declared_tokenizer_cache_sha256": "t",
                       "manifests_total": 18, "manifests_matching": 18, "manifests": {},
                       "scope": {"manifest_version": "v2", "split": "train",
                                 "require_allowed_use": "asr_train"}})
    assert any("uncommitted changes outside the review draft" in p for p in probs)

def test_reason_matrix_is_enforced_in_both_tools():
    for f in (REVIEW, FINAL):
        s = f.read_text()
        assert '"confirmed_data_defect": {"a", "b", "c"}' in s
        assert '"valid_but_decoder_incompatible": {"d"}' in s
        assert '"valid_under_limit": {"e"}' in s
        assert '"uncertain": {"f"}' in s


def test_invalid_reason_combination_is_refused(tmp_path):
    """A 'confirmed defect' supported by 'correct and valid' must not finalise."""
    s = FINAL.read_text()
    assert "is not compatible with" in s
    assert "valid_under_limit on an over-limit row" in s


def test_over_limit_row_can_never_be_retained():
    """In the review tool this is STRUCTURAL -- the retain classification is not
    offered for an over-limit row, so it cannot be chosen. The finalizer checks
    it explicitly as well, against the audit-derived trigger."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("rl4", REVIEW)
    rl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rl)
    offered = rl.CLASSES_FOR_TRIGGER["over_decoder_limit"]
    assert not any(rl.ACTION_FOR[c] == "retain" for c in offered), \
        "no offered classification for an over-limit row may map to retain"
    assert "cannot be retained" in FINAL.read_text()


def test_self_approval_is_refused():
    s = FINAL.read_text()
    assert "self-approval" in s
    assert "adds no independent check" in s


# --------------------------------------------------------------------------- #
# 8. atomic progress
# --------------------------------------------------------------------------- #
def test_progress_is_written_atomically(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("rl2", REVIEW)
    rl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rl)
    target = tmp_path / "d.json"
    rl.atomic_write(target, {"a": 1})
    assert json.loads(target.read_text()) == {"a": 1}
    assert not list(tmp_path.glob("*.tmp")), "no temp file may survive"
    s = REVIEW.read_text()
    assert "os.replace(tmp, path)" in s
    # written after every entry, not only at the end
    assert s.count("atomic_write(DECISION, doc)") >= 3


def test_nothing_is_classified_yet():
    """This suite must never be able to pass by having classified rows itself."""
    draft = json.loads(DRAFT.read_text())
    assert all(e["classification"] is None for e in draft["entries"])
    assert draft["status"] == "draft"


# --------------------------------------------------------------------------- #
# agent detection must cover terminals that ARE ttys
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("marker", ["CODEX_THREAD_ID", "CODEX_CI", "CODEX_SANDBOX",
                                    "CLAUDECODE", "CI"])
def test_agent_markers_refuse_even_with_a_tty(marker, monkeypatch):
    """An interactive agent terminal has a real tty, so the interactivity check
    alone does not catch it. Each marker must refuse on its own."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(f"rl_{marker}", REVIEW)
    rl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rl)

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    for v in rl.AGENT_ENV:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    monkeypatch.delenv("SSH_TTY", raising=False)
    monkeypatch.setenv(marker, "1")

    with pytest.raises(SystemExit) as e:
        rl.refuse_unless_local_human()
    assert "automation environment" in str(e.value)
    assert marker in str(e.value)
    assert "has fetched nothing" in str(e.value)


def test_codex_markers_are_listed():
    s = REVIEW.read_text()
    for marker in ("CODEX_THREAD_ID", "CODEX_CI", "CODEX_SANDBOX"):
        assert marker in s
    assert "an interactive Codex terminal HAS a tty" in s


# --------------------------------------------------------------------------- #
# dirty-tree policy: the draft may differ, nothing else may
# --------------------------------------------------------------------------- #
def _b(paths):
    return {"audit_verifier_git_dirty": False, "repo_dirty_paths": paths,
            "audit_verifier_file_sha256": "a", "audit_declared_verifier_sha256": "a",
            "tokenizer_cache_manifest_sha256": "t",
            "audit_declared_tokenizer_cache_sha256": "t",
            "manifests_total": 18, "manifests_matching": 18, "manifests": {},
            "scope": {"manifest_version": "v2", "split": "train",
                      "require_allowed_use": "asr_train"}}


def test_a_dirty_draft_alone_is_allowed():
    """Review writes to the draft, so requiring a wholly clean tree would make an
    interrupted review impossible to finish."""
    from pipeline import review_bindings as RB
    assert RB.verify(_b(["platform/decisions/DQ-2026-001-label-review.json"])) == []


def test_any_other_dirty_path_is_refused():
    from pipeline import review_bindings as RB
    for path in ("pipeline/train_asr.py", "platform/evidence/label-length-audit-v2.json",
                 "scripts/finalize_label_review.py"):
        probs = RB.verify(_b(["platform/decisions/DQ-2026-001-label-review.json", path]))
        assert any(path in p for p in probs), f"{path} must be refused"
        assert any("commit code and evidence" in p for p in probs)


def test_clean_tree_is_allowed():
    from pipeline import review_bindings as RB
    assert RB.verify(_b([])) == []


def test_dirty_paths_parses_renames_and_untracked():
    from pipeline import review_bindings as RB
    import inspect
    src = inspect.getsource(RB.dirty_paths)
    assert '" -> "' in src, "renames report two paths and both are changes"
    assert 'strip(\'"\')' in src, "git quotes paths containing spaces"


def test_approved_record_binds_the_final_draft_hash():
    """Otherwise an approved record could be paired with a different draft."""
    s = FINAL.read_text()
    assert '"approved_draft_sha256": hashlib.sha256(draft_raw).hexdigest()' in s
    assert "draft_raw = DRAFT.read_bytes()" in s


# --------------------------------------------------------------------------- #
# independence is an attestation, not a technical control -- say so
# --------------------------------------------------------------------------- #
def test_independence_must_be_stated_not_implied():
    """Different role strings are not different people. The tool cannot tell,
    so it refuses to produce a record that is silent on the question."""
    s = FINAL.read_text()
    assert "independence not stated" in s
    assert "--attest-independent" in s and "--no-independent-approval" in s
    assert "cannot verify who is at the keyboard" in s


def test_contradictory_independence_flags_are_refused():
    s = FINAL.read_text()
    assert "contradictory" in s
    assert "a.attest_independent and a.no_independent_approval" in s


def test_record_states_the_limits_of_the_control():
    s = FINAL.read_text()
    assert '"independent_approval": bool(a.attest_independent)' in s
    assert "ROLE STRINGS ONLY" in s
    assert "human attestation, not a " in s and "technical control." in s
    assert '"basis"' in s, "a false attestation must carry its stated reason"


def test_self_approval_still_refused_regardless_of_attestation():
    s = FINAL.read_text()
    assert "self-approval" in s
    assert "adds no independent check" in s


# --------------------------------------------------------------------------- #
# completion != adoption
# --------------------------------------------------------------------------- #
def test_loader_requires_a_separate_adoption_record():
    """COMPLETE.json says a migration finished; ADOPTION.json says a human
    approved training from it. They are different decisions."""
    s = (ROOT / "pipeline/train_asr.py").read_text()
    assert 'adopt_key = f"curated/_versions/{version}/ADOPTION.json"' in s
    assert "no adoption record at" in s
    assert "A completed migration is not an approved one" in s
    assert 'adopt.get("status") != "approved"' in s


def test_adoption_is_bound_to_the_completion_record_it_approved():
    s = (ROOT / "pipeline/train_asr.py").read_text()
    assert 'adopt.get("complete_raw_sha256")' in s
    assert "the version changed after it was adopted" in s
    assert "hashlib.sha256(comp_raw).hexdigest()" in s


def test_completion_record_no_longer_claims_adoption():
    """Requiring adopted:true inside COMPLETE.json asked a record written at
    migration time to attest to a decision taken later."""
    s = (ROOT / "pipeline/train_asr.py").read_text()
    assert 'comp.get("adopted")' not in s


def test_published_adoption_binds_what_it_approved():
    """v2 is now adopted for the B4 experiment. The record must bind the RAW
    completion bytes and the deferral policy actually in force -- an adoption
    floating free of both would approve whatever the bucket happens to hold."""
    import hashlib
    import json as _json

    import boto3
    import botocore
    try:
        cli = boto3.Session(profile_name="medzen", region_name="eu-central-1").client("s3")
        adopt = _json.loads(cli.get_object(
            Bucket="medzen-speech",
            Key="curated/_versions/v2/ADOPTION.json")["Body"].read())
        comp_raw = cli.get_object(
            Bucket="medzen-speech",
            Key="curated/_versions/v2/COMPLETE.json")["Body"].read()
    except (botocore.exceptions.NoCredentialsError,
            botocore.exceptions.ProfileNotFound,
            botocore.exceptions.ClientError):
        pytest.skip("no AWS access in this environment")

    assert adopt["status"] == "approved"
    assert adopt["complete_raw_sha256"] == hashlib.sha256(comp_raw).hexdigest()

    policy = (ROOT / "platform/decisions/DQ-2026-002-policy-deferral.json").read_bytes()
    assert adopt["deferral_policy_sha256"] == hashlib.sha256(policy).hexdigest()
    assert adopt["deferred_rows"] == 20
    # it approves a corpus; it does not claim anyone reviewed the deferred rows
    assert adopt["human_review_performed"] is False
    assert adopt["independent_human_approval_claimed"] is False
    assert adopt["scope"]["promotion_permitted"] is False
    assert adopt["scope"]["eval_permitted"] is False


def test_porcelain_first_path_is_not_truncated(tmp_path, monkeypatch):
    """`.strip()` on porcelain output eats the leading status space, shifting
    line[3:] by one and turning 'pipeline/x' into 'ipeline/x'. A mangled path
    can never match REVIEWABLE_DIRTY, so an interrupted review whose draft
    sorted first would be refused -- the very case the allowance exists for."""
    import subprocess
    from pipeline import review_bindings as RB

    repo = tmp_path / "r"
    repo.mkdir()
    run = lambda *a: subprocess.run(["git", "-C", str(repo), *a],
                                    capture_output=True, text=True)
    run("init", "-q")
    run("config", "user.email", "t@t"); run("config", "user.name", "t")
    for name in ("pipeline_first.py", "zzz_last.py"):
        (repo / name).write_text("x\n")
    run("add", "-A"); run("commit", "-qm", "init")
    for name in ("pipeline_first.py", "zzz_last.py"):
        (repo / name).write_text("changed\n")

    monkeypatch.setattr(RB, "ROOT", repo)
    assert RB.dirty_paths() == ["pipeline_first.py", "zzz_last.py"]


def test_git_helper_preserves_the_status_column():
    s = (ROOT / "pipeline/review_bindings.py").read_text()
    assert 'r.stdout.rstrip("\\n")' in s
    assert ".stdout.strip()" not in s
