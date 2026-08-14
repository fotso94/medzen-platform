#!/usr/bin/env python3
"""Shared DNS alignment and resolve-as-pod consistency checks."""

from __future__ import annotations

import ipaddress
from typing import Any

import yaml


VPC_DNS_RESOLVER = "172.31.0.2"
POD_DNS_FIELDS = {
    "dnsPolicy": "None",
    "dnsConfig": {"nameservers": [VPC_DNS_RESOLVER]},
}


class DnsAlignmentRefusal(RuntimeError):
    def __init__(self, reason_code: str, detail: str):
        super().__init__(detail)
        self.reason_code = reason_code
        self.detail = detail


def pod_dns_fields() -> dict[str, Any]:
    return {
        "dnsPolicy": POD_DNS_FIELDS["dnsPolicy"],
        "dnsConfig": {
            "nameservers": list(POD_DNS_FIELDS["dnsConfig"]["nameservers"])
        },
    }


def validate_pod_dns_fields(pod: dict[str, Any]) -> dict[str, Any]:
    observed = {
        "dnsPolicy": pod.get("dnsPolicy"),
        "dnsConfig": pod.get("dnsConfig"),
    }
    if observed != POD_DNS_FIELDS:
        raise DnsAlignmentRefusal(
            "POD_DNS_CONFIGURATION_DIFFERS",
            "pod DNS policy is not pinned directly to the VPC resolver",
        )
    return {
        "status": "PASS_POD_DNS_ALIGNED_TO_VPC_RESOLVER",
        "dns_policy": "None",
        "nameservers": [VPC_DNS_RESOLVER],
        "cluster_dns_dependency": False,
    }


def workload_egress_allowlist(rendered: str) -> dict[str, Any]:
    documents = [value for value in yaml.safe_load_all(rendered) if value]
    policies = [
        value
        for value in documents
        if value.get("kind") == "NetworkPolicy"
        and value.get("metadata", {}).get("name") == "asr-eval-private-egress"
    ]
    if len(policies) != 1:
        raise DnsAlignmentRefusal(
            "WORKLOAD_EGRESS_POLICY_AMBIGUOUS",
            "the rendered private-egress policy is absent or ambiguous",
        )
    tcp_cidrs: set[str] = set()
    dns_cidrs: set[str] = set()
    for rule in policies[0].get("spec", {}).get("egress", []):
        ports = {
            (item.get("protocol"), item.get("port"))
            for item in rule.get("ports", [])
        }
        cidrs = {
            item.get("ipBlock", {}).get("cidr") for item in rule.get("to", [])
        }
        if ("TCP", 443) in ports:
            tcp_cidrs.update(value for value in cidrs if isinstance(value, str))
        if ("UDP", 53) in ports or ("TCP", 53) in ports:
            dns_cidrs.update(value for value in cidrs if isinstance(value, str))
    if dns_cidrs != {f"{VPC_DNS_RESOLVER}/32"} or not tcp_cidrs:
        raise DnsAlignmentRefusal(
            "WORKLOAD_DNS_EGRESS_DIFFERS",
            "rendered DNS egress is not exactly the VPC resolver on port 53",
        )
    for value in tcp_cidrs:
        try:
            ipaddress.ip_network(value, strict=False)
        except ValueError as exc:
            raise DnsAlignmentRefusal(
                "WORKLOAD_EGRESS_CIDR_MALFORMED",
                "rendered TCP/443 egress contains a malformed CIDR",
            ) from exc
    return {
        "tcp_443_cidrs": sorted(tcp_cidrs),
        "dns_cidrs": sorted(dns_cidrs),
    }


def validate_dns_resolution_receipt(
    receipt: dict[str, Any],
    *,
    expected_hosts: list[str],
    allowed_tcp_443_cidrs: list[str],
) -> dict[str, Any]:
    if receipt.get("schema_version") != 1:
        raise DnsAlignmentRefusal(
            "DNS_CONTROL_RECEIPT_MALFORMED", "DNS control receipt schema differs"
        )
    reason_code = receipt.get("reason_code")
    if receipt.get("status") != "PASS_VPC_RESOLVER_CONSISTENCY":
        if isinstance(reason_code, str) and reason_code:
            raise DnsAlignmentRefusal(
                reason_code, "VPC-resolver DNS control refused before workload launch"
            )
        raise DnsAlignmentRefusal(
            "DNS_CONTROL_RECEIPT_MALFORMED", "DNS control receipt did not pass"
        )
    if receipt.get("effective_nameservers") != [VPC_DNS_RESOLVER]:
        raise DnsAlignmentRefusal(
            "DNS_EFFECTIVE_RESOLVER_DIFFERS",
            "DNS control pod did not use exactly the VPC resolver",
        )
    if receipt.get("torch_imported") is not False:
        raise DnsAlignmentRefusal(
            "DNS_CONTROL_TORCH_IMPORTED",
            "DNS consistency must complete before Torch is imported",
        )
    expected = sorted(set(expected_hosts))
    resolved = receipt.get("resolved_ips")
    if not isinstance(resolved, dict) or sorted(resolved) != expected:
        raise DnsAlignmentRefusal(
            "DNS_CONTROL_HOST_SET_DIFFERS",
            "DNS control receipt host set differs from the network binding",
        )
    networks = [
        ipaddress.ip_network(value, strict=False) for value in allowed_tcp_443_cidrs
    ]
    normalized: dict[str, list[str]] = {}
    for host in expected:
        values = resolved.get(host)
        if not isinstance(values, list) or not values:
            raise DnsAlignmentRefusal(
                "DNS_RESOLVER_UNREACHABLE",
                "one or more allowed hostnames did not resolve through the VPC resolver",
            )
        addresses: list[str] = []
        for value in values:
            try:
                address = ipaddress.ip_address(value)
            except ValueError as exc:
                raise DnsAlignmentRefusal(
                    "DNS_CONTROL_RECEIPT_MALFORMED",
                    "DNS control receipt contains a malformed address",
                ) from exc
            if not any(address in network for network in networks):
                raise DnsAlignmentRefusal(
                    "DNS_RESOLVED_IP_OUTSIDE_ALLOWLIST",
                    "a VPC-resolved endpoint address is outside the rendered TCP/443 allowlist",
                )
            addresses.append(str(address))
        normalized[host] = sorted(set(addresses))
    return {
        "status": "PASS_DNS_RESOLUTION_CONSISTENCY_GATE",
        "dns_policy": "None",
        "effective_nameservers": [VPC_DNS_RESOLVER],
        "resolved_ips": normalized,
        "allowed_tcp_443_cidrs": sorted(set(allowed_tcp_443_cidrs)),
        "torch_imported": False,
    }
