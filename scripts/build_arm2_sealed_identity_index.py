"""Producer of the Arm-2 SEALED IDENTITY authorities (owner directive, final
activation patch).

Builds the identity-only sealed exclusion authorities the mint's
authenticate_sealed_authorities consumes: for EVERY pinned SEALED pool in the
exposure index, the exact set of ``audio_checksum_sha256`` values, and NOTHING
else — no audio, transcripts, predictions or scores. Each pool's manifest bytes
are verified against the exposure-index pin (sha256 + row count) before a single
checksum is kept.

PURE CORE, FAIL-CLOSED TO OFFLINE: :func:`build_sealed_authorities` operates only
on in-memory manifest bytes the caller passes in; it imports NO AWS SDK and
touches NO network. The LIVE production runs ONLY inside the owner-approved
protected workflow under the DEDICATED sealed-identity role
(medzen-arm2-sealed-identity-role — scoped to the exact sealed objects by
VersionId, with curated/* and every non-sealed eval read DENIED). Sealed
manifests may carry references/metadata: the producer keeps ONLY the 64-hex
identity in the protected job's memory and emits ONLY the identities + counts +
aggregates — raw manifests never persist and never reach a workstation.

Output: sealed-authorities.json = a list of {key, identities} the mint reads via
--sealed-authorities; the printed per-pool {identity_unique, identity_aggregate_
sha256} are what the owner admits to the sealed-exclusion ledger.

This module PRODUCES NOTHING and reads NO S3 on import.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from mint_arm2_nomination_split import (INDEX, MintRefusal, _agg, _canon,
                                        _is_identity, _loads_strict,
                                        _read_committed, load_packet,
                                        sealed_pools, validate_caller_identity)


class SealedProducerRefusal(RuntimeError):
    """Fail-closed: the sealed identity authorities could not be derived from
    the exact pinned sealed manifests."""


def build_sealed_authorities(sealed_manifests: dict, index: dict) -> dict:
    """Build the identity-only authorities from in-memory sealed manifest bytes.

    ``sealed_manifests`` maps each pinned SEALED pool key to its raw manifest
    bytes. Every pinned sealed pool must be present; each manifest's bytes must
    hash to the exposure-index pin and its row count must match; only the 64-hex
    ``audio_checksum_sha256`` is kept. Returns
    {"authorities": [{key, identities}], "ledger_aggregates": {key: {rows,
    identity_unique, identity_aggregate_sha256}}}."""
    pools = sealed_pools(index, pinned_only=False)
    unpinned = sorted(k for k, s in pools.items()
                      if not (s.get("sha256") and s.get("s3_version_id")))
    if unpinned:
        raise SealedProducerRefusal(
            f"the exposure index carries unpinned SEALED pools {unpinned} — "
            "pin or retire them before producing sealed identities")
    if set(sealed_manifests) != set(pools):
        raise SealedProducerRefusal(
            f"sealed manifests {sorted(sealed_manifests)} != the pinned sealed "
            f"pools {sorted(pools)} — ALL pinned sealed pools are required")
    authorities, ledger = [], {}
    for key in sorted(pools):
        pin = pools[key]
        raw = sealed_manifests[key]
        if not isinstance(raw, (bytes, bytearray)) or not raw:
            raise SealedProducerRefusal(f"{key}: no manifest bytes")
        actual = hashlib.sha256(bytes(raw)).hexdigest()
        if actual != pin.get("sha256"):
            raise SealedProducerRefusal(
                f"{key}: manifest hashes to {actual[:16]}, the exposure-index "
                f"pin declares {str(pin.get('sha256'))[:16]} — refusing "
                "unpinned sealed bytes")
        identities = []
        for line in bytes(raw).decode().splitlines():
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            value = row.get("audio_checksum_sha256")
            if not _is_identity(value):
                raise SealedProducerRefusal(
                    f"{key}: row carries a malformed audio_checksum_sha256 "
                    f"{str(value)[:24]!r}")
            identities.append(value)   # IDENTITY ONLY — nothing else survives
        if pin.get("rows") is not None and len(identities) != int(pin["rows"]):
            raise SealedProducerRefusal(
                f"{key}: manifest holds {len(identities)} rows, the pin declares "
                f"{pin['rows']}")
        if len(set(identities)) != len(identities):
            raise SealedProducerRefusal(f"{key}: duplicate identities")
        unique, aggregate = _agg(identities)
        authorities.append({"key": key, "identities": sorted(identities)})
        ledger[key] = {"rows": pin.get("rows"), "identity_unique": unique,
                       "identity_aggregate_sha256": aggregate}
    return {"authorities": authorities, "ledger_aggregates": ledger}


def produce_live(get_object, index: dict) -> dict:
    """Fetch each pinned sealed manifest by key + VersionId through the injected
    reader (identity-only) and build the authorities. Pure orchestration — no
    AWS import; the protected workflow wires the role-scoped reader."""
    pools = sealed_pools(index, pinned_only=False)
    manifests = {}
    for key in sorted(pools):
        manifests[key] = get_object(key, pools[key].get("s3_version_id"))
    return build_sealed_authorities(manifests, index)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live", action="store_true",
                    help="produce from S3 — only inside the owner-approved "
                         "protected workflow under the dedicated sealed role")
    ap.add_argument("--out", default="sealed-authorities.json")
    args = ap.parse_args(argv)
    if not args.live:
        raise SystemExit(
            "offline invocation produces nothing: the pure core "
            "(build_sealed_authorities) is driven by fixtures in "
            "tests/test_arm2_sealed_identity_index.py; the live production is "
            "the owner-approved protected-workflow step")
    packet = load_packet()
    account = str((packet.get("aws") or {}).get("account") or "")
    role_name = str(((packet.get("sealed_identity_producer") or {})
                     .get("role_name")) or "")
    try:
        import boto3  # lazy: never imported on the offline/test path
    except ImportError as exc:
        raise SystemExit(
            "boto3 is unavailable — the live producer runs only inside the "
            f"approved protected workflow with the dedicated role ({exc})")
    caller = boto3.client("sts").get_caller_identity()
    try:
        validate_caller_identity({"Account": caller.get("Account"),
                                  "Arn": caller.get("Arn")},
                                 account=account, role_name=role_name)
    except MintRefusal as exc:
        raise SystemExit(f"refusing the live sealed production: {exc}")
    index = _loads_strict(_read_committed(
        str(INDEX.relative_to(INDEX.parents[2])),
        allowed_prefixes=("platform/manifests/",)))
    bucket = str((packet.get("aws") or {}).get("bucket") or "")
    s3 = boto3.client("s3")

    def get_object(key: str, s3_version_id: str) -> bytes:
        return s3.get_object(Bucket=bucket, Key=key, VersionId=s3_version_id,
                             ExpectedBucketOwner=account)["Body"].read()

    result = produce_live(get_object, index)
    Path(args.out).write_bytes(
        _canon([a for a in result["authorities"]]))
    # print ONLY the per-pool aggregates the owner admits to the ledger
    print(json.dumps({"status": "SEALED_AUTHORITIES_PRODUCED",
                      "ledger_aggregates": result["ledger_aggregates"],
                      "out": args.out}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
