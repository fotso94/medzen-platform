"""Boundary fakes for rehearsing the real ASR pilot stage implementation.

This module deliberately contains no implementation of any pilot stage.  The
cold rehearsal constructs :class:`LiveOperations` itself and injects these
objects only where that class crosses an AWS or kubectl/external-tool
boundary.  Therefore a stage return-value, state-ordering or cleanup defect in
``LiveOperations`` cannot be hidden by a second implementation.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any


from pipeline.asr_base_model_pilot_receipts import canonical_json
from scripts.asr_base_model_aws_read_fixtures import FixtureCatalog
from scripts.asr_base_model_pilot_live import (
    CALLER,
    CPU_NODEGROUP,
    GPU_NODEGROUP,
    LiveOperations,
)
from scripts.asr_base_model_async_observations import (
    NETWORK_AND_LISTENER_PRESENT,
    NETWORK_RECEIPT_ABSENT,
)
from scripts.asr_base_model_pilot_runner import AttemptContext, OperationRefusal


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests/fixtures/asr_base_model_pilot"
MANIFEST_ARCHIVE = FIXTURE_ROOT / "eval-manifests-2026-08-11.tar.gz"
PILOT_BUNDLE = FIXTURE_ROOT / "pilot-bundle-2026-001.json"
RUNTIME_ROWS = FIXTURE_ROOT / "runtime-rows-2026-001.json"
MODEL_BINDINGS = FIXTURE_ROOT / "model-bindings-2026-001.json"
GPU_NODE_READINESS_FIXTURE = (
    ROOT
    / "platform/evidence/ASR-BASE-MODEL-GPU-NODE-READINESS-FIXTURE-CAPTURE-2026-001.json"
)
GPU_NODE_READINESS_FIXTURE_SHA256 = (
    "34663d3ae7218f9423d15b4fa9aa11f4f4940022deaf87a409e6c0f4c91e5e56"
)
DEFAULT_GPU_NODE_READINESS_FIXTURE = {
    "path": str(GPU_NODE_READINESS_FIXTURE.relative_to(ROOT)),
    "sha256": GPU_NODE_READINESS_FIXTURE_SHA256,
}
DEFAULT_AWS_READ_FIXTURES = {
    "path": "platform/evidence/ASR-BASE-MODEL-AWS-READ-FIXTURE-CAPTURE-2026-001.json",
    "sha256": "e423ec4ba4f41e27a464a4a9d84a72d83cabe50184de08dafa8018dbecd4cfc0",
}


class MissingObject(Exception):
    def __init__(self, response: dict[str, Any]):
        self.response = copy.deepcopy(response)


class _Body:
    def __init__(self, value: bytes):
        self._stream = io.BytesIO(value)

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)


class BoundarySession:
    region_name = "eu-central-1"

    def __init__(
        self,
        bindings: dict[str, Any],
        injection: str | None,
        *,
        root: Path = ROOT,
    ):
        self.state = BoundaryState(bindings, injection, root=root)
        self.clients = {
            "sts": StsBoundary(self.state),
            "eks": EksBoundary(self.state),
            "ec2": Ec2Boundary(self.state),
            "ecr": EcrBoundary(self.state),
            "s3": S3Boundary(self.state),
            "autoscaling": AutoscalingBoundary(self.state),
            "ssm": SsmBoundary(self.state),
        }

    def client(self, name: str) -> Any:
        return self.clients[name]


class BoundaryState:
    def __init__(
        self,
        bindings: dict[str, Any],
        injection: str | None,
        *,
        root: Path = ROOT,
    ):
        self.root = root
        self.bindings = bindings
        self.injection = injection
        self.fixtures = FixtureCatalog(
            ROOT, bindings.get("aws_read_fixtures", DEFAULT_AWS_READ_FIXTURES)
        )
        self.aws_calls = 0
        self.aws_mutations = 0
        self.kubectl_calls = 0
        self.kubernetes_mutations = 0
        self.command_calls = 0
        self.deadline_actions: dict[str, dict[str, Any]] = {}
        self.gpu_desired = 0
        self.cpu_desired = 0
        self.instance_id = "i-rehearsal-gpu"
        self.node_name = "ip-rehearsal-gpu"
        self.monotonic_seconds = 0.0
        self.gpu_node_reads = 0
        self.gpu_node_observation_sequence: list[str] = []
        self.pilot_receipt_reads = 0
        self.pilot_receipt_observation_sequence: list[str] = []
        self.pilot_pod_reads = 0
        self.pilot_job_wait_refused = False
        self.last_sampler_command: list[str] | None = None
        self.security_groups: set[str] = set()
        self.endpoints: dict[str, dict[str, Any]] = {}
        self.volumes: set[str] = set()
        self.cni_configuration = "{}"
        self.cni_mode = "standard"
        self.namespaces: set[str] = set()
        self.dra_installed = False
        self.aggregate = self._aggregate()
        self.ssm_commands: dict[str, dict[str, Any]] = {}
        self.ssm_counter = 0
        self.prestage_objects = self._prestage_objects()
        self.prestage_downloads = {
            self._proof()["pilot_bundle"]["object"]["key"]: PILOT_BUNDLE.read_bytes(),
            next(
                item["key"]
                for item in self._proof()["objects"]
                if item["key"].endswith("runtime-rows.json")
            ): RUNTIME_ROWS.read_bytes(),
            next(
                item["key"]
                for item in self._proof()["objects"]
                if item["key"].endswith("model-bindings.json")
            ): MODEL_BINDINGS.read_bytes(),
        }

    def sleep(self, seconds: float) -> None:
        self.monotonic_seconds += seconds

    def monotonic(self) -> float:
        return self.monotonic_seconds

    def _gpu_node_fixture(self) -> dict[str, Any]:
        binding = self.bindings.get(
            "gpu_node_readiness_fixtures", DEFAULT_GPU_NODE_READINESS_FIXTURE
        )
        path = self.root / binding["path"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != binding["sha256"]:
            raise AssertionError("GPU-node readiness fixture-capture hash differs")
        fixture = json.loads(path.read_bytes())
        if fixture.get("status") != "PASS_READ_ONLY_LIVE_GPU_NODE_TRANSITION_CAPTURE":
            raise AssertionError("GPU-node readiness fixture-capture status differs")
        return fixture

    def gpu_storage_nodegroup_response(self) -> dict[str, Any]:
        """Replay the exact post-replacement DescribeNodegroup response."""
        binding = self.bindings.get("gpu_storage_policy", {}).get("live_fixture")
        if not isinstance(binding, dict):
            raise AssertionError("GPU storage live-fixture binding is absent")
        relative = binding.get("path")
        expected = binding.get("sha256")
        if not isinstance(relative, str) or not isinstance(expected, str):
            raise AssertionError("GPU storage live-fixture binding is malformed")
        path = self.root / relative
        body = path.read_bytes()
        if hashlib.sha256(body).hexdigest() != expected:
            raise AssertionError("GPU storage live-fixture hash differs")
        response = json.loads(body)
        if not isinstance(response, dict) or not isinstance(
            response.get("nodegroup"), dict
        ):
            raise AssertionError("GPU storage live fixture is malformed")
        return copy.deepcopy(response)

    def _captured_gpu_node_list(self, state: str) -> dict[str, Any]:
        fixture = self._gpu_node_fixture()
        empty = copy.deepcopy(fixture["attempt_11_empty_list"]["response"])
        if state == "empty":
            return empty
        if state not in {"not_ready", "ready"}:
            raise AssertionError(f"unknown captured GPU-node state: {state}")
        empty["items"] = [
            copy.deepcopy(fixture["attempt_11_node_objects"][state]["node"])
        ]
        return empty

    def gpu_node_response(self) -> dict[str, Any]:
        self.gpu_node_reads += 1
        if self.injection == "gpu_node_never_ready":
            observed = "empty" if self.gpu_node_reads == 1 else "not_ready"
        elif self.injection == "gpu_node_delayed_ready":
            observed = ("empty", "not_ready", "ready", "ready")[
                min(self.gpu_node_reads - 1, 3)
            ]
        else:
            observed = "ready"
        self.gpu_node_observation_sequence.append(
            f"CAPTURED_ATTEMPT_11_{observed.upper()}"
        )
        response = self._captured_gpu_node_list(observed)
        if response.get("items"):
            self.node_name = response["items"][0]["metadata"]["name"]
        return response

    @staticmethod
    def _proof() -> dict[str, Any]:
        return json.loads(
            (ROOT / "platform/evidence/ASR-BASE-MODEL-PRESTAGE-PROOF-2026-001.json").read_bytes()
        )

    def _prestage_objects(self) -> dict[tuple[str, str], dict[str, Any]]:
        values = {}
        for item in self._proof()["objects"]:
            values[(item["key"], item["version_id"])] = {
                "ContentLength": item["bytes"],
                "Metadata": {"sha256": item["sha256"]},
                "ChecksumSHA256": item["s3_checksum_sha256"],
                "ChecksumType": item["checksum_type"],
                "ServerSideEncryption": "aws:kms",
                "VersionId": item["version_id"],
            }
        return values

    def _aggregate(self) -> dict[str, Any]:
        rows = json.loads(RUNTIME_ROWS.read_bytes())["rows"]
        conditioning = json.loads(
            (ROOT / "services/asr-eval-runtime/assets/language-conditioning-v1.json").read_bytes()
        )["languages"]
        conditioned = sum(
            int(conditioning[row["language"]][provider] is not None)
            for row in rows
            for provider in ("whisper", "meta_llm")
        )
        return {
            "status": "PASS_AGGREGATE",
            "runtime_rows": len(rows),
            "completed_inferences": len(rows) * 3 + conditioned,
            "not_applicable": len(rows) * 2 - conditioned,
            "aggregate": {
                "groups": {"synthetic|unconditioned": {"wer": 0.5}},
                "gpu_memory": {
                    "unit": "MiB",
                    "sample_count": 120,
                    "baseline": 100.0,
                    "peak": 125.0,
                },
            },
        }

    def call(self) -> None:
        self.aws_calls += 1

    def mutate(self) -> None:
        self.aws_calls += 1
        self.aws_mutations += 1

    def zero_state(self) -> bool:
        return (
            not self.deadline_actions
            and self.gpu_desired == 0
            and self.cpu_desired == 0
            and not self.security_groups
            and not self.endpoints
            and not self.volumes
            and not self.namespaces
            and not self.dra_installed
            and self.cni_configuration == "{}"
            and self.cni_mode == "standard"
        )


class StsBoundary:
    def __init__(self, state: BoundaryState): self.state = state

    def get_caller_identity(self) -> dict[str, str]:
        self.state.call()
        if self.state.injection == "deadline_identity_and_acceptance":
            raise OperationRefusal("INJECTED_DEADLINE_IDENTITY", "injected deadline refusal")
        return self.state.fixtures.payload("sts-get-caller-identity")


class EksBoundary:
    def __init__(self, state: BoundaryState): self.state = state

    def describe_nodegroup(self, *, clusterName: str, nodegroupName: str) -> dict[str, Any]:
        self.state.call()
        desired = self.state.gpu_desired if nodegroupName == GPU_NODEGROUP else self.state.cpu_desired
        if (
            nodegroupName == GPU_NODEGROUP
            and isinstance(self.state.bindings.get("gpu_storage_policy"), dict)
        ):
            response = self.state.gpu_storage_nodegroup_response()
            response["nodegroup"]["scalingConfig"]["desiredSize"] = desired
            response["nodegroup"]["status"] = "ACTIVE"
            response["nodegroup"]["resources"]["autoScalingGroups"][0]["name"] = (
                self.state.bindings["aws"]["gpu_asg_name"]
            )
            if self.state.injection == "gpu_storage_below_floor":
                response["nodegroup"]["diskSize"] = 20
            return response
        name = "eks-describe-nodegroup-gpu" if nodegroupName == GPU_NODEGROUP else "eks-describe-nodegroup-cpu"
        return self.state.fixtures.replay(
            name,
            {
                "nodegroup.scalingConfig.desiredSize": desired,
                "nodegroup.status": "ACTIVE",
                "nodegroup.resources.autoScalingGroups.0.name": (
                    "eks-gpu-b8cfd795-fa28-70a1-b844-258a0f0adc26"
                    if nodegroupName == GPU_NODEGROUP
                    else "eks-cpu-32cfd795-fa28-d1d9-1b8c-2ed678be1772"
                ),
            },
        )

    def update_nodegroup_config(self, *, nodegroupName: str, scalingConfig: dict[str, int], **_: Any) -> dict[str, Any]:
        self.state.mutate()
        if nodegroupName == GPU_NODEGROUP:
            self.state.gpu_desired = scalingConfig["desiredSize"]
        return {"update": {"status": "Successful"}}

    def describe_addon(self, **_: Any) -> dict[str, Any]:
        self.state.call()
        return self.state.fixtures.replay(
            "eks-describe-addon-vpc-cni",
            {"addon.configurationValues": self.state.cni_configuration, "addon.status": "ACTIVE"},
        )

    def update_addon(self, *, configurationValues: str, **_: Any) -> dict[str, Any]:
        self.state.mutate()
        self.state.cni_configuration = configurationValues
        return {"update": {"status": "Successful"}}


class AutoscalingBoundary:
    def __init__(self, state: BoundaryState): self.state = state

    def put_scheduled_update_group_action(self, *, ScheduledActionName: str, DesiredCapacity: int, **_: Any) -> None:
        self.state.mutate()
        self.state.deadline_actions[ScheduledActionName] = {"ScheduledActionName": ScheduledActionName, "DesiredCapacity": DesiredCapacity}

    def describe_scheduled_actions(self, *, ScheduledActionNames: list[str] | None = None, **_: Any) -> dict[str, Any]:
        self.state.call()
        actions = list(self.state.deadline_actions.values())
        if ScheduledActionNames:
            actions = [item for item in actions if item["ScheduledActionName"] in ScheduledActionNames]
        return self.state.fixtures.replay(
            "autoscaling-describe-scheduled-actions-empty",
            {"ScheduledUpdateGroupActions": copy.deepcopy(actions)},
        )

    def delete_scheduled_action(self, *, ScheduledActionName: str, **_: Any) -> None:
        self.state.mutate()
        self.state.deadline_actions.pop(ScheduledActionName, None)
        if self.state.injection == "cleanup_and_expiry":
            raise OperationRefusal(
                "INJECTED_CLEANUP_RECEIPT_REFUSAL",
                "injected cleanup refusal after deadline removal",
            )

    def describe_auto_scaling_groups(self, **_: Any) -> dict[str, Any]:
        self.state.call()
        instances = [] if self.state.gpu_desired == 0 else [{"InstanceId": self.state.instance_id}]
        return self.state.fixtures.replay(
            "autoscaling-describe-gpu-group",
            {
                "AutoScalingGroups.0.DesiredCapacity": self.state.gpu_desired,
                "AutoScalingGroups.0.Instances": instances,
            },
        )


class Ec2Boundary:
    def __init__(self, state: BoundaryState): self.state = state

    def describe_vpc_endpoints(self, *, VpcEndpointIds: list[str] | None = None, Filters: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        self.state.call()
        values = list(self.state.endpoints.values())
        if VpcEndpointIds is not None:
            values = [value for value in values if value["VpcEndpointId"] in VpcEndpointIds]
        if Filters and any(item["Name"] == "vpc-endpoint-id" for item in Filters):
            requested = next(item["Values"] for item in Filters if item["Name"] == "vpc-endpoint-id")
            values = [value for value in values if value["VpcEndpointId"] in requested]
        if not values:
            return self.state.fixtures.payload("ec2-describe-eval-vpc-endpoints-empty")
        return {"VpcEndpoints": copy.deepcopy(values)}

    def create_security_group(self, **_: Any) -> dict[str, str]:
        self.state.mutate()
        value = "sg-rehearsal-endpoint"
        self.state.security_groups.add(value)
        return {"GroupId": value}

    def revoke_security_group_egress(self, **_: Any) -> None: self.state.mutate()
    def authorize_security_group_ingress(self, **_: Any) -> None: self.state.mutate()

    def create_vpc_endpoint(self, *, VpcEndpointType: str, ServiceName: str, **_: Any) -> dict[str, Any]:
        policy_document = _.get("PolicyDocument")
        if not isinstance(policy_document, str):
            raise AssertionError("endpoint policy document is absent")
        policy = json.loads(policy_document)
        actions = {
            action
            for statement in policy.get("Statement", [])
            for action in (
                statement.get("Action", [])
                if isinstance(statement.get("Action", []), list)
                else [statement.get("Action")]
            )
        }
        if VpcEndpointType == "Gateway":
            required = {"s3:GetObject", "s3:GetObjectVersion"}
        else:
            required = {
                "ecr:GetAuthorizationToken",
                "ecr:BatchGetImage",
                "ecr:GetDownloadUrlForLayer",
                "ecr:BatchCheckLayerAvailability",
            }
        if not required.issubset(actions):
            raise OperationRefusal(
                "ENDPOINT_POLICY_CALL_UNCOVERED",
                "rehearsal endpoint policy omits an inventory-derived action",
                outcome="BLOCKED_NETWORK_ISOLATION",
            )
        self.state.mutate()
        if self.state.injection == "private_endpoint_and_policy_gate":
            raise OperationRefusal(
                "INJECTED_PRIVATE_ENDPOINT_REFUSAL",
                "injected private endpoint refusal",
                outcome="BLOCKED_NETWORK_ISOLATION",
            )
        ordinal = len(self.state.endpoints) + 1
        if VpcEndpointType == "Interface":
            payload = self.state.fixtures.replay(
                "ec2-describe-vpc-endpoint-interface-template",
                {
                    "VpcEndpoints.0.VpcEndpointId": f"vpce-rehearsal-{ordinal}",
                    "VpcEndpoints.0.ServiceName": ServiceName,
                    "VpcEndpoints.0.NetworkInterfaceIds": [
                        f"eni-rehearsal-{ordinal}-{az}" for az in range(1, 4)
                    ],
                    "VpcEndpoints.0.Groups.0.GroupId": "sg-rehearsal-endpoint",
                    "VpcEndpoints.0.Groups.0.GroupName": "medzen-asr-eval-vpce",
                    "VpcEndpoints.0.PolicyDocument": "{}",
                    "VpcEndpoints.0.PrivateDnsEnabled": True,
                    "VpcEndpoints.0.Tags": [{"Key": "MedZenPurpose", "Value": "asr-base-model-eval"}],
                },
            )
        else:
            payload = self.state.fixtures.replay(
                "ec2-describe-vpc-endpoint-gateway-template",
                {
                    "VpcEndpoints.0.VpcEndpointId": f"vpce-rehearsal-{ordinal}",
                    "VpcEndpoints.0.ServiceName": ServiceName,
                    "VpcEndpoints.0.RouteTableIds": ["rtb-0c6eb6874ce0565dc"],
                    "VpcEndpoints.0.PolicyDocument": "{}",
                    "VpcEndpoints.0.Tags": [{"Key": "MedZenPurpose", "Value": "asr-base-model-eval"}],
                    "VpcEndpoints.0.VpcId": "vpc-051aa9df8b64bf141",
                    "VpcEndpoints.0.OwnerId": "558069890522",
                    "VpcEndpoints.0.ServiceRegion": "eu-central-1",
                },
            )
        value = payload["VpcEndpoints"][0]
        self.state.endpoints[value["VpcEndpointId"]] = value
        return {"VpcEndpoint": copy.deepcopy(value)}

    def describe_network_interfaces(self, *, NetworkInterfaceIds: list[str]) -> dict[str, Any]:
        self.state.call()
        replacements = {}
        for index, identifier in enumerate(NetworkInterfaceIds):
            replacements[f"NetworkInterfaces.{index}.NetworkInterfaceId"] = identifier
            replacements[f"NetworkInterfaces.{index}.PrivateIpAddress"] = f"10.0.1.{index + 7}"
        replayed = self.state.fixtures.replay(
            "ec2-describe-network-interfaces-template", replacements
        )
        replayed["NetworkInterfaces"] = replayed["NetworkInterfaces"][: len(NetworkInterfaceIds)]
        return replayed

    def describe_prefix_lists(self, **_: Any) -> dict[str, Any]:
        self.state.call()
        return self.state.fixtures.payload("ec2-describe-prefix-lists-s3")

    def get_managed_prefix_list_entries(self, **_: Any) -> dict[str, Any]:
        self.state.call()
        return self.state.fixtures.payload("ec2-get-managed-prefix-list-entries-s3")

    def describe_instances(self, **_: Any) -> dict[str, Any]:
        self.state.call()
        return self.state.fixtures.replay(
            "ec2-describe-instance-template",
            {
                "Reservations.0.Instances.0.InstanceId": self.state.instance_id,
                "Reservations.0.Instances.0.Placement.AvailabilityZone": "eu-central-1a",
            },
        )

    def create_volume(self, **_: Any) -> dict[str, str]:
        self.state.mutate()
        value = "vol-rehearsal"
        self.state.volumes.add(value)
        return {"VolumeId": value}

    def get_waiter(self, _: str) -> Any:
        state = self.state

        def wait(**__: Any) -> None:
            state.call()
            if state.volumes:
                state.fixtures.payload("ec2-describe-volume-template")
            else:
                state.fixtures.payload("ec2-describe-eval-volumes-empty")

        return SimpleNamespace(wait=wait)

    def attach_volume(self, **_: Any) -> None: self.state.mutate()
    def detach_volume(self, **_: Any) -> None: self.state.mutate()

    def delete_volume(self, *, VolumeId: str) -> None:
        self.state.mutate()
        self.state.volumes.discard(VolumeId)

    def delete_vpc_endpoints(self, *, VpcEndpointIds: list[str]) -> None:
        self.state.mutate()
        for value in VpcEndpointIds:
            self.state.endpoints.pop(value, None)

    def delete_security_group(self, *, GroupId: str) -> None:
        self.state.mutate()
        self.state.security_groups.discard(GroupId)

    def describe_volumes(self, **_: Any) -> dict[str, Any]:
        self.state.call()
        if not self.state.volumes:
            return self.state.fixtures.payload("ec2-describe-eval-volumes-empty")
        values = []
        for value in sorted(self.state.volumes):
            replayed = self.state.fixtures.replay(
                "ec2-describe-volume-template",
                {
                    "Volumes.0.VolumeId": value,
                    "Volumes.0.AvailabilityZone": "eu-central-1a",
                    "Volumes.0.State": "available",
                    "Volumes.0.Size": 60,
                    "Volumes.0.Attachments": [],
                },
            )
            values.append(replayed["Volumes"][0])
        return {"Volumes": values}


class EcrBoundary:
    class _Exceptions:
        class RepositoryNotFoundException(Exception): pass

    exceptions = _Exceptions()

    def __init__(self, state: BoundaryState):
        self.state = state

    def describe_repositories(self, **_: Any) -> dict[str, Any]:
        self.state.call()
        return self.state.fixtures.payload("ecr-describe-repository")

    def batch_get_image(self, *, imageIds: list[dict[str, str]], **_: Any) -> dict[str, Any]:
        self.state.call()
        image = self.state.bindings["image"]
        requested = imageIds[0]
        if requested.get("imageTag") == image["tag"]:
            return self.state.fixtures.payload("ecr-batch-get-index-by-tag")
        names = {
            image["oci_index_digest"]: "ecr-batch-get-index-by-digest",
            image["linux_amd64_digest"]: "ecr-batch-get-child",
            image["attestation_digest"]: "ecr-batch-get-attestation",
        }
        if requested.get("imageDigest") in names:
            return self.state.fixtures.payload(names[requested["imageDigest"]])
        raise AssertionError("unrecorded ECR BatchGetImage response requested")

    def get_registry_scanning_configuration(self) -> dict[str, Any]:
        self.state.call()
        return self.state.fixtures.payload("ecr-get-registry-scanning-configuration")

    def describe_image_scan_findings(self, **_: Any) -> dict[str, Any]:
        self.state.call()
        return self.state.fixtures.payload("ecr-describe-image-scan-findings")

    def batch_check_layer_availability(self, **_: Any) -> dict[str, Any]:
        self.state.call()
        return self.state.fixtures.payload("ecr-batch-check-layer-availability")

    def get_download_url_for_layer(self, *, layerDigest: str, **_: Any) -> dict[str, Any]:
        self.state.call()
        return self.state.fixtures.replay(
            "ecr-get-download-url-template",
            {"downloadUrl": "https://ecr.rehearsal.invalid/exact-layer", "layerDigest": layerDigest},
        )


class S3Boundary:
    def __init__(self, state: BoundaryState): self.state = state

    def head_object(self, *, Key: str, VersionId: str, **_: Any) -> dict[str, Any]:
        self.state.call()
        if self.state.injection == "prestage_object_absent" and Key == self.state._proof()["objects"][0]["key"]:
            raise MissingObject(self.state.fixtures.payload("s3-head-object-not-found"))
        value = self.state.prestage_objects.get((Key, VersionId))
        if value is None:
            raise MissingObject(self.state.fixtures.payload("s3-head-object-not-found"))
        ordinal = next(
            index
            for index, item in enumerate(self.state._proof()["objects"], 1)
            if item["key"] == Key and item["version_id"] == VersionId
        )
        captured = self.state.fixtures.payload(f"s3-head-prestage-{ordinal:02d}")
        # The proof values must equal the separately captured real HeadObject.
        for field in ("ContentLength", "ChecksumSHA256", "ChecksumType", "ServerSideEncryption", "VersionId"):
            if captured.get(field) != value.get(field):
                raise AssertionError(f"prestage HeadObject fixture differs: {field}")
        if captured.get("Metadata", {}).get("sha256") != value.get("Metadata", {}).get("sha256"):
            raise AssertionError("prestage HeadObject metadata fixture differs")
        return captured

    def download_fileobj(self, _: str, key: str, stream: Any, ExtraArgs: dict[str, Any] | None = None) -> None:
        self.state.call()
        capture = (
            "s3-get-model-bindings"
            if key.endswith("/model-bindings.json")
            else "s3-get-pilot-bundle"
        )
        self.state.fixtures.payload(capture)
        body = self.state.prestage_downloads.get(key)
        if body is None:
            raise MissingObject(self.state.fixtures.payload("s3-head-object-not-found"))
        stream.write(body)

    def generate_presigned_url(self, *_: Any, Params: dict[str, Any], **__: Any) -> str:
        self.state.call()
        return f"https://s3.rehearsal.invalid/{Params['Key']}?versionId={Params.get('VersionId', '')}"


class SsmBoundary:
    class _Exceptions:
        class InvocationDoesNotExist(Exception): pass

    exceptions = _Exceptions()

    def __init__(self, state: BoundaryState): self.state = state

    def send_command(self, **kwargs: Any) -> dict[str, Any]:
        self.state.mutate()
        self.state.ssm_counter += 1
        command_id = f"command-rehearsal-{self.state.ssm_counter}"
        commands = kwargs.get("Parameters", {}).get("commands", [])
        stdout = canonical_json(self.state.aggregate).decode() if any("aggregate.json" in command and command.startswith("cat ") for command in commands) else ""
        self.state.ssm_commands[command_id] = {"Status": "Success", "StandardOutputContent": stdout}
        return {"Command": {"CommandId": command_id}}

    def get_command_invocation(self, *, CommandId: str, **_: Any) -> dict[str, Any]:
        self.state.call()
        body = self.state.ssm_commands[CommandId]
        return self.state.fixtures.replay(
            "ssm-get-command-invocation-template",
            {
                "CommandId": CommandId,
                "InstanceId": self.state.instance_id,
                "Status": body["Status"],
                "StatusDetails": body["Status"],
                "ResponseCode": 0,
                "StandardOutputContent": body.get("StandardOutputContent", ""),
                "StandardErrorContent": "",
            },
        )


class ExternalCommandBoundary:
    """Route only external AWS/kubectl/Docker calls; local Python remains real."""

    def __init__(self, state: BoundaryState): self.state = state

    @staticmethod
    def _completed(command: list[str], *, stdout: bytes = b"", returncode: int = 0) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr=b"")

    def __call__(self, command: list[str], *, cwd: Path | None = None, stdin: bytes | None = None, timeout: int = 900, check: bool = True, journal_path: Path | None = None) -> subprocess.CompletedProcess[bytes]:
        del stdin, check, journal_path
        self.state.command_calls += 1
        executable = Path(command[0]).name
        if executable == "aws":
            self.state.call()
            if "update-kubeconfig" in command:
                self.state.fixtures.payload("eks-describe-cluster")
                kubeconfig = Path(command[command.index("--kubeconfig") + 1])
                kubeconfig.write_text("rehearsal\n", encoding="utf-8")
                return self._completed(command)
            if "sync" in command:
                self.state.fixtures.payload("s3-list-eval-manifests-template")
                self.state.fixtures.payload("s3-get-eval-manifest-template")
                destination = Path(command[command.index("sync") + 2])
                with tarfile.open(MANIFEST_ARCHIVE, "r:gz") as archive:
                    archive.extractall(destination, filter="data")
                return self._completed(command)
            raise AssertionError(f"unhandled rehearsal AWS command: {command}")
        if executable == "kubectl":
            self.state.kubectl_calls += 1
            if any(
                "--query-gpu=index,memory.used,memory.total" in argument
                for argument in command
            ):
                self.state.last_sampler_command = list(command)
                if self.state.injection == "sampler_driver_library_missing":
                    return self._completed(
                        command,
                        stdout=(
                            b"NVIDIA-SMI couldn't find libnvidia-ml.so library "
                            b"in your system.\n" * 120
                        ),
                    )
                return self._completed(command, stdout=(b"0, 100, 23034\n" * 120))
            if "wait" in command:
                if "pod/asr-eval-inbound-control" in command:
                    return self._completed(command)
                if self.state.injection == "pilot_job_refused":
                    self.state.pilot_job_wait_refused = True
                    return self._completed(command, returncode=1)
                return self._completed(command)
            if "delete" in command and "namespace" in command:
                namespace = command[command.index("namespace") + 1]
                self.state.namespaces.discard(namespace)
                if namespace == "nvidia-dra-driver":
                    self.state.dra_installed = False
                self.state.kubernetes_mutations += 1
                return self._completed(command)
            raise AssertionError(f"unhandled rehearsal kubectl command: {command}")
        # Local Python audit is the real executable and operates on the
        # recorded manifest archive. This is intentionally not faked.
        completed = subprocess.run(command, cwd=cwd, capture_output=True, timeout=timeout)
        if completed.returncode != 0:
            raise OperationRefusal("BOUNDED_COMMAND_REFUSED", f"{executable} refused in rehearsal")
        return completed


class KubectlBoundary:
    def __init__(self, state: BoundaryState): self.state = state

    def __call__(self, _: AttemptContext, *args: str, stdin: bytes | None = None, timeout: int = 900, json_output: bool = False) -> dict[str, Any] | bytes:
        del timeout
        self.state.kubectl_calls += 1
        if args[:2] == ("get", "namespaces"):
            return {"items": [{"metadata": {"name": value}} for value in sorted(self.state.namespaces)]}
        if args[:2] == ("get", "daemonset/aws-node"):
            return {"spec": {"template": {"spec": {"containers": [{"name": "aws-node", "env": []}]}}}}
        if args[:2] == ("set", "env"):
            assignment = args[-1]
            self.state.kubernetes_mutations += 1
            self.state.cni_mode = "standard" if assignment.endswith("-") else assignment.split("=", 1)[1]
            return b""
        if args[:2] == ("create", "namespace"):
            self.state.namespaces.add(args[2])
            self.state.kubernetes_mutations += 1
            return b""
        if args[:2] == ("get", "nodes"):
            return self.state.gpu_node_response()
        if args[:2] == ("apply", "-f"):
            self.state.kubernetes_mutations += 1
            body = (stdin or b"").decode(errors="replace")
            if "nvidia-dra-driver" in body:
                self.state.dra_installed = True
                self.state.namespaces.add("nvidia-dra-driver")
            if "medzen-asr-eval" in body:
                self.state.namespaces.add("medzen-asr-eval")
            return b""
        if args[:2] == ("get", "pods") and "nvidia-dra-driver" in args:
            return {"items": [{
                "metadata": {"name": "nvidia-dra-rehearsal", "uid": "dra-rehearsal-uid"},
                "spec": {"nodeName": "gpu-rehearsal", "containers": [{"name": "gpus"}]},
                "status": {
                    "phase": "Running",
                    "conditions": [{"type": "Ready", "status": "False", "reason": "ContainersNotReady"}],
                    "containerStatuses": [{"name": "gpus", "ready": False, "restartCount": 0, "state": {"running": {}}}],
                },
            }]}
        if (
            args[:2] == ("get", "pods")
            and "kube-system" in args
            and "k8s-app=aws-node" in args
        ):
            return {
                "items": [{
                    "metadata": {"name": "aws-node-rehearsal"},
                    "spec": {"nodeName": self.state.node_name},
                }]
            }
        if args[:2] == ("get", "daemonset"):
            return {
                "metadata": {"generation": 1},
                "status": {
                    "observedGeneration": 1,
                    "desiredNumberScheduled": 1,
                    "currentNumberScheduled": 1,
                    "numberReady": 0,
                    "numberAvailable": 0,
                    "numberUnavailable": 1,
                },
            }
        if args[:2] == ("get", "events"):
            return {"items": [{
                "type": "Warning", "reason": "Unhealthy", "count": 1,
                "message": "synthetic DRA readiness probe refusal",
                "involvedObject": {"kind": "Pod", "name": "nvidia-dra-rehearsal"},
            }]}
        if args[:2] == ("get", "deviceclass"):
            return {"metadata": {"name": "gpu.nvidia.com"}}
        if args[:2] == ("get", "resourceslices"):
            return {"items": []}
        if args[:2] == ("get", "pods"):
            return {"items": [{"metadata": {"name": "asr-pilot-rehearsal"}, "status": {"podIP": "10.0.2.21"}}]}
        if args[:2] == ("get", "job"):
            return {
                "metadata": {"name": args[2]},
                "status": {"failed": 1, "conditions": [{"type": "Failed", "status": "True", "reason": "SyntheticWorkloadRefusal"}]},
            }
        if args[:2] == ("get", "pod"):
            self.state.pilot_pod_reads += 1
            if (
                self.state.injection != "network_receipt_pod_terminal"
                and not self.state.pilot_job_wait_refused
            ):
                return {
                    "metadata": {"name": args[2]},
                    "status": {
                        "phase": "Running",
                        "containerStatuses": [{
                            "name": "offline-evaluator",
                            "ready": True,
                            "state": {"running": {}},
                        }],
                    },
                }
            return {
                "metadata": {"name": args[2]},
                "status": {
                    "phase": "Failed",
                    "containerStatuses": [{"name": "offline-evaluator", "state": {"terminated": {"exitCode": 72, "reason": "Error"}}}],
                },
            }
        if args[:2] == ("logs", "-n"):
            if "aws-network-policy-agent" in args:
                return (
                    b"level=info component=network-policy-agent "
                    b"msg=synthetic-policy-converged\n"
                )
            return b""
        if args[:2] == ("delete", "pod/asr-eval-inbound-control"):
            self.state.kubernetes_mutations += 1
            return b""
        if json_output:
            raise AssertionError(f"unhandled rehearsal kubectl JSON call: {args}")
        return b""


class SsmCommandBoundary:
    def __init__(self, state: BoundaryState): self.state = state

    def __call__(self, _: str, commands: list[str], *, timeout_seconds: int = 900) -> dict[str, Any]:
        del timeout_seconds
        self.state.mutate()
        self.state.ssm_counter += 1
        command_id = f"command-rehearsal-{self.state.ssm_counter}"
        if self.state.injection == "node_staging_unknown_user" and any(
            "/usr/sbin/chroot" in command for command in commands
        ):
            return {
                "command_id": command_id,
                "status": "Failed",
                "response_code": 1,
                "stdout_sha256": hashlib.sha256(b"").hexdigest(),
                "stdout": "",
                "stderr": "sudo: unknown user #10001",
            }
        if any(NETWORK_AND_LISTENER_PRESENT in command for command in commands):
            self.state.pilot_receipt_reads += 1
            if self.state.injection == "network_receipt_timeout":
                observed = "ABSENT"
            elif (
                self.state.injection == "network_receipt_delayed"
                and self.state.pilot_receipt_reads <= 2
            ):
                observed = "ABSENT"
            else:
                observed = "READY"
            self.state.pilot_receipt_observation_sequence.append(observed)
            if observed == "ABSENT":
                stdout = f"{NETWORK_RECEIPT_ABSENT}\n"
            else:
                stdout = (
                    f"{NETWORK_AND_LISTENER_PRESENT}\n"
                    + canonical_json({
                        "status": "PASS_NETWORK_ISOLATION_PRE_TORCH",
                        "torch_imported": False,
                    }).decode()
                )
        elif any("MEDZEN_NETWORK_RECEIPT_PRESENT" in command for command in commands):
            stdout = (
                "MEDZEN_NETWORK_RECEIPT_PRESENT\n"
                + canonical_json({
                    "schema_version": 2,
                    "status": "REFUSED_NETWORK_ISOLATION_PRE_TORCH",
                    "reason_code": "POSITIVE_NETWORK_CONVERGENCE_TIMEOUT",
                    "torch_imported": False,
                    "telemetry": {
                        "positive_convergence": [{
                            "attempt": 1,
                            "host": "api.ecr.eu-central-1.amazonaws.com",
                            "resolved_ips": ["10.0.1.10"],
                            "status": "CONNECT_REFUSED",
                            "address_outcomes": [{
                                "ip": "10.0.1.10",
                                "status": "CONNECT_REFUSED",
                                "errno": 111,
                                "elapsed_seconds": 3.0,
                            }],
                        }],
                        "allowed": {},
                        "denied": {},
                    },
                }).decode()
            )
        else:
            stdout = ""
        return {"command_id": command_id, "status": "Success", "response_code": 0, "stdout_sha256": hashlib.sha256(stdout.encode()).hexdigest(), "stdout": stdout, "stderr": ""}


def digest_scan_boundary(injection: str | None):
    read_attempts = 0

    def scan(_: Any, __: str, image: dict[str, Any], workdir: Path) -> dict[str, Any]:
        nonlocal read_attempts
        read_attempts += 1
        workdir.mkdir(parents=True, exist_ok=True)
        if injection == "image_stream_reset_then_success" and read_attempts == 1:
            from scripts.asr_idempotent_read_retry import TransientReadFault

            raise TransientReadFault("ECR_PULL_BACK", "CONNECTION_RESET")
        if injection == "image_stream_persistent_reset":
            from scripts.asr_idempotent_read_retry import TransientReadFault

            raise TransientReadFault("ECR_PULL_BACK", "CONNECTION_RESET")
        if injection == "security_wrong_digest":
            from scripts.asr_eval_digest_rescan import DigestRescanRefusal

            raise DigestRescanRefusal("ECR_RESCAN_CHILD_BINDING_DIFFERS", "injected child digest drift")
        if injection == "security_extra_finding":
            from scripts.asr_eval_digest_rescan import DigestRescanRefusal

            raise DigestRescanRefusal("SCOUT_FINDINGS_DIFFER", "injected extra finding")
        sarif = ROOT / "platform/evidence/ASR-EVAL-RUNTIME-LOCAL-SCAN-2026-003.sarif.json"
        (workdir / "docker-scout.sarif.json").write_bytes(sarif.read_bytes())
        return {
            "status": "PASS_DIGEST_VERIFIED_DUAL_SCAN_GATE",
            "reconstruction": {
                "status": "PASS_SINGLE_REPRESENTATION_EXACT_DOCKER_ARCHIVE",
                "child_digest": image["linux_amd64_digest"],
                "all_streamed_descriptors_byte_verified": True,
                "simultaneous_full_image_representations": 1,
                "oci_layout_materialized": False,
            },
            "ecr_basic": {"status": "PASS_ECR_BASIC_OS_GATE", "coverage": "OPERATING_SYSTEM_PACKAGES_ONLY", "critical": 0, "high": 0},
            "docker_scout": {
                "status": "PASS_DOCKER_SCOUT_ACCEPTED_RISK_GATE",
                "scanner_version": "1.18.3",
                "scanner_git_commit": "aa68fc25c596bea659d54867443238fd30218d23",
                "critical": 0,
                "high": 4,
                "artifact_mode": "SINGLE_STREAMED_DOCKER_ARCHIVE_OF_DIGEST_VERIFIED_ECR_CHILD",
            },
        }

    return scan


def build_rehearsal_operations(
    bindings: dict[str, Any],
    *,
    injection: str | None = None,
    root: Path = ROOT,
) -> tuple[LiveOperations, BoundaryState]:
    """Return the real stage class wired only to deterministic boundaries."""
    session = BoundarySession(bindings, injection, root=root)
    state = session.state
    if injection == "dra_not_ready":
        from scripts.run_b6a_003c_c_proof import StableDRARefusal

        def dra_waiter(**_: Any) -> dict[str, Any]:
            raise StableDRARefusal(
                "DRA stable readiness timed out: synthetic not-ready condition"
            )
    else:
        dra_waiter = lambda **_: {  # noqa: E731
            "status": "PASS_STABLE_DRA_READINESS", "stable_observations": 3
        }
    operations = LiveOperations(
        root,
        session=session,
        command_runner=ExternalCommandBoundary(state),
        kubectl_runner=KubectlBoundary(state),
        ssm_runner=SsmCommandBoundary(state),
        digest_scanner=digest_scan_boundary(injection),
        dra_waiter=dra_waiter,
        sleeper=state.sleep,
        monotonic=state.monotonic,
    )
    return operations, state


def assert_no_parallel_stage_implementation() -> dict[str, Any]:
    """Machine guard: no rehearsal class may define a pilot stage."""
    from pipeline.asr_base_model_pilot_receipts import STAGES

    offenders = []
    for value in globals().values():
        if isinstance(value, type) and value is not LiveOperations:
            offenders.extend(f"{value.__name__}.{stage}" for stage in STAGES if stage in value.__dict__)
    if offenders:
        raise AssertionError(f"parallel rehearsal stages are prohibited: {sorted(offenders)}")
    return {"status": "PASS_REAL_LIVE_OPERATIONS_ONLY", "parallel_stage_implementations": 0}
