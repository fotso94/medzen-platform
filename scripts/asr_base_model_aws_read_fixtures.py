#!/usr/bin/env python3
"""Recorded-real AWS response fixtures for the offline ASR pilot executor.

The live-capture record is the authority.  Rehearsal boundaries may replay a
fixture byte-for-byte, or may substitute values only at a path explicitly
listed as dynamic in that record.  Dynamic replay may never introduce a key
that is absent from the recorded service response shape.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]

DIRECT_READ_METHODS: dict[str, set[str]] = {
    "batch_check_layer_availability": {"ecr:BatchCheckLayerAvailability"},
    "batch_get_image": {"ecr:BatchGetImage"},
    "describe_addon": {"eks:DescribeAddon"},
    "describe_auto_scaling_groups": {"autoscaling:DescribeAutoScalingGroups"},
    "describe_image_scan_findings": {"ecr:DescribeImageScanFindings"},
    "describe_instances": {"ec2:DescribeInstances"},
    "describe_network_interfaces": {"ec2:DescribeNetworkInterfaces"},
    "describe_nodegroup": {"eks:DescribeNodegroup"},
    "describe_prefix_lists": {"ec2:DescribePrefixLists"},
    "describe_repositories": {"ecr:DescribeRepositories"},
    "describe_scheduled_actions": {"autoscaling:DescribeScheduledActions"},
    "describe_volumes": {"ec2:DescribeVolumes"},
    "describe_vpc_endpoints": {"ec2:DescribeVpcEndpoints"},
    "download_fileobj": {"s3:HeadObject", "s3:GetObject"},
    "get_caller_identity": {"sts:GetCallerIdentity"},
    "get_command_invocation": {"ssm:GetCommandInvocation"},
    "get_download_url_for_layer": {"ecr:GetDownloadUrlForLayer"},
    "get_managed_prefix_list_entries": {"ec2:GetManagedPrefixListEntries"},
    "get_object": {"s3:GetObject"},
    "get_registry_scanning_configuration": {"ecr:GetRegistryScanningConfiguration"},
    "get_waiter": {"ec2:DescribeVolumes"},
    "head_object": {"s3:HeadObject"},
}

EXTERNAL_READ_APIS = {
    "eks:DescribeCluster",       # aws eks update-kubeconfig
    "s3:GetObject",             # aws s3 sync/download_fileobj
    "s3:HeadObject",            # managed download_fileobj
    "s3:ListObjectsV2",         # aws s3 sync
}

SOURCES = (
    "scripts/asr_base_model_pilot_live.py",
    "scripts/asr_eval_digest_rescan.py",
    "scripts/asr_eval_oci_publication.py",
    "scripts/asr_base_model_pilot_staging.py",
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


CLIENT_NAMES = {"asg", "ec2", "ecr", "eks", "s3", "ssm", "sts"}
READ_PREFIXES = ("batch_", "describe_", "download_", "get_", "head_", "list_")


def _read_methods(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    methods: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        receiver = node.func.value
        client = (
            receiver.attr
            if isinstance(receiver, ast.Attribute)
            and isinstance(receiver.value, ast.Name)
            and receiver.value.id == "self"
            else receiver.id if isinstance(receiver, ast.Name) else None
        )
        if client not in CLIENT_NAMES:
            continue
        method = node.func.attr
        if method.startswith(READ_PREFIXES) and method != "generate_presigned_url":
            methods.add(method)
    unknown = methods - set(DIRECT_READ_METHODS)
    if unknown:
        raise AssertionError(f"unmapped executor AWS read methods: {sorted(unknown)}")
    return methods


def source_read_inventory(root: Path = ROOT) -> set[str]:
    methods: set[str] = set()
    for relative in SOURCES:
        methods.update(_read_methods(root / relative))
    apis = {
        api
        for method in methods
        for api in DIRECT_READ_METHODS[method]
    }
    apis.update(EXTERNAL_READ_APIS)
    return apis


class FixtureCatalog:
    """Load and validate the packet-bound, read-only live-capture set."""

    def __init__(self, root: Path, binding: dict[str, Any]):
        self.root = root
        evidence_path = root / str(binding.get("path", ""))
        if not evidence_path.is_file() or sha256_file(evidence_path) != binding.get("sha256"):
            raise AssertionError("AWS read fixture evidence binding differs")
        self.evidence_path = evidence_path
        self.evidence = json.loads(evidence_path.read_bytes())
        if (
            self.evidence.get("status")
            != "PASS_READ_ONLY_LIVE_CAPTURE_COMPLETE_ASR_EXECUTOR_COVERAGE"
            or self.evidence.get("aws", {}).get("mutations") != 0
        ):
            raise AssertionError("AWS read fixture evidence is not a zero-mutation PASS")
        captures = self.evidence.get("captures")
        if not isinstance(captures, list):
            raise AssertionError("AWS read fixture capture list is malformed")
        self._captures: dict[str, dict[str, Any]] = {}
        covered: set[str] = set()
        for capture in captures:
            name = capture.get("name")
            api = capture.get("api")
            relative = capture.get("path")
            digest = capture.get("sha256")
            if (
                not isinstance(name, str)
                or name in self._captures
                or not isinstance(api, str)
                or not isinstance(relative, str)
                or not isinstance(digest, str)
            ):
                raise AssertionError("AWS read fixture binding is malformed")
            path = root / relative
            if sha256_file(path) != digest:
                raise AssertionError(f"AWS read fixture hash differs: {relative}")
            payload = json.loads(path.read_bytes())
            if not isinstance(payload, dict):
                raise AssertionError(f"AWS read fixture is not an object: {relative}")
            self._captures[name] = {**capture, "payload": payload}
            covered.add(api)
        discovered = source_read_inventory(root)
        declared = set(self.evidence.get("runtime_api_inventory", []))
        if discovered != declared or not discovered.issubset(covered):
            raise AssertionError("AWS read fixture API coverage is incomplete or stale")

    def payload(self, name: str) -> dict[str, Any]:
        try:
            return copy.deepcopy(self._captures[name]["payload"])
        except KeyError as exc:
            raise AssertionError(f"AWS read fixture is absent: {name}") from exc

    def dynamic_paths(self, name: str) -> set[str]:
        try:
            values = self._captures[name].get("dynamic_value_paths", [])
        except KeyError as exc:
            raise AssertionError(f"AWS read fixture is absent: {name}") from exc
        if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
            raise AssertionError(f"dynamic fixture paths are malformed: {name}")
        return set(values)

    def replay(self, name: str, replacements: dict[str, Any] | None = None) -> dict[str, Any]:
        """Replay a real payload, replacing only predeclared dynamic paths."""
        value = self.payload(name)
        replacements = replacements or {}
        permitted = self.dynamic_paths(name)
        if set(replacements) - permitted:
            raise AssertionError(
                f"fixture replay attempted undeclared fields: {name}:"
                f"{sorted(set(replacements) - permitted)}"
            )
        for dotted, replacement in replacements.items():
            current: Any = value
            segments = dotted.split(".")
            for segment in segments[:-1]:
                if segment.isdigit():
                    if not isinstance(current, list) or int(segment) >= len(current):
                        raise AssertionError(f"dynamic fixture path differs: {name}:{dotted}")
                    current = current[int(segment)]
                else:
                    if not isinstance(current, dict) or segment not in current:
                        raise AssertionError(f"dynamic fixture path differs: {name}:{dotted}")
                    current = current[segment]
            final = segments[-1]
            if final.isdigit():
                if not isinstance(current, list) or int(final) >= len(current):
                    raise AssertionError(f"dynamic fixture path differs: {name}:{dotted}")
                current[int(final)] = copy.deepcopy(replacement)
            else:
                if not isinstance(current, dict) or final not in current:
                    raise AssertionError(f"dynamic fixture path differs: {name}:{dotted}")
                current[final] = copy.deepcopy(replacement)
        return value

    def summary(self) -> dict[str, Any]:
        return {
            "status": "PASS_RECORDED_REAL_AWS_RESPONSE_COVERAGE",
            "evidence_path": str(self.evidence_path.relative_to(self.root)),
            "evidence_sha256": sha256_file(self.evidence_path),
            "runtime_read_api_count": len(self.evidence["runtime_api_inventory"]),
            "fixture_count": len(self._captures),
            "uncovered_read_apis": 0,
            "boundary_fake_invented_fields": 0,
        }


def _path_exists(value: Any, dotted: str) -> bool:
    current = value
    for segment in dotted.split("."):
        if segment == "*":
            if not isinstance(current, list) or not current:
                return False
            current = current[0]
        elif segment.isdigit():
            if not isinstance(current, list) or int(segment) >= len(current):
                return False
            current = current[int(segment)]
        else:
            if not isinstance(current, dict) or segment not in current:
                return False
            current = current[segment]
    return True


def validate_dynamic_paths(catalog: FixtureCatalog) -> dict[str, Any]:
    checked = 0
    for name, capture in catalog._captures.items():
        payload = capture["payload"]
        for path in catalog.dynamic_paths(name):
            if not _path_exists(payload, path):
                raise AssertionError(f"dynamic path is absent from real response: {name}:{path}")
            checked += 1
    return {
        "status": "PASS_DYNAMIC_VALUES_ONLY_ON_CAPTURED_FIELDS",
        "dynamic_path_count": checked,
        "invented_field_count": 0,
    }
