from __future__ import annotations

from pathlib import Path
import sys

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.lock_b6a_003c_b_dra_render import (
    DRARenderRefusal,
    LOCKED_IMAGE,
    TAGGED_IMAGE,
    lock_render,
)
from scripts.render_b6a_003c_b import (
    ASR_RUNTIME_IMAGE,
    MODEL_LOADER_IMAGE,
    RenderRefusal,
    render,
)


def test_workload_render_pins_scanned_children_and_preserves_private_boundary():
    raw = (ROOT / "platform/k8s/b6a/asr-platform-proof.template.yaml").read_bytes()
    rendered = render(raw)
    documents = [item for item in yaml.safe_load_all(rendered) if item]
    deployment = next(item for item in documents if item["kind"] == "Deployment")
    pod = deployment["spec"]["template"]["spec"]
    assert pod["initContainers"][0]["image"] == MODEL_LOADER_IMAGE
    assert pod["containers"][0]["image"] == ASR_RUNTIME_IMAGE
    assert "OWNER_APPROVAL_REQUIRED" not in rendered.decode()
    service = next(item for item in documents if item["kind"] == "Service")
    assert service["spec"]["type"] == "ClusterIP"
    assert not any(item["kind"] in {"Ingress", "Gateway"} for item in documents)
    committed = ROOT / "platform/k8s/b6a/asr-platform-proof-003c-b.rendered.yaml"
    assert rendered == committed.read_bytes()


def test_workload_render_fails_closed_on_ambiguous_source_template():
    raw = (ROOT / "platform/k8s/b6a/asr-platform-proof.template.yaml").read_text()
    raw = raw.replace("medzen-model-loader", "unexpected-loader", 1)
    with pytest.raises(RenderRefusal, match="placeholder"):
        render(raw.encode())


def _dra_render(*, compute=False, image=TAGGED_IMAGE):
    service_account = "dra-kubelet"
    objects = [
        {
            "apiVersion": "resource.k8s.io/v1",
            "kind": "DeviceClass",
            "metadata": {"name": "gpu.nvidia.com"},
            "spec": {},
        },
        {
            "apiVersion": "apps/v1",
            "kind": "DaemonSet",
            "metadata": {"name": "dra-driver-nvidia-gpu-kubelet-plugin"},
            "spec": {"template": {"spec": {
                "serviceAccountName": service_account,
                "nodeSelector": {"workload": "gpu"},
                "initContainers": [{"name": "init-container", "image": image}],
                "containers": [{
                    "name": "gpus",
                    "image": image,
                    "env": [{"name": "IMAGE_NAME", "value": image}],
                }],
            }}},
        },
        {
            "apiVersion": "admissionregistration.k8s.io/v1",
            "kind": "ValidatingAdmissionPolicy",
            "metadata": {"name": "dra-policy"},
            "spec": {"matchConditions": [{
                "name": "isRestrictedUser",
                "expression": "request.userInfo.username == \"wrong-account\"",
            }]},
        },
        {
            "apiVersion": "admissionregistration.k8s.io/v1",
            "kind": "ValidatingAdmissionPolicyBinding",
            "metadata": {"name": "dra-policy"},
            "spec": {"policyName": "dra-policy"},
        },
    ]
    if compute:
        objects.append({
            "apiVersion": "apps/v1", "kind": "Deployment",
            "metadata": {"name": "dra-driver-nvidia-gpu-compute-domain-controller"},
            "spec": {},
        })
    return yaml.safe_dump_all(objects).encode()


def test_dra_render_locks_every_runtime_and_environment_reference():
    locked = lock_render(_dra_render())
    assert TAGGED_IMAGE not in locked.decode()
    assert locked.decode().count(LOCKED_IMAGE) == 3
    documents = [item for item in yaml.safe_load_all(locked) if item]
    assert {item["kind"] for item in documents} == {
        "Namespace", "DeviceClass", "DaemonSet", "ValidatingAdmissionPolicy",
        "ValidatingAdmissionPolicyBinding",
    }
    policy = next(item for item in documents if item["kind"] == "ValidatingAdmissionPolicy")
    assert policy["spec"]["matchConditions"][0]["expression"] == (
        'request.userInfo.username == "system:serviceaccount:nvidia-dra-driver:dra-kubelet"'
    )


def test_dra_render_rejects_compute_domains_or_unexpected_image():
    with pytest.raises(DRARenderRefusal, match="compute-domain"):
        lock_render(_dra_render(compute=True))
    with pytest.raises(DRARenderRefusal, match="not all present"):
        lock_render(_dra_render(image="example.invalid/image:latest"))


def test_dra_values_explicitly_disable_compute_domains_and_tags_are_not_final():
    values = yaml.safe_load(
        (ROOT / "platform/k8s/b6a/nvidia-dra-003c-b.values.yaml").read_text()
    )
    assert values["resources"] == {
        "gpus": {"enabled": True}, "computeDomains": {"enabled": False}
    }
    assert values["gpuResourcesEnabledOverride"] is True
    assert values["resourceApiVersion"] == "resource.k8s.io/v1"
    assert values["kubeletPlugin"]["nodeSelector"] == {"workload": "gpu"}
    assert values["kubeletPlugin"]["affinity"] is None


def test_committed_dra_render_contains_only_locked_gpu_component():
    path = ROOT / "platform/k8s/b6a/nvidia-dra-003c-b.locked.yaml"
    text = path.read_text()
    documents = [item for item in yaml.safe_load_all(text) if item]
    assert TAGGED_IMAGE not in text
    assert set(image for item in documents for image in _all_images(item)) == {LOCKED_IMAGE}
    assert [item["metadata"]["name"] for item in documents if item["kind"] == "DeviceClass"] == [
        "gpu.nvidia.com"
    ]
    assert not any(item["kind"] == "Deployment" for item in documents)
    assert "compute-domain" not in text
    daemonset = next(item for item in documents if item["kind"] == "DaemonSet")
    service_account = daemonset["spec"]["template"]["spec"]["serviceAccountName"]
    policy = next(item for item in documents if item["kind"] == "ValidatingAdmissionPolicy")
    assert policy["spec"]["matchConditions"][0]["expression"] == (
        "request.userInfo.username == "
        f'"system:serviceaccount:nvidia-dra-driver:{service_account}"'
    )


def _all_images(value):
    found = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "image" and isinstance(child, str):
                found.append(child)
            found.extend(_all_images(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_all_images(child))
    return found
