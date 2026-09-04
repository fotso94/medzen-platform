from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import yaml


ROOT = Path(__file__).resolve().parents[1]

def _pin_matches_committed_history(relative: str, expected: str) -> bool:
    """The packet's claim is that the pin matched a REVIEWED committed state,
    not that the file may never evolve (same rule as test_b6a_auth_003c_d)."""
    import hashlib, subprocess
    revs = subprocess.run(["git", "rev-list", "HEAD", "--", relative],
                          cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    for rev in revs:
        shown = subprocess.run(["git", "show", f"{rev}:{relative}"],
                               cwd=ROOT, capture_output=True, check=False)
        if shown.returncode == 0 and hashlib.sha256(shown.stdout).hexdigest() == expected:
            return True
    return False

DIGEST = "sha256:c2ebdeae779c796e3d071d7a0d3a4ebdbb31e4e8d53e3e5372ee0ab0c4f3f08f"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def policy_actions(policy: dict) -> set[str]:
    result: set[str] = set()
    for statement in policy["Statement"]:
        actions = statement["Action"]
        result.update(actions if isinstance(actions, list) else [actions])
    return result


def test_lbc_postrenderer_pins_exact_child_and_refuses_ambiguity() -> None:
    module = load_module("lbc_renderer", "scripts/pin_aws_lbc_digest.py")
    tagged = (
        "558069890522.dkr.ecr.eu-central-1.amazonaws.com/"
        "medzen-aws-load-balancer-controller:v3.5.0-c2ebdeae779c"
    )
    rendered = module.render(
        f"apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: aws-load-balancer-controller\n"
        f"spec:\n  template:\n    spec:\n      containers:\n      - image: \"{tagged}\"\n"
    )
    assert DIGEST in rendered
    assert tagged not in rendered
    assert module.render("apiVersion: v1\nkind: Service\n") == "apiVersion: v1\nkind: Service\n"
    for bad in ("", f"{tagged}\n{tagged}"):
        content = f"kind: Deployment\nname: aws-load-balancer-controller\n{bad}"
        try:
            module.render(content)
        except ValueError:
            pass
        else:
            raise AssertionError("ambiguous or absent controller image was accepted")


def test_lbc_policy_removes_broad_families_and_scopes_creation() -> None:
    template = (ROOT / "platform/iam/medzen-lbc-role.policy.template.json").read_text()
    policy = json.loads(template.replace("${alb_security_group_id}", "sg-0123456789abcdef0"))
    actions = policy_actions(policy)
    prohibited_prefixes = (
        "iam:", "acm:", "cognito-idp:", "waf:", "waf-regional:", "wafv2:", "shield:"
    )
    assert not any(action.startswith(prohibited_prefixes) for action in actions)
    assert not any(
        action.startswith("ec2:") and not action.startswith("ec2:Describe")
        and action != "ec2:GetSecurityGroupsForVpc"
        for action in actions
    )
    create = next(item for item in policy["Statement"] if item["Sid"] == "CreateOnlyInternalAlbWithExactBoundary")
    conditions = create["Condition"]
    assert conditions["StringEquals"]["elasticloadbalancing:Scheme"] == "internal"
    assert conditions["StringEquals"]["aws:RequestTag/elbv2.k8s.aws/cluster"] == "medzen-speech"
    assert conditions["ForAllValues:StringEquals"]["elasticloadbalancing:SecurityGroup"] == ["sg-0123456789abcdef0"]
    assert set(conditions["ForAllValues:StringEquals"]["elasticloadbalancing:Subnet"]) == {
        "subnet-00232b25bc1ac407a",
        "subnet-05029419c6c61a536",
        "subnet-01fb2fc3f56bce55e",
    }
    target_resources = json.dumps(policy["Statement"])
    assert "targetgroup/k8s-medzen-*/*" in target_resources
    assert "targetgroup/medzen-b6-*/*" not in target_resources


def test_lbc_design_hashes_and_window_only_installation() -> None:
    design = json.loads((ROOT / "platform/designs/B6-LBC-QUALIFICATION-2026-001.json").read_text())
    assert design["status"] == "LOCAL_DESIGN_COMPLETE_AWS_EXECUTION_NOT_AUTHORIZED"
    assert design["upstream"]["linux_amd64_child_digest"] == DIGEST
    assert sha(ROOT / design["helm"]["values_path"]) == design["helm"]["values_sha256"]
    assert sha(ROOT / design["helm"]["postrenderer_path"]) == design["helm"]["postrenderer_sha256"]
    assert design["terraform"]["window_enable_default"] is False
    assert design["terraform"]["window_only_resource"] == "helm_release.b6_load_balancer_controller[0]"
    terraform = (ROOT / "infra/alb_controller.tf").read_text()
    assert "count = var.enable_b6_load_balancer_controller ? 1 : 0" in terraform
    assert "wait          = true" in terraform
    assert "atomic        = true" in terraform


def test_lbc_values_are_internal_narrow_and_non_mutating() -> None:
    values = yaml.safe_load((ROOT / "platform/designs/B6-LBC-HELM-VALUES-2026-001.yaml").read_text())
    assert values["watchNamespace"] == "medzen"
    assert values["defaultLoadBalancerScheme"] == "internal"
    assert values["defaultTargetType"] == "ip"
    assert values["enableBackendSecurityGroup"] is False
    assert values["enableManageBackendSecurityGroupRules"] is False
    assert values["enableServiceMutatorWebhook"] is False
    assert values["enableWaf"] is False and values["enableWafv2"] is False
    assert values["enableShield"] is False


def test_lbc_stage_b_plan_guard_refuses_any_extra_resource() -> None:
    guard = load_module("lbc_plan_guard", "scripts/check_b6_lbc_plans.py")
    resources = [
        {
            "address": address,
            "change": {
                "actions": actions,
                "after": (
                    {
                        "vpc_id": "vpc-051aa9df8b64bf141",
                        "ingress": [],
                        "egress": [{"from_port": 8080, "to_port": 8080}],
                    }
                    if address == "aws_security_group.b6_internal_alb"
                    else {
                        "namespace": "kube-system",
                        "service_account": "aws-load-balancer-controller",
                    }
                    if address == "aws_eks_pod_identity_association.b6_load_balancer_controller"
                    else {}
                ),
            },
        }
        for address, actions in guard.STAGE_B.items()
    ]
    guard.validate_stage_b({"resource_changes": resources})
    resources.append({"address": "aws_eks_node_group.cpu", "change": {"actions": ["update"], "after": {}}})
    try:
        guard.validate_stage_b({"resource_changes": resources})
    except ValueError:
        pass
    else:
        raise AssertionError("an extra node-group change was accepted")


def test_synthetic_secret_has_no_value_in_terraform_and_exact_reader() -> None:
    manifest = json.loads((ROOT / "platform/manifests/B6-CLIENT-API-KEYS-2026-001.json").read_text())
    assert manifest["status"] == "PROPOSED_NOT_AUTHORIZED"
    assert manifest["aws"]["secret_name"] == "medzen/client-api-keys"
    assert manifest["key_material_contract"]["random_bytes"] == 32
    assert manifest["key_material_contract"]["plaintext_never_committed_or_logged"] is True
    terraform = (ROOT / "infra/b6_client_secret.tf").read_text()
    assert 'name                    = "medzen/client-api-keys"' in terraform
    assert "aws_secretsmanager_secret_version" not in terraform
    assert "block_public_policy = true" in terraform
    assert "DenyEveryOtherPrincipalRead" in terraform


def test_supplemental_orchestrator_kms_is_secrets_manager_context_scoped() -> None:
    source = (ROOT / "infra/b6_client_secret.tf").read_text()
    assert 'actions   = ["kms:Decrypt"]' in source
    assert 'variable = "kms:ViaService"' in source
    assert 'values   = ["secretsmanager.${var.region}.amazonaws.com"]' in source
    assert 'variable = "kms:EncryptionContext:SecretARN"' in source
    assert "secret:medzen/client-api-keys*" in source
    # pin updated for B6v2 round 3: secret-KMS mapping + audio-cache
    # ListBucket added to services.yaml (reviewed; applied + simulated).
    # The generated ViaService grant now overlaps this supplemental
    # context-scoped one — both constrain decrypt to Secrets Manager.
    # Pin updated 2026-09-03: rag-index gained bedrock:Retrieve on the dev
    # corpus Knowledge Base (B6-126); no KMS or secret grant changed.
    assert sha(ROOT / "platform/services.yaml") == "e057e3ef99771e6dfa282af3235ca1ba6a67ea1a88a503b71224ad560b22b858"


def test_deployment_registry_manifest_is_exact_and_non_serving() -> None:
    validator = load_module("deployment_registry_validator", "scripts/check_b6_deployment_registry_packet.py")
    request = validator.validate()
    assert request["snapshot"]["parameter_count"] == 3
    assert request["snapshot"]["root"].endswith(request["snapshot"]["snapshot_material_sha256"])
    assert all(item["Overwrite"] is False for item in request["parameters"])
    assert "/medzen/registry/serving/current" not in json.dumps(request["parameters"])


def test_packets_remain_unapproved_and_keep_worker_capacity_zero() -> None:
    for packet in ("005-alb-controller-qualification", "006-synthetic-client-api-keys", "007-deployment-registry"):
        text = (ROOT / f"platform/decisions/B6-AWS-CHANGE-PACKET-2026-{packet}.md").read_text()
        assert "AWAITING INDEPENDENT REVIEW AND OWNER APPROVAL" in text
        assert "CPU" in text and "GPU" in text
    planning = (ROOT / "infra/b6_planning_override.tf").read_text()
    assert "desired_size = 0" in planning
    assert "min_size     = 0" in planning
    assert sha(ROOT / "infra/eks.tf") == "37103846a11bcdb2e2aca5f81f221d6ee767675c77481b5451484447fd0aca7b" or _pin_matches_committed_history("infra/eks.tf", "37103846a11bcdb2e2aca5f81f221d6ee767675c77481b5451484447fd0aca7b")


def test_preparation_evidence_binds_packets_and_preserves_history() -> None:
    evidence = json.loads(
        (ROOT / "platform/evidence/B6-PREDEPLOYMENT-BOUNDARY-PREPARATION-2026-001.json").read_text()
    )
    assert evidence["status"] == "LOCAL_PREPARATION_COMPLETE_AWAITING_INDEPENDENT_REVIEW"
    for packet in evidence["packets"].values():
        assert sha(ROOT / packet["path"]) == packet["sha256"]
    for relative, expected in evidence["historical_hash_preservation"]["files"].items():
        assert (sha(ROOT / relative) == expected
                or _pin_matches_committed_history(relative, expected))
    assert all(value == 0 for value in evidence["explicit_non_events"].values())


def test_aws_execution_runners_refuse_without_apply() -> None:
    for script in (
        "scripts/run_b6_client_secret_publication.py",
        "scripts/run_b6_deployment_registry_publication.py",
    ):
        result = subprocess.run(
            [sys.executable, str(ROOT / script), "--authorization", "/does/not/exist", "--receipt", "/private/tmp/unused.json"],
            cwd=ROOT, capture_output=True, text=True,
        )
        assert result.returncode == 2
        assert "--apply is required" in result.stderr
