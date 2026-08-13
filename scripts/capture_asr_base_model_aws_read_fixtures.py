#!/usr/bin/env python3
"""Capture the ASR pilot's AWS read responses without mutating AWS.

Transport metadata, presigned credentials and object bodies are not persisted.
Their field positions plus content hashes/byte counts are retained.  Everything
else is the direct boto3 response normalized only for JSON datetime encoding.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

import boto3


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.asr_base_model_pilot_receipts import canonical_json, write_exclusive
from scripts.asr_base_model_aws_read_fixtures import source_read_inventory


PROFILE = "medzen"
REGION = "eu-central-1"
ACCOUNT = "558069890522"
CLUSTER = "medzen-speech"
GPU_ASG = "eks-gpu-b8cfd795-fa28-70a1-b844-258a0f0adc26"
BUCKET = "medzen-speech"
REPOSITORY = "medzen-asr-eval-runtime"
TAG = "pilot-5d1b8a0"
INDEX = "sha256:506d6dd5933854fade34a05d5dfe6a35be7b97dc54da541f0814a3d3e4a6b2aa"
CHILD = "sha256:85a82f348f6157adb36016d5b8d6155866ee0c4d40ae1faf4d80df677d50d14e"
ATTESTATION = "sha256:c8ad9bbae25dda5dbd3db33114fac380b9436076857aaa416b9ca33074e112e1"
CONFIG = "sha256:5cdc428267ae873aaea299c1e64fd6fbdf1d84119c4c0b2ee8d307f722e2ff9a"
PREFIX = "research/asr-base-model/pilot/1cdca3e75195c7c7417550154e36a1f372715e17efd13c835c87ee503fa84eee/"


def _normalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _normalize(body) for key, body in value.items() if key != "ResponseMetadata"}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return {"bytes": len(value), "sha256": hashlib.sha256(value).hexdigest()}
    return value


def _sanitize_instance_template(response: dict[str, Any]) -> dict[str, Any]:
    """Retain the DescribeInstances shape without unrelated account metadata."""
    value = _normalize(response)
    for reservation in value.get("Reservations", []):
        reservation.pop("OwnerId", None)
        reservation.pop("ReservationId", None)
        for instance in reservation.get("Instances", []):
            instance.pop("Tags", None)
            instance.pop("KeyName", None)
            instance.pop("ClientToken", None)
    return value


def _capture_body(response: dict[str, Any]) -> dict[str, Any]:
    body = response["Body"].read()
    value = _normalize(response)
    value["Body"] = {"bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
    return value


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def capture(output_root: Path, evidence: Path) -> dict[str, Any]:
    output_root = output_root.resolve()
    evidence = evidence.resolve()
    if evidence.exists():
        raise FileExistsError(evidence)
    output_root.mkdir(parents=True, exist_ok=True)
    session = boto3.Session(profile_name=PROFILE, region_name=REGION)
    clients = {name: session.client(name) for name in ("sts", "eks", "autoscaling", "ec2", "ecr", "s3", "ssm")}
    captures: list[dict[str, Any]] = []

    def save(name: str, api: str, request: dict[str, Any], response: Any,
             *, region: str = REGION, dynamic: list[str] | None = None,
             sanitization: list[str] | None = None) -> None:
        path = output_root / f"{name}.json"
        write_exclusive(path, canonical_json(_normalize(response)))
        captures.append({
            "name": name,
            "api": api,
            "region": region,
            "request": request,
            "path": str(path.relative_to(Path.cwd())),
            "sha256": _sha(path),
            "dynamic_value_paths": dynamic or [],
            "sanitization": sanitization or [],
        })

    sts = clients["sts"]
    save("sts-get-caller-identity", "sts:GetCallerIdentity", {}, sts.get_caller_identity())

    eks = clients["eks"]
    for name in ("cpu", "gpu"):
        request = {"clusterName": CLUSTER, "nodegroupName": name}
        save(
            f"eks-describe-nodegroup-{name}", "eks:DescribeNodegroup", request,
            eks.describe_nodegroup(**request),
            dynamic=[
                "nodegroup.scalingConfig.desiredSize",
                "nodegroup.resources.autoScalingGroups.0.name",
                "nodegroup.status",
            ],
        )
    request = {"clusterName": CLUSTER, "addonName": "vpc-cni"}
    save("eks-describe-addon-vpc-cni", "eks:DescribeAddon", request, eks.describe_addon(**request), dynamic=["addon.configurationValues", "addon.status"])
    request = {"name": CLUSTER}
    save("eks-describe-cluster", "eks:DescribeCluster", request, eks.describe_cluster(**request))

    asg = clients["autoscaling"]
    request = {"AutoScalingGroupNames": [GPU_ASG]}
    save(
        "autoscaling-describe-gpu-group", "autoscaling:DescribeAutoScalingGroups", request,
        asg.describe_auto_scaling_groups(**request),
        dynamic=[
            "AutoScalingGroups.0.DesiredCapacity",
            "AutoScalingGroups.0.Instances",
        ],
    )
    request = {"AutoScalingGroupName": GPU_ASG}
    save(
        "autoscaling-describe-scheduled-actions-empty", "autoscaling:DescribeScheduledActions", request,
        asg.describe_scheduled_actions(**request),
        dynamic=["ScheduledUpdateGroupActions"],
    )

    ec2 = clients["ec2"]
    zero_filters = [
        {"Name": "vpc-id", "Values": ["vpc-051aa9df8b64bf141"]},
        {"Name": "tag:MedZenPurpose", "Values": ["asr-base-model-eval"]},
    ]
    save("ec2-describe-eval-vpc-endpoints-empty", "ec2:DescribeVpcEndpoints", {"Filters": zero_filters}, ec2.describe_vpc_endpoints(Filters=zero_filters))
    request = {"VpcEndpointIds": ["vpce-0c807782b5e1c9577"]}
    interface = ec2.describe_vpc_endpoints(**request)
    save(
        "ec2-describe-vpc-endpoint-interface-template", "ec2:DescribeVpcEndpoints", request, interface,
        dynamic=[
            "VpcEndpoints.0.VpcEndpointId", "VpcEndpoints.0.ServiceName",
            "VpcEndpoints.0.NetworkInterfaceIds", "VpcEndpoints.0.Groups.0.GroupId",
            "VpcEndpoints.0.Groups.0.GroupName", "VpcEndpoints.0.PolicyDocument",
            "VpcEndpoints.0.PrivateDnsEnabled", "VpcEndpoints.0.Tags",
        ],
    )
    us_east_2 = boto3.Session(profile_name=PROFILE, region_name="us-east-2").client("ec2")
    request = {"VpcEndpointIds": ["vpce-09b2f7b21a4f625f3"]}
    gateway = us_east_2.describe_vpc_endpoints(**request)
    save(
        "ec2-describe-vpc-endpoint-gateway-template", "ec2:DescribeVpcEndpoints", request, gateway,
        region="us-east-2",
        dynamic=[
            "VpcEndpoints.0.VpcEndpointId", "VpcEndpoints.0.ServiceName",
            "VpcEndpoints.0.RouteTableIds", "VpcEndpoints.0.PolicyDocument",
            "VpcEndpoints.0.Tags", "VpcEndpoints.0.VpcId", "VpcEndpoints.0.OwnerId",
            "VpcEndpoints.0.ServiceRegion",
        ],
    )
    interface_pair = ec2.describe_vpc_endpoints(
        VpcEndpointIds=["vpce-0c807782b5e1c9577", "vpce-04ef4b2d1bc9726ee"]
    )
    eni_ids = [
        eni
        for endpoint in interface_pair["VpcEndpoints"]
        for eni in endpoint["NetworkInterfaceIds"]
    ]
    request = {"NetworkInterfaceIds": eni_ids}
    save(
        "ec2-describe-network-interfaces-template", "ec2:DescribeNetworkInterfaces", request,
        ec2.describe_network_interfaces(**request),
        dynamic=[
            "NetworkInterfaces.0.NetworkInterfaceId", "NetworkInterfaces.0.PrivateIpAddress",
            "NetworkInterfaces.1.NetworkInterfaceId", "NetworkInterfaces.1.PrivateIpAddress",
            "NetworkInterfaces.2.NetworkInterfaceId", "NetworkInterfaces.2.PrivateIpAddress",
            "NetworkInterfaces.3.NetworkInterfaceId", "NetworkInterfaces.3.PrivateIpAddress",
            "NetworkInterfaces.4.NetworkInterfaceId", "NetworkInterfaces.4.PrivateIpAddress",
            "NetworkInterfaces.5.NetworkInterfaceId", "NetworkInterfaces.5.PrivateIpAddress",
        ],
    )
    request = {"Filters": [{"Name": "prefix-list-name", "Values": [f"com.amazonaws.{REGION}.s3"]}]}
    prefix_lists = ec2.describe_prefix_lists(**request)
    save("ec2-describe-prefix-lists-s3", "ec2:DescribePrefixLists", request, prefix_lists)
    prefix_id = prefix_lists["PrefixLists"][0]["PrefixListId"]
    request = {"PrefixListId": prefix_id}
    save("ec2-get-managed-prefix-list-entries-s3", "ec2:GetManagedPrefixListEntries", request, ec2.get_managed_prefix_list_entries(**request))
    request = {"InstanceIds": ["i-087c38e7c60da5a28"]}
    us_east_1 = boto3.Session(profile_name=PROFILE, region_name="us-east-1").client("ec2")
    save(
        "ec2-describe-instance-template", "ec2:DescribeInstances", request,
        _sanitize_instance_template(us_east_1.describe_instances(**request)), region="us-east-1",
        dynamic=[
            "Reservations.0.Instances.0.InstanceId",
            "Reservations.0.Instances.0.Placement.AvailabilityZone",
        ],
        sanitization=["unrelated instance Tags, KeyName, ClientToken, OwnerId and ReservationId removed"],
    )
    volume_filters = [
        {"Name": "tag:MedZenPurpose", "Values": ["asr-base-model-eval"]},
        {"Name": "status", "Values": ["available", "in-use", "creating"]},
    ]
    save("ec2-describe-eval-volumes-empty", "ec2:DescribeVolumes", {"Filters": volume_filters}, ec2.describe_volumes(Filters=volume_filters))
    request = {"VolumeIds": ["vol-0c8b9f6916b207635"]}
    save(
        "ec2-describe-volume-template", "ec2:DescribeVolumes", request,
        us_east_1.describe_volumes(**request), region="us-east-1",
        dynamic=[
            "Volumes.0.VolumeId", "Volumes.0.AvailabilityZone", "Volumes.0.State",
            "Volumes.0.Size", "Volumes.0.Attachments",
        ],
    )

    ecr = clients["ecr"]
    request = {"repositoryNames": [REPOSITORY]}
    save("ecr-describe-repository", "ecr:DescribeRepositories", request, ecr.describe_repositories(**request))
    media_index = ["application/vnd.oci.image.index.v1+json"]
    media_manifest = ["application/vnd.oci.image.manifest.v1+json"]
    for name, image_ids, media in (
        ("ecr-batch-get-index-by-tag", [{"imageTag": TAG}], media_index),
        ("ecr-batch-get-index-by-digest", [{"imageDigest": INDEX}], media_index),
        ("ecr-batch-get-child", [{"imageDigest": CHILD}], media_manifest),
        ("ecr-batch-get-attestation", [{"imageDigest": ATTESTATION}], media_manifest),
    ):
        request = {"repositoryName": REPOSITORY, "imageIds": image_ids, "acceptedMediaTypes": media}
        save(name, "ecr:BatchGetImage", request, ecr.batch_get_image(**request))
    child_manifest = json.loads(ecr.batch_get_image(repositoryName=REPOSITORY, imageIds=[{"imageDigest": CHILD}], acceptedMediaTypes=media_manifest)["images"][0]["imageManifest"])
    digests = [child_manifest["config"]["digest"], *[item["digest"] for item in child_manifest["layers"]]]
    request = {"repositoryName": REPOSITORY, "layerDigests": digests}
    save("ecr-batch-check-layer-availability", "ecr:BatchCheckLayerAvailability", request, ecr.batch_check_layer_availability(**request))
    response = ecr.get_download_url_for_layer(repositoryName=REPOSITORY, layerDigest=CONFIG)
    response["downloadUrl"] = "<redacted-presigned-download-url>"
    request = {"repositoryName": REPOSITORY, "layerDigest": CONFIG}
    save("ecr-get-download-url-template", "ecr:GetDownloadUrlForLayer", request, response, dynamic=["downloadUrl", "layerDigest"], sanitization=["downloadUrl credential-bearing value redacted"])
    request = {"repositoryName": REPOSITORY, "imageId": {"imageDigest": CHILD}}
    save("ecr-describe-image-scan-findings", "ecr:DescribeImageScanFindings", request, ecr.describe_image_scan_findings(**request))
    save("ecr-get-registry-scanning-configuration", "ecr:GetRegistryScanningConfiguration", {}, ecr.get_registry_scanning_configuration())

    s3 = clients["s3"]
    proof = json.loads((Path.cwd() / "platform/evidence/ASR-BASE-MODEL-PRESTAGE-PROOF-2026-001.json").read_bytes())
    for index, item in enumerate(proof["objects"], 1):
        request = {"Bucket": BUCKET, "Key": item["key"], "VersionId": item["version_id"], "ChecksumMode": "ENABLED"}
        save(f"s3-head-prestage-{index:02d}", "s3:HeadObject", request, s3.head_object(**request))
    absent_request = {
        "Bucket": BUCKET,
        "Key": "research/asr-base-model/pilot/fixture-capture/known-absent-object",
    }
    try:
        s3.head_object(**absent_request)
    except s3.exceptions.ClientError as exc:
        absent_response = {
            key: value
            for key, value in exc.response.items()
            if key != "ResponseMetadata"
        }
        if absent_response.get("Error", {}).get("Code") != "404":
            raise
    else:
        raise RuntimeError("known-absent S3 fixture key unexpectedly exists")
    save(
        "s3-head-object-not-found",
        "s3:HeadObject",
        absent_request,
        absent_response,
        sanitization=["ResponseMetadata removed from modeled ClientError"],
    )
    bundle = proof["pilot_bundle"]["object"]
    request = {"Bucket": BUCKET, "Key": bundle["key"], "VersionId": bundle["version_id"]}
    save("s3-get-pilot-bundle", "s3:GetObject", request, _capture_body(s3.get_object(**request)), sanitization=["Body replaced by bytes and SHA-256"])
    model_bindings = next(
        item for item in proof["objects"] if item["key"].endswith("/model-bindings.json")
    )
    request = {"Bucket": BUCKET, "Key": model_bindings["key"], "VersionId": model_bindings["version_id"]}
    save("s3-get-model-bindings", "s3:GetObject", request, _capture_body(s3.get_object(**request)), sanitization=["Body replaced by bytes and SHA-256"])
    list_request = {"Bucket": BUCKET, "Prefix": "eval/", "MaxKeys": 1}
    listing = s3.list_objects_v2(**list_request)
    save("s3-list-eval-manifests-template", "s3:ListObjectsV2", list_request, listing)
    first_key = listing["Contents"][0]["Key"]
    request = {"Bucket": BUCKET, "Key": first_key}
    save("s3-get-eval-manifest-template", "s3:GetObject", request, _capture_body(s3.get_object(**request)), sanitization=["Body replaced by bytes and SHA-256"])

    ssm = clients["ssm"]
    request = {"CommandId": "7c7d9ed4-9fb5-4e20-9915-a7af390d20d8", "InstanceId": "i-02ac6ba203d231f97"}
    ssm_response = ssm.get_command_invocation(**request)
    ssm_response["StandardOutputContent"] = "<redacted-non-pilot-command-output>"
    ssm_response["StandardErrorContent"] = "<redacted-non-pilot-command-error>"
    save(
        "ssm-get-command-invocation-template", "ssm:GetCommandInvocation", request,
        ssm_response,
        dynamic=[
            "CommandId", "InstanceId", "Status", "StatusDetails", "ResponseCode",
            "StandardOutputContent", "StandardErrorContent",
        ],
        sanitization=["historical non-pilot command stdout and stderr redacted"],
    )

    caller = sts.get_caller_identity()["Arn"]
    inventory = sorted(source_read_inventory(Path.cwd()))
    covered = {item["api"] for item in captures}
    if set(inventory) != covered:
        raise RuntimeError(f"capture coverage differs: missing={sorted(set(inventory)-covered)} extra={sorted(covered-set(inventory))}")
    record = {
        "id": "ASR-BASE-MODEL-AWS-READ-FIXTURE-CAPTURE-2026-001",
        "status": "PASS_READ_ONLY_LIVE_CAPTURE_COMPLETE_ASR_EXECUTOR_COVERAGE",
        "recorded_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "aws": {"account": ACCOUNT, "primary_region": REGION, "caller_identity": caller, "mutations": 0, "credentials_phi_audio_or_predictions": False},
        "scope": {"sources": list((
            "scripts/asr_base_model_pilot_live.py", "scripts/asr_eval_digest_rescan.py",
            "scripts/asr_eval_oci_publication.py", "scripts/asr_base_model_pilot_staging.py",
        )), "explicit_read_api_count": len(inventory), "fixture_count": len(captures), "uncovered_read_apis": 0},
        "normalization": {
            "removed_fields": ["ResponseMetadata"],
            "redacted_fields": ["ECR GetDownloadUrlForLayer.downloadUrl", "unrelated DescribeInstances account metadata"],
            "body_policy": "S3 GetObject body replaced with exact byte count and SHA-256; committed content fixtures remain separately hash-bound.",
            "dynamic_replay_rule": "Only declared dynamic_value_paths may change; every path must exist in its captured real payload before substitution; no key insertion is permitted.",
        },
        "runtime_api_inventory": inventory,
        "captures": captures,
        "historical_attempt_10_refusal": {
            "path": "platform/evidence/ASR-BASE-MODEL-PACKET-2026-002I-ATTEMPT-10-NETWORK-ISOLATION-REFUSAL.json",
            "sha256": hashlib.sha256((Path.cwd() / "platform/evidence/ASR-BASE-MODEL-PACKET-2026-002I-ATTEMPT-10-NETWORK-ISOLATION-REFUSAL.json").read_bytes()).hexdigest(),
        },
        "status_facts": {
            "s3_prefix_list_api": "ec2:DescribePrefixLists",
            "describe_vpc_endpoints_prefix_list_id_present": False,
            "boundary_fake_invented_fields_permitted": False,
            "read_only_capture_mutations": 0,
        },
        "stateful_dynamic_replay": {
            "rationale": "Resources created during a future attempt do not exist during this zero-mutation capture. Their read responses start from a real captured payload and replace only declared existing fields; collection-valued state is derived solely from the rehearsed mutation request.",
            "whole_collection_paths": [
                "autoscaling-describe-scheduled-actions-empty:ScheduledUpdateGroupActions",
                "autoscaling-describe-gpu-group:AutoScalingGroups.0.Instances"
            ],
            "field_lineage": "ScheduledActionName, DesiredCapacity and InstanceId come directly from prior rehearsed mutation outputs and are the only fields consumed by LiveOperations.",
            "new_undeclared_fields_permitted": False
        },
    }
    write_exclusive(evidence, canonical_json(record))
    return {"status": record["status"], "evidence_sha256": _sha(evidence), "fixture_count": len(captures), "api_count": len(inventory), "aws_mutations": 0}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(capture(args.output_root, args.evidence), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
