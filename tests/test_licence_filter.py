"""T3: the licence-policy gate fails closed on absence, unknowns and NC."""

import pytest

from pipeline.licence_filter import (
    DEFAULT_ALLOWED,
    LicencePolicyRefusal,
    filter_training_rows,
)


def _row(policy, lang="alpha"):
    return {"license_policy": policy, "primary_language": lang}


def test_default_gate_excludes_sharealike_and_reports():
    rows = [_row("cc0"), _row("commercial_ok"), _row("cc_by_4_0"),
            _row("sharealike_review"), _row("sharealike_review")]
    eligible, report = filter_training_rows(rows)
    assert len(eligible) == 3
    assert report["excluded_rows_by_policy"] == {"sharealike_review": 2}
    assert report["attribution_required"] is True


def test_missing_policy_refuses():
    with pytest.raises(LicencePolicyRefusal, match="no default-permit"):
        filter_training_rows([_row("cc0"), {"primary_language": "x"}])


def test_unknown_policy_refuses():
    with pytest.raises(LicencePolicyRefusal, match="unknown license_policy"):
        filter_training_rows([_row("some_new_licence")])


def test_widening_after_legal_review_is_an_explicit_code_change():
    rows = [_row("sharealike_review")]
    widened = frozenset(DEFAULT_ALLOWED | {"sharealike_review"})
    eligible, report = filter_training_rows(rows, allowed=widened)
    assert len(eligible) == 1 and report["excluded_rows_by_policy"] == {}


def test_never_train_policies_cannot_be_allowed():
    with pytest.raises(LicencePolicyRefusal, match="never be allowed"):
        filter_training_rows([_row("cc0")], allowed=frozenset({"cc0", "nc"}))
