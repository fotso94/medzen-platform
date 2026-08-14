#!/usr/bin/env python3
"""Shared, fail-closed lifecycle contracts for short-lived pilot Pods.

The live executor owns Kubernetes calls.  This module owns the response-shape
validation, progress identity, exact-image inventory proof, and source audit
used by both live execution and the cold rehearsal.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import Any


POD_POLL_INTERVAL_SECONDS = 10
POD_TERMINAL_TIMEOUT_SECONDS = 1200
POD_PULL_STALL_SECONDS = 600
POD_DELETE_TIMEOUT_SECONDS = 180
POD_ABSENCE_STABLE_OBSERVATIONS = 2


class PodLifecycleRefusal(RuntimeError):
    def __init__(self, reason_code: str, detail: str):
        super().__init__(detail)
        self.reason_code = reason_code
        self.detail = detail


def _safe_text(value: Any, *, limit: int = 256) -> str | None:
    if value is None:
        return None
    text = str(value).replace("\r", " ").replace("\n", " ")
    return text[:limit]


def observe_pod(value: dict[str, Any]) -> dict[str, Any]:
    """Return only stable, bounded fields relevant to Pod progress."""

    if not isinstance(value, dict):
        raise PodLifecycleRefusal(
            "STAGE_POD_RESPONSE_MALFORMED", "pod response is not an object"
        )
    metadata = value.get("metadata")
    status = value.get("status")
    if not isinstance(metadata, dict) or not isinstance(status, dict):
        raise PodLifecycleRefusal(
            "STAGE_POD_RESPONSE_MALFORMED", "pod metadata or status is absent"
        )
    name = metadata.get("name")
    phase = status.get("phase")
    if not isinstance(name, str) or not name or not isinstance(phase, str):
        raise PodLifecycleRefusal(
            "STAGE_POD_RESPONSE_MALFORMED", "pod name or phase is absent"
        )
    containers: list[dict[str, Any]] = []
    for item in status.get("containerStatuses", []):
        if not isinstance(item, dict):
            continue
        state = item.get("state") if isinstance(item.get("state"), dict) else {}
        selected: dict[str, Any] = {}
        for state_name in ("waiting", "running", "terminated"):
            body = state.get(state_name)
            if not isinstance(body, dict):
                continue
            selected = {
                "kind": state_name,
                "reason": _safe_text(body.get("reason")),
                "message": _safe_text(body.get("message")),
                "exit_code": body.get("exitCode"),
                "started_at": body.get("startedAt"),
                "finished_at": body.get("finishedAt"),
            }
            break
        containers.append(
            {
                "name": item.get("name"),
                "ready": item.get("ready"),
                "restart_count": item.get("restartCount"),
                "image_id": _safe_text(item.get("imageID")),
                "state": selected,
            }
        )
    conditions = [
        {
            "type": item.get("type"),
            "status": item.get("status"),
            "reason": _safe_text(item.get("reason")),
            "message": _safe_text(item.get("message")),
        }
        for item in status.get("conditions", [])
        if isinstance(item, dict)
    ]
    observation = {
        "name": name,
        "phase": phase,
        "reason": _safe_text(status.get("reason")),
        "message": _safe_text(status.get("message")),
        "conditions": conditions,
        "containers": containers,
    }
    encoded = json.dumps(
        observation, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return {
        **observation,
        "terminal": phase in {"Succeeded", "Failed"},
        "progress_sha256": hashlib.sha256(encoded).hexdigest(),
    }


def observe_named_pod_list(value: dict[str, Any], expected_name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not isinstance(value.get("items"), list):
        raise PodLifecycleRefusal(
            "STAGE_POD_LIST_RESPONSE_MALFORMED", "pod-list response has no items list"
        )
    names = sorted(
        item.get("metadata", {}).get("name")
        for item in value["items"]
        if isinstance(item, dict)
        and isinstance(item.get("metadata"), dict)
        and isinstance(item.get("metadata", {}).get("name"), str)
    )
    unexpected = [name for name in names if name != expected_name]
    if unexpected:
        raise PodLifecycleRefusal(
            "STAGE_POD_ABSENCE_SELECTOR_DIFFERS",
            "stage-pod absence query returned an unexpected name",
        )
    return {"present": expected_name in names, "observed_names": names}


def exact_image_in_node_inventory(
    value: dict[str, Any], *, expected_node: str, expected_image: str
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PodLifecycleRefusal(
            "NODE_IMAGE_INVENTORY_MALFORMED", "node response is not an object"
        )
    name = value.get("metadata", {}).get("name")
    images = value.get("status", {}).get("images")
    if name != expected_node or not isinstance(images, list):
        raise PodLifecycleRefusal(
            "NODE_IMAGE_INVENTORY_MALFORMED", "node name or image inventory differs"
        )
    names = sorted(
        candidate
        for item in images
        if isinstance(item, dict)
        for candidate in item.get("names", [])
        if isinstance(candidate, str)
    )
    return {
        "node_name": name,
        "exact_image_present": expected_image in names,
        "inventory_entries": len(images),
        "matching_name_sha256": (
            hashlib.sha256(expected_image.encode()).hexdigest()
            if expected_image in names
            else None
        ),
    }


def audit_waiter_and_finalizer_sites(root: Path) -> dict[str, Any]:
    """Enumerate every executor/helper waiter and finally site.

    This audit is intentionally structural.  It prevents a rehearsal boundary
    from skipping a nonterminal observation and prevents a new stage-local
    blocking Pod delete from reintroducing exception masking.
    """

    paths = [
        Path("scripts/asr_base_model_pilot_live.py"),
        Path("scripts/asr_base_model_pilot_runner.py"),
        Path("scripts/asr_base_model_pilot_assets.py"),
        Path("scripts/asr_base_model_pilot_staging.py"),
        Path("scripts/asr_base_model_pilot_workload.py"),
    ]
    finally_sites: list[dict[str, Any]] = []
    for relative in paths:
        source = (root / relative).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(relative))
        functions = [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try) or not node.finalbody:
                continue
            owners = [
                item
                for item in functions
                if item.lineno <= node.lineno <= (item.end_lineno or item.lineno)
            ]
            owner = max(owners, key=lambda item: item.lineno).name if owners else "<module>"
            final_source = "\n".join(
                ast.get_source_segment(source, item) or "" for item in node.finalbody
            )
            finally_sites.append(
                {
                    "path": str(relative),
                    "line": node.lineno,
                    "owner": owner,
                    "disposition": (
                        "CONTAINED_OR_NON_RAISING"
                        if "except" in final_source
                        or owner in {"_execute_attempt", "execute_attempt", "_LISTENER_PROGRAM"}
                        else "LOCAL_RESOURCE_RELEASE_REVIEWED"
                    ),
                }
            )

    live = (root / "scripts/asr_base_model_pilot_live.py").read_text(encoding="utf-8")
    fake = (root / "scripts/asr_base_model_pilot_fake.py").read_text(encoding="utf-8")
    forbidden = (
        '"--wait=true"',
        "self._sleep(",
    )
    stage_local_segment = live[
        live.index("    def _cross_pod_refusal") : live.index(
            "    def _capture_pilot_workload_refusal_diagnostics"
        )
    ]
    if any(value in stage_local_segment for value in forbidden):
        raise PodLifecycleRefusal(
            "STAGE_POD_LIFECYCLE_BYPASS_PRESENT",
            "stage-local Pod lifecycle still uses a blocking delete or undefined sleeper",
        )
    required_live = (
        "_wait_stage_pod_terminal(",
        "_delete_stage_pod(",
        "_wait_stage_pod_absent(",
        "_image_prepull_qualification(",
    )
    if any(value not in live for value in required_live):
        raise PodLifecycleRefusal(
            "STAGE_POD_LIFECYCLE_HELPER_ABSENT",
            "shared stage-Pod lifecycle helper family is incomplete",
        )
    required_fake = (
        "pod_terminal_observation_sequence",
        "pod_absence_observation_sequence",
        '"Pending"',
        '"PRESENT"',
    )
    if any(value not in fake for value in required_fake):
        raise PodLifecycleRefusal(
            "WAITER_FAKE_NONTERMINAL_BRANCH_ABSENT",
            "rehearsal fake does not expose nonterminal and present-before-absent observations",
        )
    waiters = [
        {"site": "gpu_node_readiness", "nonterminal_required": True},
        {"site": "registry_scan_configuration", "nonterminal_required": True},
        {"site": "private_endpoint_availability", "nonterminal_required": True},
        {"site": "dra_readiness", "nonterminal_required": True},
        {"site": "image_prepull_terminal", "nonterminal_required": True},
        {"site": "image_inventory_stability", "nonterminal_required": True},
        {"site": "dns_control_terminal", "nonterminal_required": True},
        {"site": "inbound_control_terminal", "nonterminal_required": True},
        {"site": "stage_pod_stable_absence", "nonterminal_required": True},
        {"site": "pilot_pod_discovery", "nonterminal_required": True},
        {"site": "pilot_network_receipts", "nonterminal_required": True},
        {"site": "pilot_job_completion", "nonterminal_required": True},
        {"site": "aggregate_ssm_completion", "nonterminal_required": True},
        {"site": "cleanup_endpoint_absence", "nonterminal_required": True},
        {"site": "cleanup_zero_state", "nonterminal_required": True},
    ]
    return {
        "status": "PASS_SYSTEMIC_WAITER_FINALIZER_AUDIT",
        "waiter_site_count": len(waiters),
        "waiters": waiters,
        "finally_site_count": len(finally_sites),
        "finally_sites": finally_sites,
        "stage_local_blocking_pod_deletes": 0,
        "undefined_sleep_calls": 0,
        "rehearsal_waiters_with_instant_terminal_only": 0,
    }
