#!/usr/bin/env python3
"""Shared bounded-helper contracts for the offline ASR pilot.

Live execution and cold rehearsal both enter real or injected helpers through
these validators. A fake therefore cannot accept parameters that the real
helper rejects.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from scripts.asr_base_model_node_staging import STAGING_SSM_TIMEOUT_SECONDS
from scripts.asr_base_model_pod_lifecycle import (
    POD_ABSENCE_STABLE_OBSERVATIONS,
    POD_DELETE_TIMEOUT_SECONDS,
    POD_POLL_INTERVAL_SECONDS,
    POD_TERMINAL_TIMEOUT_SECONDS,
)


@dataclass(frozen=True)
class IntegerBound:
    minimum: int
    maximum: int
    default: int


BOUNDARY_CONTRACTS: dict[str, dict[str, IntegerBound]] = {
    "external_command": {"timeout": IntegerBound(1, 10_800, 900)},
    "kubectl": {"timeout": IntegerBound(1, 10_800, 900)},
    "ssm": {"timeout_seconds": IntegerBound(1, 1_800, 900)},
    "nodegroup": {
        "desired": IntegerBound(0, 1, 0),
        "timeout_seconds": IntegerBound(1, 1_200, 1_200),
    },
    "gpu_node_readiness": {
        "timeout_seconds": IntegerBound(1, 600, 600),
        "poll_interval_seconds": IntegerBound(1, 60, 10),
        "required_observations": IntegerBound(2, 10, 2),
    },
    "volume_attachment": {
        "timeout_seconds": IntegerBound(1, 300, 300),
        "poll_interval_seconds": IntegerBound(1, 30, 5),
        "required_observations": IntegerBound(2, 10, 2),
    },
    "pilot_receipt_readiness": {
        "timeout_seconds": IntegerBound(1, 300, 300),
        "poll_interval_seconds": IntegerBound(1, 60, 10),
        "required_observations": IntegerBound(2, 10, 2),
    },
    "stage_pod_terminal": {
        "timeout_seconds": IntegerBound(1, 1_200, POD_TERMINAL_TIMEOUT_SECONDS),
        "poll_interval_seconds": IntegerBound(1, 60, POD_POLL_INTERVAL_SECONDS),
    },
    "stage_pod_absence": {
        "timeout_seconds": IntegerBound(1, 300, POD_DELETE_TIMEOUT_SECONDS),
        "poll_interval_seconds": IntegerBound(1, 30, 5),
        "required_observations": IntegerBound(
            2, 10, POD_ABSENCE_STABLE_OBSERVATIONS
        ),
    },
    "pilot_job_completion": {
        # Maximum tracks the largest bindable Job cap: the 21,600s window
        # ceiling minus the fixed 1,800s host reserve.
        "timeout_seconds": IntegerBound(1, 19_800, 9_000),
        "poll_interval_seconds": IntegerBound(1, 60, 10),
    },
    "registry_scan_configuration": {
        "timeout_seconds": IntegerBound(1, 300, 120)
    },
    "dra_wait": {
        "timeout_seconds": IntegerBound(1, 300, 300),
        "poll_seconds": IntegerBound(1, 30, 2),
    },
}

DRA_WAIT_TIMEOUT_SECONDS = BOUNDARY_CONTRACTS["dra_wait"]["timeout_seconds"].default
DRA_WAIT_MAX_SECONDS = BOUNDARY_CONTRACTS["dra_wait"]["timeout_seconds"].maximum
DRA_WAIT_POLL_SECONDS = BOUNDARY_CONTRACTS["dra_wait"]["poll_seconds"].default


class BoundaryContractRefusal(RuntimeError):
    def __init__(self, reason_code: str, detail: str):
        super().__init__(detail)
        self.reason_code = reason_code
        self.detail = detail


def validate_boundary_parameters(boundary: str, **values: Any) -> dict[str, Any]:
    contract = BOUNDARY_CONTRACTS.get(boundary)
    if contract is None or set(values) != set(contract):
        raise BoundaryContractRefusal(
            "BOUNDARY_CONTRACT_UNKNOWN_OR_INCOMPLETE",
            f"boundary parameter set differs: {boundary}",
        )
    normalized: dict[str, int] = {}
    for name, bound in contract.items():
        value = values[name]
        if isinstance(value, bool) or not isinstance(value, int):
            raise BoundaryContractRefusal(
                "BOUNDARY_PARAMETER_NOT_INTEGER",
                f"{boundary}.{name} must be one integer",
            )
        if not bound.minimum <= value <= bound.maximum:
            raise BoundaryContractRefusal(
                "BOUNDARY_PARAMETER_OUT_OF_RANGE",
                f"{boundary}.{name} must be between {bound.minimum} and {bound.maximum}",
            )
        normalized[name] = value
    for timeout_name, poll_name in (
        ("timeout_seconds", "poll_interval_seconds"),
        ("timeout_seconds", "poll_seconds"),
    ):
        if timeout_name in normalized and poll_name in normalized and normalized[poll_name] >= normalized[timeout_name]:
            raise BoundaryContractRefusal(
                "BOUNDARY_PARAMETER_RELATION_DIFFERS",
                f"{boundary}.{poll_name} must be shorter than {timeout_name}",
            )
    return {
        "status": "PASS_SHARED_BOUNDARY_PARAMETERS",
        "boundary": boundary,
        "parameters": normalized,
    }


def invoke_dra_waiter(
    waiter: Callable[..., dict[str, Any]],
    *,
    kubeconfig: Path,
    timeout_seconds: int = DRA_WAIT_TIMEOUT_SECONDS,
    poll_seconds: int = DRA_WAIT_POLL_SECONDS,
) -> dict[str, Any]:
    """Validate before invoking either the real helper or a rehearsal fake."""
    validate_boundary_parameters(
        "dra_wait",
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
    )
    return waiter(
        kubeconfig=kubeconfig,
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
    )


_CALL_AUDIT = {
    "_command": ("external_command", {"timeout": "timeout"}),
    "_json_external_command": ("external_command", {"timeout": "timeout"}),
    "_aws": ("external_command", {"timeout": "timeout"}),
    "_kubectl": ("kubectl", {"timeout": "timeout"}),
    "_ssm": ("ssm", {"timeout_seconds": "timeout_seconds"}),
    "_wait_nodegroup": (
        "nodegroup",
        {"desired": "desired", "timeout_seconds": "timeout_seconds"},
    ),
    "_wait_gpu_node_ready": (
        "gpu_node_readiness",
        {
            "timeout_seconds": "timeout_seconds",
            "poll_interval_seconds": "poll_interval_seconds",
            "required_observations": "required_observations",
        },
    ),
    "_wait_volume_attachment": (
        "volume_attachment",
        {
            "timeout_seconds": "timeout_seconds",
            "poll_interval_seconds": "poll_interval_seconds",
            "required_observations": "required_observations",
        },
    ),
    "_wait_pilot_network_receipts": (
        "pilot_receipt_readiness",
        {
            "timeout_seconds": "timeout_seconds",
            "poll_interval_seconds": "poll_interval_seconds",
            "required_observations": "required_observations",
        },
    ),
    "_wait_stage_pod_terminal": (
        "stage_pod_terminal",
        {
            "timeout_seconds": "timeout_seconds",
            "poll_interval_seconds": "poll_interval_seconds",
        },
    ),
    "_wait_stage_pod_absent": (
        "stage_pod_absence",
        {
            "timeout_seconds": "timeout_seconds",
            "poll_interval_seconds": "poll_interval_seconds",
            "required_observations": "required_observations",
        },
    ),
    "_wait_pilot_job_complete": (
        "pilot_job_completion",
        {
            "timeout_seconds": "timeout_seconds",
            "poll_interval_seconds": "poll_interval_seconds",
        },
    ),
    "_wait_registry_scanning_configuration": (
        "registry_scan_configuration", {"timeout_seconds": "timeout_seconds"}
    ),
    "wait_for_stable_dra": (
        "dra_wait", {"timeout_seconds": "timeout_seconds", "poll_seconds": "poll_seconds"}
    ),
    "invoke_dra_waiter": (
        "dra_wait", {"timeout_seconds": "timeout_seconds"}
    ),
    "_dra_readiness": (
        "dra_wait", {"timeout_seconds": "timeout_seconds"}
    ),
}


def _module_constants(tree: ast.Module) -> dict[str, int]:
    values: dict[str, int] = {}
    changed = True
    while changed:
        changed = False
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value: int | None = None
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, int) and not isinstance(node.value.value, bool):
                value = node.value.value
            elif isinstance(node.value, ast.Name) and node.value.id in values:
                value = values[node.value.id]
            if value is None:
                continue
            for target in targets:
                if isinstance(target, ast.Name) and values.get(target.id) != value:
                    values[target.id] = value
                    changed = True
    return values


def _function_defaults(node: ast.FunctionDef | ast.AsyncFunctionDef, constants: dict[str, int]) -> dict[str, int]:
    positional = list(node.args.posonlyargs) + list(node.args.args)
    defaults = [None] * (len(positional) - len(node.args.defaults)) + list(node.args.defaults)
    pairs = [(arg.arg, default) for arg, default in zip(positional, defaults)]
    pairs.extend((arg.arg, default) for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults))
    result: dict[str, int] = {}
    for name, value in pairs:
        if isinstance(value, ast.Constant) and isinstance(value.value, int) and not isinstance(value.value, bool):
            result[name] = value.value
        elif isinstance(value, ast.Name) and value.id in constants:
            result[name] = constants[value.id]
    return result


def _integer_expression(node: ast.AST, constants: dict[str, int], defaults: dict[str, int]) -> int | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id, defaults.get(node.id))
    # bound_attempt_window(...)["job_active_deadline_seconds"] resolves
    # statically to its worst case (the contract maximum): the resolver
    # itself caps every bindable value at window ceiling minus reserve, and
    # validate_boundary_parameters still validates the exact runtime value
    # inside the waiter.
    if (
        isinstance(node, ast.Subscript)
        and isinstance(node.slice, ast.Constant)
        and node.slice.value == "job_active_deadline_seconds"
        and isinstance(node.value, ast.Call)
        and (
            (isinstance(node.value.func, ast.Name) and node.value.func.id == "bound_attempt_window")
            or (isinstance(node.value.func, ast.Attribute) and node.value.func.attr == "bound_attempt_window")
        )
    ):
        return BOUNDARY_CONTRACTS["pilot_job_completion"]["timeout_seconds"].maximum
    return None


def audit_bounded_helper_calls(root: Path) -> dict[str, Any]:
    """Enumerate and validate every bounded-helper call in the execution family."""
    paths = [
        root / "scripts/asr_base_model_pilot_live.py",
        root / "scripts/run_b6a_003c_c_proof.py",
        root / "scripts/run_b6a_003c_d_sampler_self_test.py",
        root / "scripts/run_b6a_003c_e_sampler_self_test.py",
        root / "scripts/run_b6a_003c_f_sampler_self_test.py",
    ]
    calls: list[dict[str, Any]] = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        constants = {
            "DRA_WAIT_TIMEOUT_SECONDS": DRA_WAIT_TIMEOUT_SECONDS,
            "DRA_WAIT_MAX_SECONDS": DRA_WAIT_MAX_SECONDS,
            "DRA_WAIT_POLL_SECONDS": DRA_WAIT_POLL_SECONDS,
            "STAGING_SSM_TIMEOUT_SECONDS": STAGING_SSM_TIMEOUT_SECONDS,
            "POD_TERMINAL_TIMEOUT_SECONDS": POD_TERMINAL_TIMEOUT_SECONDS,
            "POD_POLL_INTERVAL_SECONDS": POD_POLL_INTERVAL_SECONDS,
            "POD_DELETE_TIMEOUT_SECONDS": POD_DELETE_TIMEOUT_SECONDS,
            "POD_ABSENCE_STABLE_OBSERVATIONS": POD_ABSENCE_STABLE_OBSERVATIONS,
            **_module_constants(tree),
        }
        functions = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id if isinstance(node.func, ast.Name) else None
            if name not in _CALL_AUDIT:
                continue
            owners = [item for item in functions if item.lineno <= node.lineno <= (item.end_lineno or item.lineno)]
            owner = max(owners, key=lambda item: item.lineno) if owners else None
            defaults = _function_defaults(owner, constants) if owner is not None else {}
            boundary, keyword_map = _CALL_AUDIT[name]
            keywords = {item.arg: item.value for item in node.keywords if item.arg is not None}
            values: dict[str, int] = {}
            for contract_name, contract in BOUNDARY_CONTRACTS[boundary].items():
                call_name = keyword_map.get(contract_name, contract_name)
                expression = keywords.get(call_name)
                if expression is None:
                    values[contract_name] = contract.default
                    continue
                measured = _integer_expression(expression, constants, defaults)
                if measured is None:
                    raise BoundaryContractRefusal(
                        "BOUNDARY_CALL_ARGUMENT_NOT_STATICALLY_RESOLVABLE",
                        f"{path.name}:{node.lineno} {name}.{call_name}",
                    )
                values[contract_name] = measured
            validate_boundary_parameters(boundary, **values)
            calls.append({
                "path": str(path.relative_to(root)),
                "line": node.lineno,
                "helper": name,
                "boundary": boundary,
                "parameters": values,
            })
    if not calls:
        raise BoundaryContractRefusal("BOUNDARY_CALL_AUDIT_EMPTY", "no bounded-helper call sites were found")

    live_path = paths[0]
    live_tree = ast.parse(live_path.read_text(encoding="utf-8"), filename=str(live_path))
    functions = [node for node in ast.walk(live_tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    expected_wrappers = {
        "_command_runner": "_command",
        "_kubectl_runner": "_kubectl",
        "_ssm_runner": "_ssm",
        "_dra_waiter": "_dra_readiness",
    }
    direct: list[dict[str, Any]] = []
    for node in ast.walk(live_tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute) or node.func.attr not in expected_wrappers:
            continue
        owners = [item for item in functions if item.lineno <= node.lineno <= (item.end_lineno or item.lineno)]
        owner = max(owners, key=lambda item: item.lineno).name if owners else None
        direct.append({"helper": node.func.attr, "line": node.lineno, "wrapper": owner})
        if owner != expected_wrappers[node.func.attr]:
            raise BoundaryContractRefusal(
                "INJECTED_BOUNDARY_BYPASSES_SHARED_WRAPPER",
                f"{node.func.attr} is called from {owner}",
            )
    return {
        "status": "PASS_ALL_BOUNDED_HELPER_CALLS",
        "audited_files": [str(path.relative_to(root)) for path in paths],
        "call_site_count": len(calls),
        "call_sites": calls,
        "direct_injected_boundary_count": len(direct),
        "direct_injected_boundaries": direct,
        "fake_may_bypass_validation": False,
    }
