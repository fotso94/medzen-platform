"""The deferral policy must stay a policy: a decision about one run, never a
finding about the data. These tests exist because the failure mode is silent --
a record that reads like review, and a corpus quietly shrunk on that basis.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
POLICY = ROOT / "platform/decisions/DQ-2026-002-policy-deferral.json"
DRAFT = ROOT / "platform/decisions/DQ-2026-001-label-review.json"
AUDIT = ROOT / "platform/evidence/label-length-audit-v2.json"
MAKE = ROOT / "scripts/make_deferral_policy.py"
PUBLISH = ROOT / "scripts/publish_adoption.py"


@pytest.fixture(scope="module")
def policy():
    return json.loads(POLICY.read_bytes())


# --------------------------------------------------------------------------- #
# it says, unmissably, that nobody looked
# --------------------------------------------------------------------------- #
def test_declares_no_human_review(policy):
    assert policy["human_review_performed"] is False
    assert policy["decision_type"] == "policy_deferral"
    assert all(e["human_reviewed"] is False for e in policy["exclusions"])


def test_no_row_is_called_defective(policy):
    """Nobody listened, so nothing about the content is known. A deferral that
    quietly implied 'defect' would be a finding no evidence supports."""
    assert policy["counts"]["defects"] == 0
    assert all(e["defect"] is False for e in policy["exclusions"])
    assert {e["classification"] for e in policy["exclusions"]} == {
        "unreviewed_anomaly_deferred_by_policy"}
    assert {e["action"] for e in policy["exclusions"]} == {"defer_pending_review"}


def test_scope_is_one_experiment_and_forbids_promotion(policy):
    s = policy["scope"]
    assert s["promotion_permitted"] is False
    assert s["distribution_permitted"] is False
    assert s["artifacts"] == "candidates/ only"
    assert "human review" in s["reuse_requires"]


def test_does_not_close_the_open_human_review(policy):
    assert policy["relates_to"]["review_draft_status"] == "draft"
    assert policy["relates_to"]["review_draft_classified_entries"] == 0
    assert json.loads(DRAFT.read_bytes())["status"] == "draft"
    assert all(e["classification"] is None
               for e in json.loads(DRAFT.read_bytes())["entries"])


# --------------------------------------------------------------------------- #
# exactly the 20, and exactly the ones the audit flagged
# --------------------------------------------------------------------------- #
def test_exactly_twenty_unique_rows_six_plus_fourteen(policy):
    e = policy["exclusions"]
    assert len(e) == 20
    assert len({x["audio_checksum_sha256"] for x in e}) == 20
    by = {}
    for x in e:
        by[x["trigger"]] = by.get(x["trigger"], 0) + 1
    assert by == {"over_decoder_limit": 6, "extreme_token_rate_under_limit": 14}
    assert policy["counts"] == {"total": 20, "over_decoder_limit": 6,
                                "extreme_token_rate_under_limit": 14,
                                "defects": 0, "excluded_as_defective": 0}


def test_checksums_are_exactly_the_audit_rows(policy):
    audit = json.loads(AUDIT.read_bytes())
    expected = {r["audio_checksum_sha256"] for r in audit["over_limit_rows"]}
    expected |= {r["audio_checksum_sha256"] for r in audit["rate_outlier_rows"]}
    assert {e["audio_checksum_sha256"] for e in policy["exclusions"]} == expected


def test_trigger_agrees_with_the_token_count(policy):
    limit = json.loads(AUDIT.read_bytes())["label_limit"]
    for e in policy["exclusions"]:
        over = e["label_tokens_effective"] > limit
        assert over == (e["trigger"] == "over_decoder_limit"), e["audio_checksum_sha256"][:16]


# --------------------------------------------------------------------------- #
# bindings
# --------------------------------------------------------------------------- #
def test_bound_to_the_clean_audit(policy):
    b = policy["bindings"]
    audit_raw = AUDIT.read_bytes()
    assert b["audit_sha256"] == hashlib.sha256(audit_raw).hexdigest()
    assert json.loads(audit_raw)["verifier"]["git_dirty"] is False
    assert b["audit_scope"] == {"manifest_version": "v2", "split": "train",
                                "require_allowed_use": "asr_train",
                                "rows_skipped_not_permitted": 0}


def test_binds_complete_raw_bytes_all_manifests_and_the_tokenizer(policy):
    b = policy["bindings"]
    assert len(b["v2_complete_raw_sha256"]) == 64
    assert b["manifests_total"] == 18 and len(b["manifests"]) == 18
    assert all(len(v) == 64 for v in b["manifests"].values())
    assert b["tokenizer_revision"] == "06f233fe06e710322aca913c1bc4249a0d71fce1"
    assert len(b["tokenizer_cache_manifest_sha256"]) == 64


def test_checksum_digest_covers_the_twenty(policy):
    got = hashlib.sha256("\n".join(sorted(
        e["audio_checksum_sha256"] for e in policy["exclusions"])).encode()).hexdigest()
    assert policy["bindings"]["deferred_checksums_sha256"] == got


# --------------------------------------------------------------------------- #
# PII
# --------------------------------------------------------------------------- #
def test_carries_no_content_or_identifiers(policy):
    allowed = {"audio_checksum_sha256", "language", "task", "duration_s",
               "label_tokens_effective", "tokens_per_s", "z_score", "trigger",
               "classification", "action", "defect", "reason_code",
               "human_reviewed"}
    for e in policy["exclusions"]:
        assert set(e) <= allowed, set(e) - allowed
    # Scan the DATA, not the prose: content_policy legitimately names the very
    # fields it promises are absent, so scanning the whole document would fail
    # on its own disclaimer.
    blob = json.dumps({"exclusions": policy["exclusions"],
                       "bindings": policy["bindings"],
                       "counts": policy["counts"]})
    for banned in ("text_normalized", "audio_filepath", "transcript", "speaker",
                   "session", "s3://"):
        assert banned not in blob, banned
    assert "no transcript" in policy["content_policy"]


# --------------------------------------------------------------------------- #
# the generator cannot be used to launder review
# --------------------------------------------------------------------------- #
def test_generator_refuses_when_the_draft_has_been_classified(tmp_path):
    src = MAKE.read_text()
    assert 'e.get("classification") is not None' in src
    assert "must be finalised through" in src
    assert 'draft.get("status") != "draft"' in src


def test_generator_hardcodes_the_conservative_values():
    src = MAKE.read_text()
    assert 'CLASSIFICATION = "unreviewed_anomaly_deferred_by_policy"' in src
    assert 'ACTION = "defer_pending_review"' in src
    assert '"defect": False' in src
    assert '"human_review_performed": False' in src


def test_generator_refuses_a_dirty_tree():
    src = MAKE.read_text()
    assert "commit code and evidence before writing policy" in src


# --------------------------------------------------------------------------- #
# adoption publisher
# --------------------------------------------------------------------------- #
def test_publisher_refuses_to_overwrite_an_existing_adoption():
    src = PUBLISH.read_text()
    assert "already exists" in src
    assert "one-time decision" in src


def test_publisher_does_not_read_an_error_as_an_absence():
    """AccessDenied or a network failure is not proof the key is missing.
    Treating it as such lets a transient error authorise an overwrite."""
    src = PUBLISH.read_text()
    assert "An error is not an absence." in src
    assert 'code not in ("404", "NoSuchKey", "NotFound")' in src
    assert "except Exception:\n        pass" not in src


def test_publisher_writes_conditionally():
    """head_object then put_object is a race: the key can appear in between."""
    src = PUBLISH.read_text()
    assert 'IfNoneMatch="*"' in src
    assert "PreconditionFailed" in src
    assert "created concurrently" in src
    assert "ParamValidationError" in src, \
        "a botocore without conditional writes must refuse, not fall back"


def test_publisher_binds_raw_bytes_and_the_policy():
    src = PUBLISH.read_text()
    assert "hashlib.sha256(comp_raw).hexdigest()" in src
    assert '"deferral_policy_sha256": policy_sha' in src
    assert 'policy.get("decision_type") != "policy_deferral"' in src
    assert 'policy.get("human_review_performed") is not False' in src


def test_publisher_does_not_claim_worm():
    """Versioning is not Object Lock. Saying 'immutable' without that
    distinction would overstate what the bucket enforces."""
    src = PUBLISH.read_text()
    assert "Object Lock is NOT configured" in src
    assert "must not be described as WORM" in src
    assert "not WORM" in src
    assert '"immutability"' in src, "the record itself must state what it is not"


def test_publisher_defaults_to_dry_run():
    src = PUBLISH.read_text()
    assert '"--upload", action="store_true"' in src
    assert "dry run" in src


# --------------------------------------------------------------------------- #
# end to end: the policy the loader would read is the one on disk
# --------------------------------------------------------------------------- #
def test_loader_accepts_this_policy_and_reports_its_hash():
    sys.path.insert(0, str(ROOT))
    from pipeline.train_asr import load_exclusions
    out, doc, sha = load_exclusions(str(POLICY), expect=20)
    assert len(out) == 20
    assert doc["decision_type"] == "policy_deferral"
    assert sha == hashlib.sha256(POLICY.read_bytes()).hexdigest()


def test_loader_refuses_a_policy_that_claims_a_defect(tmp_path):
    sys.path.insert(0, str(ROOT))
    from pipeline.train_asr import load_exclusions
    doc = json.loads(POLICY.read_bytes())
    doc["exclusions"][0]["defect"] = True
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(doc))
    with pytest.raises(SystemExit) as e:
        load_exclusions(str(p))
    assert "claim a defect" in str(e.value)


def test_loader_refuses_a_policy_that_permits_promotion(tmp_path):
    sys.path.insert(0, str(ROOT))
    from pipeline.train_asr import load_exclusions
    doc = json.loads(POLICY.read_bytes())
    doc["scope"]["promotion_permitted"] = True
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(doc))
    with pytest.raises(SystemExit) as e:
        load_exclusions(str(p))
    assert "must forbid promotion" in str(e.value)


def test_loader_refuses_the_wrong_count(tmp_path):
    sys.path.insert(0, str(ROOT))
    from pipeline.train_asr import load_exclusions
    doc = json.loads(POLICY.read_bytes())
    doc["exclusions"] = doc["exclusions"][:19]
    p = tmp_path / "short.json"
    p.write_text(json.dumps(doc))
    with pytest.raises(SystemExit) as e:
        load_exclusions(str(p), expect=20)
    assert "expected exactly 20" in str(e.value)


# --------------------------------------------------------------------------- #
# the CORRECTED 19-row policy (DQ-2026-003)
# --------------------------------------------------------------------------- #
P3 = ROOT / "platform/decisions/DQ-2026-003-policy-deferral-corrected.json"
AUDIT3 = ROOT / "platform/evidence/label-length-audit-v2-corrected.json"


@pytest.fixture(scope="module")
def policy3():
    return json.loads(P3.read_bytes())


def test_corrected_policy_defers_nineteen_not_twenty(policy3):
    """The bos defect inflated every row by one token; one amharic row sat
    exactly on the 448 boundary and was never over it."""
    e = policy3["exclusions"]
    assert len(e) == 19
    assert len({x["audio_checksum_sha256"] for x in e}) == 19
    by = {}
    for x in e:
        by[x["trigger"]] = by.get(x["trigger"], 0) + 1
    assert by == {"over_decoder_limit": 5, "extreme_token_rate_under_limit": 14}


def test_corrected_policy_is_still_a_policy_not_a_finding(policy3):
    assert policy3["human_review_performed"] is False
    assert policy3["decision_type"] == "policy_deferral"
    assert policy3["counts"]["defects"] == 0
    assert all(x["defect"] is False for x in policy3["exclusions"])
    assert {x["action"] for x in policy3["exclusions"]} == {"defer_pending_review"}
    assert {x["reason_code"] for x in policy3["exclusions"]} == {
        "policy_deferral_no_human_review"}
    assert policy3["scope"]["promotion_permitted"] is False


def test_corrected_policy_is_bound_to_the_corrected_audit(policy3):
    import hashlib
    assert policy3["bindings"]["audit_sha256"] == \
        hashlib.sha256(AUDIT3.read_bytes()).hexdigest()
    a = json.loads(AUDIT3.read_bytes())
    assert a["rows_over_limit"] == 5 and a["rate_outliers_under_limit"] == 14
    assert a["verifier"]["git_dirty"] is False


def test_the_boundary_row_is_no_longer_deferred(policy3):
    """d0ffd52881d0b074: raw 449, effective 448 == the limit."""
    checksums = {x["audio_checksum_sha256"] for x in policy3["exclusions"]}
    dropped = [c for c in checksums if c.startswith("d0ffd52881d0b074")]
    assert not dropped, "the boundary row must not be in the corrected policy"
    old = json.loads((ROOT / "platform/decisions/"
                      "DQ-2026-002-policy-deferral.json").read_bytes())
    old_cs = {x["audio_checksum_sha256"] for x in old["exclusions"]}
    assert len(old_cs - checksums) == 1, "exactly one row leaves the set"


def test_old_twenty_row_policy_is_untouched():
    """Superseded, not rewritten -- it is what the failed run actually used."""
    old = json.loads((ROOT / "platform/decisions/"
                      "DQ-2026-002-policy-deferral.json").read_bytes())
    assert len(old["exclusions"]) == 20


def test_generator_requires_explicit_counts():
    """A policy must never be regenerated silently against a corpus whose
    flagged set has moved."""
    src = MAKE.read_text()
    assert '"--expect-over"' in src and '"--expect-rate"' in src
    assert "a.expect_over, a.expect_rate" in src
    assert "The counts are NOT hardcoded" in src


def test_frozen_validation_sets_are_recorded():
    v = json.loads((ROOT / "platform/evidence/"
                    "VAL-2026-001-frozen-validation-sets.json").read_bytes())
    assert v["languages"] == 9 and v["total_rows"] == 385
    assert set(v["sets"]) == {"acholi", "akan", "amharic", "ewe", "fula",
                              "lingala", "luganda", "oromo", "shona"}
    for lang, s in v["sets"].items():
        assert len(s["manifest_sha256"]) == 64, lang
        assert s["rows"] > 0 and s["speakers"] > 0 and s["sessions"] > 0
        assert s["baseline_wer"] is not None, lang
    assert "NOT a promotion holdout" in v["purpose"]
