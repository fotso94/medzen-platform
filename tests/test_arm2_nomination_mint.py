"""Arm-2 Phase-A nomination-split MINTING harness — deterministic mint + the
adversarial leakage proofs (owner directive 2026-08-25). All host-safe and
OFFLINE: the pure core is driven by committed fixtures, AWS is proven
IMPOSSIBLE in the test path, and the live (S3-reading) mint is proven to refuse
before touching any AWS SDK.
"""
from __future__ import annotations

import builtins
import hashlib
import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "scripts"))

from mint_arm2_nomination_split import (  # noqa: E402
    NOMINATION_LANGUAGES, LIVE_AUTHORIZATION_ENV, LIVE_AUTHORIZATION_TOKEN,
    LiveMintForbidden, MintRefusal, candidate_pinned_pool_keys, load_index,
    live_mint, main, mint_phase_a_split, nomination_pool_keys, sealed_pool_keys,
    veto_surface_checksums, verify_frozen_manifest)
from build_arm2_exposure_index import used_union_checksums  # noqa: E402

_INDEX = load_index()
_HEX = set("0123456789abcdef")


def _ck(tag: str, i: int) -> str:
    return hashlib.sha256(f"{tag}:{i}".encode()).hexdigest()


def _clean_pool_identities(n: int = 5) -> dict[str, list[str]]:
    """Synthetic, mutually-disjoint identities for every nomination-dev, sealed
    and pinned-candidate pool. Synthetic sha256(tag:i) values never collide with
    the REAL in-repo CANDIDATE_EXPOSED union or veto surfaces, so a clean mint
    succeeds with non-empty splits."""
    pools: dict[str, list[str]] = {}
    for keys in nomination_pool_keys(_INDEX).values():
        for k in keys:
            pools[k] = [_ck(k, i) for i in range(n)]
    for k in sealed_pool_keys(_INDEX):
        pools[k] = [_ck(k, i) for i in range(n)]
    for k in candidate_pinned_pool_keys(_INDEX):
        pools[k] = [_ck(k, i) for i in range(n)]
    return pools


def _first_pool(lang: str) -> str:
    return nomination_pool_keys(_INDEX)[lang][0]


def _nonveto_candidate() -> str:
    """A CANDIDATE_EXPOSED identity that is NOT also a directional-veto row
    (candidate and veto surfaces overlap: the dev-selection includes the veto
    languages). Deterministic (sorted) so the choice is stable across processes
    regardless of PYTHONHASHSEED."""
    return sorted(used_union_checksums() - veto_surface_checksums())[0]


# --------------------------------------------------------------------------
# happy path
# --------------------------------------------------------------------------

def test_clean_mint_is_nonempty_disjoint_and_verifies():
    manifest = mint_phase_a_split(_INDEX, _clean_pool_identities())
    assert manifest["status"] == "MINTED_OFFLINE_FIXTURE"
    for lang in NOMINATION_LANGUAGES:
        assert manifest["split"][lang], f"{lang} split is empty"
    assert manifest["aggregate_overlap_counts"] == {
        "candidate_exposed": 0, "sealed": 0, "veto_surface": 0,
        "training_exposed": 0}
    assert verify_frozen_manifest(manifest, _INDEX) == []


def test_mint_is_deterministic():
    a = mint_phase_a_split(_INDEX, _clean_pool_identities())
    b = mint_phase_a_split(_INDEX, _clean_pool_identities())
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_frozen_manifest_carries_identities_only_never_text_or_audio():
    manifest = mint_phase_a_split(_INDEX, _clean_pool_identities())
    forbidden = {"text", "text_normalized", "transcript", "audio",
                 "audio_filepath", "waveform", "wav"}

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                assert k not in forbidden, f"manifest leaks a {k!r} field"
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(manifest)
    # every split value is a bare 64-hex identity
    for rows in manifest["split"].values():
        for c in rows:
            assert len(c) == 64 and set(c) <= _HEX


# --------------------------------------------------------------------------
# adversarial leakage — a poisoned pool row must NEVER reach the frozen split
# --------------------------------------------------------------------------

def test_a_candidate_exposed_row_in_a_nomination_pool_is_filtered_out():
    leaked = _nonveto_candidate()                        # a REAL candidate id
    pools = _clean_pool_identities()
    key = _first_pool("english")
    pools[key] = pools[key] + [leaked]
    manifest = mint_phase_a_split(_INDEX, pools)
    assert leaked not in manifest["split"]["english"]
    assert manifest["per_language"]["english"]["removed_candidate"] >= 1
    assert manifest["aggregate_overlap_counts"]["candidate_exposed"] == 0


def test_a_sealed_row_in_a_nomination_pool_is_filtered_out():
    sealed_key = sealed_pool_keys(_INDEX)[0]
    pools = _clean_pool_identities()
    leaked = pools[sealed_key][0]                          # a fixture sealed id
    key = _first_pool("swahili")
    pools[key] = pools[key] + [leaked]
    manifest = mint_phase_a_split(_INDEX, pools)
    assert leaked not in manifest["split"]["swahili"]
    assert manifest["per_language"]["swahili"]["removed_sealed"] >= 1
    assert manifest["aggregate_overlap_counts"]["sealed"] == 0


def test_a_veto_surface_row_in_a_nomination_pool_refuses():
    veto = next(iter(veto_surface_checksums()))
    pools = _clean_pool_identities()
    key = _first_pool("french")
    pools[key] = pools[key] + [veto]
    with pytest.raises(MintRefusal, match="veto"):
        mint_phase_a_split(_INDEX, pools)


def test_an_empty_language_split_refuses():
    # make english's only dev pool contain nothing but a candidate-exposed row
    leaked = _nonveto_candidate()
    pools = _clean_pool_identities()
    for key in nomination_pool_keys(_INDEX)["english"]:
        pools[key] = [leaked]
    with pytest.raises(MintRefusal, match="EMPTY"):
        mint_phase_a_split(_INDEX, pools)


def test_missing_pool_identities_refuses():
    pools = _clean_pool_identities()
    pools.pop(_first_pool("pidgin"))
    with pytest.raises(MintRefusal, match="not provided"):
        mint_phase_a_split(_INDEX, pools)


# --------------------------------------------------------------------------
# independent verifier catches a tampered/leaking manifest
# --------------------------------------------------------------------------

def test_verifier_flags_a_nonzero_overlap_count():
    manifest = mint_phase_a_split(_INDEX, _clean_pool_identities())
    manifest["aggregate_overlap_counts"]["sealed"] = 3
    failures = verify_frozen_manifest(manifest, _INDEX)
    assert any("sealed" in f for f in failures)


def test_verifier_flags_a_veto_identity_smuggled_into_the_split():
    manifest = mint_phase_a_split(_INDEX, _clean_pool_identities())
    manifest["split"]["english"].append(next(iter(veto_surface_checksums())))
    failures = verify_frozen_manifest(manifest, _INDEX)
    assert any("veto" in f for f in failures)


# --------------------------------------------------------------------------
# AWS is IMPOSSIBLE in the test path; the live mint refuses before touching it
# --------------------------------------------------------------------------

def test_pure_mint_works_with_aws_sdk_import_forced_to_fail(monkeypatch):
    real_import = builtins.__import__

    def no_aws(name, *a, **k):
        if name.split(".")[0] in ("boto3", "botocore"):
            raise ImportError("AWS is impossible in test mode")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_aws)
    # the pure core imports no AWS SDK, so it mints normally
    manifest = mint_phase_a_split(_INDEX, _clean_pool_identities())
    assert manifest["aggregate_overlap_counts"]["sealed"] == 0


def test_live_mint_refuses_without_authorization(monkeypatch):
    monkeypatch.delenv(LIVE_AUTHORIZATION_ENV, raising=False)
    with pytest.raises(LiveMintForbidden, match="authorization"):
        live_mint(_INDEX, s3_reader=lambda **k: b"", authorization="")


def test_live_mint_refuses_with_token_but_no_env(monkeypatch):
    monkeypatch.delenv(LIVE_AUTHORIZATION_ENV, raising=False)
    with pytest.raises(LiveMintForbidden):
        live_mint(_INDEX, s3_reader=lambda **k: b"",
                  authorization=LIVE_AUTHORIZATION_TOKEN)


def test_live_mint_refuses_with_authorization_but_no_reader(monkeypatch):
    monkeypatch.setenv(LIVE_AUTHORIZATION_ENV, LIVE_AUTHORIZATION_TOKEN)
    with pytest.raises(LiveMintForbidden, match="reader"):
        live_mint(_INDEX, s3_reader=None, authorization=LIVE_AUTHORIZATION_TOKEN)


def test_live_refusal_touches_no_aws_sdk(monkeypatch):
    """The refusal path must not import boto3/botocore — force those imports to
    explode and confirm the refusal still raises LiveMintForbidden."""
    monkeypatch.delenv(LIVE_AUTHORIZATION_ENV, raising=False)
    real_import = builtins.__import__

    def no_aws(name, *a, **k):
        if name.split(".")[0] in ("boto3", "botocore"):
            raise AssertionError("live refusal must not touch AWS")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_aws)
    with pytest.raises(LiveMintForbidden):
        live_mint(_INDEX, s3_reader=lambda **k: b"", authorization="")


# --------------------------------------------------------------------------
# the CLI mints nothing offline and has no built-in AWS client
# --------------------------------------------------------------------------

def test_cli_offline_mints_nothing():
    with pytest.raises(SystemExit, match="mints nothing"):
        main([])


def test_cli_live_has_no_builtin_client():
    with pytest.raises(SystemExit, match="no built-in AWS client"):
        main(["--live", "--authorization", LIVE_AUTHORIZATION_TOKEN])


# --------------------------------------------------------------------------
# the live-mint packet exists, is PENDING_REVIEW and pins what the mint reads
# --------------------------------------------------------------------------

def test_live_mint_packet_pins_every_pool_the_mint_reads():
    packet = json.loads(
        (_REPO / "platform/decisions/"
         "B5-UNIVERSAL-ARM2-NOMINATION-LIVE-MINT-PACKET-2026-001.json").read_bytes())
    assert packet["status"].startswith("PENDING")
    pinned = {p["key"]: p for p in packet["pinned_objects"]}
    reads = (set(sum(nomination_pool_keys(_INDEX).values(), []))
             | set(sealed_pool_keys(_INDEX))
             | set(candidate_pinned_pool_keys(_INDEX)))
    missing = sorted(reads - set(pinned))
    assert missing == [], f"packet does not pin: {missing}"
    for key in reads:
        assert pinned[key].get("s3_version_id"), f"{key} has no VersionId"
        assert pinned[key].get("sha256"), f"{key} has no sha256"
