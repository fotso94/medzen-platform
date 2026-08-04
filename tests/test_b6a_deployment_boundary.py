from __future__ import annotations

import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
TREE = "5adf77568813513bc3697a1501ba354c04c7b93ea374fc5407cf4f6402f7431e"


def _policy():
    return json.loads((ROOT / "platform/iam/b6a/medzen-b6a-asr-role.policy.template.json").read_text())


def _objects():
    return list(yaml.safe_load_all((ROOT / "platform/k8s/b6a/asr-platform-proof.template.yaml").read_text()))


def test_b6a_role_can_read_only_the_exact_nonapproved_artifact():
    policy = _policy()
    allows = [s for s in policy["Statement"] if s["Effect"] == "Allow"]
    rendered = json.dumps(allows, sort_keys=True)
    assert f"b6a/asr/v0/{TREE}" in rendered
    assert "approved/asr" not in rendered
    actions = {a for statement in allows for a in (
        [statement["Action"]] if isinstance(statement["Action"], str)
        else statement["Action"])}
    assert actions == {"s3:ListBucket", "s3:GetObject", "kms:Decrypt", "kms:DescribeKey"}


def test_b6a_role_denies_mutation_and_approved_model_reads():
    denies = [s for s in _policy()["Statement"] if s["Effect"] == "Deny"]
    rendered = json.dumps(denies, sort_keys=True)
    for action in ("s3:PutObject", "s3:DeleteObject", "s3:RestoreObject"):
        assert action in rendered
    assert "approved/asr/*" in rendered
    assert "asr-runtime-b6a" in rendered


def test_b6a_role_fails_closed_on_either_identity_tag_mismatch():
    denies = {
        statement["Sid"]: statement
        for statement in _policy()["Statement"]
        if statement["Effect"] == "Deny"
    }
    assert denies["ExactB6ANamespaceOnly"]["Condition"] == {
        "StringNotEquals": {"aws:PrincipalTag/kubernetes-namespace": "medzen"}
    }
    assert denies["ExactB6AServiceAccountOnly"]["Condition"] == {
        "StringNotEquals": {
            "aws:PrincipalTag/kubernetes-service-account": "asr-runtime-b6a"
        }
    }


def test_b6a_manifest_is_internal_gpu_only_and_fail_closed_before_push():
    objects = _objects()
    by_kind = {item["kind"]: item for item in objects}
    assert by_kind["Service"]["spec"]["type"] == "ClusterIP"
    assert by_kind["NetworkPolicy"]["spec"]["ingress"] == []
    deployment = by_kind["Deployment"]["spec"]["template"]["spec"]
    assert deployment["serviceAccountName"] == "asr-runtime-b6a"
    assert deployment["nodeSelector"] == {"workload": "gpu"}
    assert deployment["resourceClaims"] == [{
        "name": "gpu",
        "resourceClaimTemplateName": "asr-runtime-b6a-gpu",
    }]
    for container in deployment["initContainers"] + deployment["containers"]:
        assert container["resources"]["claims"] == [{"name": "gpu"}]
        assert "nvidia.com/gpu" not in container["resources"].get("limits", {})
        assert container["securityContext"]["readOnlyRootFilesystem"] is True
        assert "OWNER_APPROVAL_REQUIRED_ECR_DIGEST" in container["image"]
    assert not any(item["kind"] in {"Ingress", "Gateway"} for item in objects)


def test_b6a_dra_claim_is_one_gpu_shared_by_loader_and_runtime():
    claim = next(
        item for item in _objects() if item["kind"] == "ResourceClaimTemplate"
    )
    assert claim["apiVersion"] == "resource.k8s.io/v1"
    request = claim["spec"]["spec"]["devices"]["requests"]
    assert request == [{
        "name": "gpu",
        "exactly": {"deviceClassName": "gpu.nvidia.com", "count": 1},
    }]


def test_b6a_config_binds_exact_manifest_and_never_uses_production_registry():
    config = next(item for item in _objects() if item["kind"] == "ConfigMap")["data"]
    assert f"/b6a/asr/v0/{TREE}/MANIFEST.json" in config["MODEL_MANIFEST_S3_URI"]
    assert config["MODEL_MANIFEST_SHA256"] == "c64978f4f231516caa2387ab4ccee569ddf4a0f3b98049278c0afe4c307fd850"
    assert "/approved/" not in json.dumps(config)
    assert "/medzen/registry" not in json.dumps(config)
