from pathlib import Path


PLAN = Path("platform/decisions/PLAN-2026-014-b7-ci-cd-and-canary-rollback.md")


def text() -> str:
    return PLAN.read_text(encoding="utf-8")


def test_plan_is_review_only_and_binds_b6_closure() -> None:
    value = text()
    assert "PLANNING REVIEW ONLY - NOT EXECUTION AUTHORIZATION" in value
    assert "e04a4140491d7a5d0a389403bcc3c20eed3ca713" in value
    assert "7bbec6ed173fdf3b02d4038312cf4cdf5aa12d7b" in value
    assert "8f174c2ad41782ca3cb1300d9c888aac373487422c66b543f6ce8aa90a45d299" in value
    assert "COST-REGISTRY-2026-006" in value


def test_plan_contains_exactly_the_four_base_v5_pipelines() -> None:
    value = text()
    workflows = {
        ".github/workflows/application.yml",
        ".github/workflows/model.yml",
        ".github/workflows/content-rag.yml",
        ".github/workflows/infrastructure.yml",
    }
    assert all(workflow in value for workflow in workflows)
    assert value.count("Proposed workflow:") == 4


def test_model_pipeline_preserves_the_b5_block() -> None:
    value = text()
    assert "The current mode is **refusal-only**" in value
    assert "B5 remains `BLOCKED`" in value
    assert "zero registrations, approved objects, registry serving changes and deployments" in value
    assert "It may not\nregister a model" in value


def test_original_canary_thresholds_are_copied_without_recalibration() -> None:
    value = text()
    assert "error rate greater than `2%` over `5 minutes`" in value
    assert "p95 latency greater than `1.5x` the frozen baseline over `10 minutes`" in value
    assert "readiness failure" in value
    assert "Missing metrics never mean PASS" in value


def test_canary_rollback_is_compare_and_swap_and_non_serving_first() -> None:
    value = text()
    assert "compare-and-swap" in value
    assert "first live drill uses a non-serving test alias" in value
    assert "A production drill is required before first production traffic" in value
    assert "No image is rebuilt" in value


def test_oidc_is_least_privilege_and_static_keys_are_forbidden() -> None:
    value = text()
    for role in (
        "medzen-b7-plan-role",
        "medzen-b7-build-role",
        "medzen-b7-deploy-test-role",
        "medzen-b7-content-publisher-role",
        "medzen-b7-infra-apply-role",
        "medzen-b7-rollback-role",
    ):
        assert role in value
    assert "One broad\n`github-actions` role is prohibited" in value
    assert "uses no long-lived\nAWS keys" in value


def test_branch_protection_gap_fails_closed() -> None:
    value = text()
    assert "No apply-on-merge fallback is permitted" in value
    assert "manual `workflow_dispatch` only while branch protection is\nunavailable" in value
    assert "protected GitHub environments with independent manual review" in value


def test_completion_separates_engineering_from_production() -> None:
    value = text()
    assert "`B7_ENGINEERING_READY`" in value
    assert "`B7_PRODUCTION_AUTOMATION_BLOCKED`" in value
    assert "B7 must not be described as production release readiness" in value
