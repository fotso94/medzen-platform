"""Fail-closed ECR Basic Scanning rule merge and restoration helpers."""

from __future__ import annotations

import copy
import json
from typing import Any


SCAN_FREQUENCY = "SCAN_ON_PUSH"
FILTER_TYPE = "WILDCARD"


class RegistryScanningConfigurationRefusal(ValueError):
    """Raised when an ECR scanning configuration is ambiguous or malformed."""


def canonical_configuration(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def validate_configuration(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("scanType") != "BASIC":
        raise RegistryScanningConfigurationRefusal(
            "ECR registry scanning configuration must be BASIC"
        )
    rules = value.get("rules")
    if not isinstance(rules, list):
        raise RegistryScanningConfigurationRefusal("ECR scanning rules must be a list")
    frequencies: set[str] = set()
    for rule in rules:
        if not isinstance(rule, dict):
            raise RegistryScanningConfigurationRefusal("ECR scanning rule is malformed")
        frequency = rule.get("scanFrequency")
        filters = rule.get("repositoryFilters")
        if not isinstance(frequency, str) or not frequency:
            raise RegistryScanningConfigurationRefusal(
                "ECR scanning rule frequency is malformed"
            )
        if frequency in frequencies:
            raise RegistryScanningConfigurationRefusal(
                f"duplicate ECR scan frequency: {frequency}"
            )
        frequencies.add(frequency)
        if not isinstance(filters, list) or not filters:
            raise RegistryScanningConfigurationRefusal(
                f"ECR scanning rule filters are malformed for {frequency}"
            )
        identities: set[tuple[str, str]] = set()
        for repository_filter in filters:
            if not isinstance(repository_filter, dict):
                raise RegistryScanningConfigurationRefusal(
                    "ECR repository filter is malformed"
                )
            name = repository_filter.get("filter")
            filter_type = repository_filter.get("filterType")
            if not isinstance(name, str) or not name or filter_type != FILTER_TYPE:
                raise RegistryScanningConfigurationRefusal(
                    "ECR repository filter name or type is malformed"
                )
            identity = (name, filter_type)
            if identity in identities:
                raise RegistryScanningConfigurationRefusal(
                    f"duplicate ECR repository filter: {name}"
                )
            identities.add(identity)
    return copy.deepcopy(value)


def merge_scan_on_push_filter(
    value: dict[str, Any], repository: str
) -> tuple[dict[str, Any], bool]:
    if not isinstance(repository, str) or not repository:
        raise RegistryScanningConfigurationRefusal("ECR repository name is malformed")
    updated = validate_configuration(value)
    exact = {"filter": repository, "filterType": FILTER_TYPE}
    for rule in updated["rules"]:
        for repository_filter in rule["repositoryFilters"]:
            if repository_filter.get("filter") == repository:
                if rule["scanFrequency"] != SCAN_FREQUENCY or repository_filter != exact:
                    raise RegistryScanningConfigurationRefusal(
                        "ECR repository filter exists with ambiguous frequency or type"
                    )
                return updated, False
    target = next(
        (
            rule
            for rule in updated["rules"]
            if rule["scanFrequency"] == SCAN_FREQUENCY
        ),
        None,
    )
    if target is None:
        updated["rules"].append(
            {"scanFrequency": SCAN_FREQUENCY, "repositoryFilters": [exact]}
        )
    else:
        target["repositoryFilters"].append(exact)
    validate_configuration(updated)
    return updated, True
