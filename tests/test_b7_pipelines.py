"""B7 Phase 1: the application pipelines obey the plan's hard rules.

These are structural assertions over the workflow YAML — the properties a
reviewer must never have to re-check by eye: OIDC-only auth, every action
pinned by commit SHA, the rollout gate present with its fail-the-pipeline
semantics, AWS-touching jobs dark until the CI role variable exists, and
one pipeline instance per service in platform/services.yaml.
"""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"

SHA_PIN = re.compile(r"uses:\s*\S+@[0-9a-f]{40}\b")


def _workflow_files():
    files = sorted(WORKFLOWS.glob("*.yml"))
    assert files, "no workflows found"
    return files


def test_every_third_party_action_is_pinned_by_full_sha():
    for path in _workflow_files():
        for line in path.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith("uses:") and "./.github/" not in stripped:
                assert SHA_PIN.search(stripped), f"{path.name}: unpinned action: {stripped}"


def test_no_static_aws_keys_anywhere():
    for path in _workflow_files():
        body = path.read_text()
        for marker in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "aws-access-key-id", "aws-secret-access-key"):
            assert marker not in body, f"{path.name} references static AWS keys"


MINIMAL = {"contents": "read", "id-token": "write"}
# Job-level additions must be documented here and nowhere else: the plan job
# posts the terraform plan for review; the drift job turns findings into
# visible work items. Nothing ever gets write on contents.
DOCUMENTED_JOB_ADDITIONS = {
    "infra-pipeline.yml": {
        "plan": {**MINIMAL, "pull-requests": "write"},
        "drift": {**MINIMAL, "issues": "write"},
    },
    "model-pipeline.yml": {
        # pushes ONLY the registry-bump branch and opens its PR; activation
        # of the actual bump is a B5-reactivation change (see the checklist).
        "open-registry-pr": {"contents": "write", "id-token": "write", "pull-requests": "write"},
    },
}


def _needs_oidc(path) -> bool:
    """A workflow earns id-token:write only by actually assuming an AWS
    role — directly, or by calling a local reusable workflow that does
    (the caller's grant must cover the callee). Codex review #18: the
    old rule handed every workflow id-token:write unconditionally."""
    body = path.read_text()
    # raw OIDC use (the hardened canaries call STS directly to observe
    # the exact error code — Codex review #27) counts too
    markers = ("configure-aws-credentials", "ACTIONS_ID_TOKEN_REQUEST",
               "assume-role-with-web-identity")
    if any(m in body for m in markers):
        return True
    for other in _workflow_files():
        if (f"uses: ./.github/workflows/{other.name}" in body
                and any(m in other.read_text() for m in markers)):
            return True
    return False


def test_permissions_are_minimal_everywhere():
    for path in _workflow_files():
        doc = yaml.safe_load(path.read_text())
        expected_top = MINIMAL if _needs_oidc(path) else {"contents": "read"}
        assert doc.get("permissions") == expected_top, (
            f"{path.name}: top-level permissions must be exactly "
            f"{expected_top} (id-token:write only with actual OIDC use)"
        )
        allowed = DOCUMENTED_JOB_ADDITIONS.get(path.name, {})
        for job_name, job in doc.get("jobs", {}).items():
            job_perms = job.get("permissions")
            if job_perms is None:
                continue
            assert job_perms == allowed.get(job_name), (
                f"{path.name}:{job_name}: undocumented job-level permissions {job_perms}"
            )
        for job_name, expected in allowed.items():
            assert doc["jobs"][job_name].get("permissions") == expected


def test_infra_apply_requires_the_protected_environment():
    doc = yaml.safe_load((WORKFLOWS / "infra-pipeline.yml").read_text())
    assert doc["jobs"]["apply"]["environment"] == "infrastructure"
    for job_name in ("plan", "apply", "drift"):
        assert "MEDZEN_CI_ROLE_ARN" in doc["jobs"][job_name]["if"]
    assert "if" not in doc["jobs"]["fmt-validate"], "offline validation must always run"
    assert "-auto-approve" not in str(doc["jobs"]["plan"]), "plan job must never apply"


def test_aws_touching_jobs_ship_dark_until_the_ci_role_exists():
    doc = yaml.safe_load((WORKFLOWS / "_service-pipeline.yml").read_text())
    for job_name in ("push", "deploy"):
        job = doc["jobs"][job_name]
        assert "MEDZEN_CI_ROLE_ARN" in job.get("if", ""), (
            f"{job_name} must be gated on the CI role variable"
        )
    for job_name in ("test", "build-scan"):
        assert "if" not in doc["jobs"][job_name], (
            f"{job_name} must always run — it needs no credentials"
        )


def test_rollout_gate_fails_pipeline_not_cluster():
    body = (WORKFLOWS / "_service-pipeline.yml").read_text()
    assert "rollout status" in body and "--timeout=10m" in body
    assert "exit 1" in body, "a stuck rollout must fail the pipeline"
    for forbidden in ("rollout undo", "kubectl delete", "--force", "--grace-period"):
        assert forbidden not in body, f"pipeline must never force the cluster: {forbidden}"


def test_scan_is_fail_closed_on_critical_and_high():
    # B6v2 round 3: Docker Scout needs an entitlement the org lacks — the
    # step failed at login, i.e. no scan ever ran. Trivy is unauthenticated.
    body = (WORKFLOWS / "_service-pipeline.yml").read_text()
    assert "docker/scout-action" not in body, "Scout cannot run in this org"
    assert "aquasecurity/trivy-action" in body
    assert "severity: CRITICAL,HIGH" in body
    assert 'exit-code: "1"' in body


def test_test_job_installs_the_service_pins_not_the_training_stack():
    # The repo-root requirements.txt is the data/training stack; installing
    # it gave the orchestrator a floating mlflow-transitive fastapi and no
    # python-multipart (every multipart parse became INVALID_REQUEST 400).
    body = (WORKFLOWS / "_service-pipeline.yml").read_text()
    assert "-r ${{ inputs.context_path }}/requirements.txt" in body
    assert "pip install -r requirements.txt" not in body
    assert '"httpx==0.28.1"' in body, "starlette's TestClient needs httpx"


def test_one_pipeline_instance_per_declared_service():
    services = yaml.safe_load((ROOT / "platform/services.yaml").read_text())["services"]
    for name in services:
        instance = WORKFLOWS / f"app-{name}.yml"
        assert instance.is_file(), f"service {name} has no pipeline instance"
        doc = yaml.safe_load(instance.read_text())
        job = doc["jobs"]["pipeline"]
        assert job["uses"] == "./.github/workflows/_service-pipeline.yml"
        assert job["with"]["service"] == name
        context = ROOT / job["with"]["context_path"]
        assert (context / "Dockerfile").is_file(), f"{name}: context has no Dockerfile"


def test_instances_trigger_only_on_their_own_paths():
    services = yaml.safe_load((ROOT / "platform/services.yaml").read_text())["services"]
    for name in services:
        doc = yaml.safe_load((WORKFLOWS / f"app-{name}.yml").read_text())
        paths = doc[True]["push"]["paths"] if True in doc else doc["on"]["push"]["paths"]
        context = [p for p in paths if p.startswith("services/")]
        assert len(context) == 1, f"{name}: must watch exactly its own service tree"
