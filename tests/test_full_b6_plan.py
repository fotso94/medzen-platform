from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "platform/decisions/PLAN-2026-013-full-b6-serving.md"


def text() -> str:
    return PLAN.read_text()


def test_full_b6_plan_covers_the_six_point_review_agenda():
    value = text()
    for heading in (
        "## 1. Contract adoption first",
        "## 2. Build order",
        "## 3. Per-service IAM boundary",
        "## 4. Required failure drills",
        "## 5. Cost model and packet boundaries",
        "## 6. Parallel ASR base-model decision",
    ):
        assert heading in value


def test_full_b6_plan_places_non_serving_ssm_publication_before_integration():
    value = text()
    publication = value.index("### B6.5A - Versioned SSM test-registry publication")
    integration = value.index("### B6.6 - Bounded EKS integration")
    assert publication < integration
    assert "/medzen/registry/test/b6/<snapshot-sha256>/*" in value
    assert "may not write\n`/medzen/registry/serving/current`" in value
    assert "small, separate AWS packet" in value


def test_full_b6_plan_names_meta_omnilingual_asr_shortlist():
    assert "Meta Omnilingual ASR" in text()


def test_full_b6_plan_starts_from_the_unified_master_and_is_not_authorization():
    value = text()
    assert "20b4b4fdcbe42477907838ec01ed616e92f05149" in value
    assert "PROPOSED - NOT EXECUTION AUTHORIZATION" in value
    assert "no new AWS resource" in value


def test_full_b6_plan_preserves_b5_and_language_boundaries():
    value = text()
    assert "B5 remains `BLOCKED`" in value
    assert "No language is silently reactivated" in value
    assert "v0 remains a platform-test model" in value


def test_full_b6_plan_requires_zero_compute_for_local_builds():
    value = text()
    assert "local build/test first with CPU=0 and GPU=0" in value
    cpu = (ROOT / "infra/b6_planning_override.tf").read_text()
    assert "desired_size = 0" in cpu
    assert "min_size     = 0" in cpu


def test_cpu_zero_override_preserves_historical_b6a_eks_source_hash():
    import hashlib

    eks = ROOT / "infra/eks.tf"
    assert hashlib.sha256(eks.read_bytes()).hexdigest() == (
        "37103846a11bcdb2e2aca5f81f221d6ee767675c77481b5451484447fd0aca7b"
    )


def test_full_b6_plan_corrects_tts_and_pod_identity_assumptions():
    value = text()
    assert "do not reuse or modify" in value
    assert "share a Pod service account" in " ".join(value.split())
    architecture = (ROOT / "ARCHITECTURE.md").read_text()
    assert "Local mocked B6.5 implementation exists" in architecture
    assert "shares the ASR pod role" in architecture
    assert "ASR runtime validates that marker" in architecture
