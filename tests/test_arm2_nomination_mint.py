"""Arm-2 Phase-A nomination-split MINTING harness — deterministic mint + the
adversarial leakage proofs (owner directive 2026-08-25, corrected per Codex
round 34). All host-safe and OFFLINE: the pure core is driven by committed
fixtures, AWS is proven IMPOSSIBLE in the test path, the live (S3-reading)
mint refuses before touching any AWS SDK, and the three round-34 reproduced
bypasses (unverified bytes accepted as FROZEN; a candidate leak passing the
verifier; cross-language duplicates accepted) are locked as regressions.
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

import mint_arm2_nomination_split as mint_mod  # noqa: E402
from mint_arm2_nomination_split import (  # noqa: E402
    NOMINATION_LANGUAGES, LiveMintForbidden, MintRefusal,
    candidate_pinned_pool_keys, load_index, live_mint, main,
    mint_phase_a_split, nomination_pool_keys, sealed_pool_keys,
    veto_surface_checksums, verify_frozen_manifest)
from build_arm2_exposure_index import used_union_checksums  # noqa: E402

_INDEX = load_index()
_PACKET_PATH = (_REPO / "platform/decisions/"
                "B5-UNIVERSAL-ARM2-NOMINATION-LIVE-MINT-PACKET-2026-001.json")
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


def _training_ids(n: int = 7) -> set[str]:
    return {_ck("training-corpus", i) for i in range(n)}


def _first_pool(lang: str) -> str:
    return nomination_pool_keys(_INDEX)[lang][0]


def _nonveto_candidate() -> str:
    """A CANDIDATE_EXPOSED identity that is NOT also a directional-veto row
    (candidate and veto surfaces overlap: the dev-selection includes the veto
    languages). Deterministic (sorted) so the choice is stable across processes
    regardless of PYTHONHASHSEED."""
    return sorted(used_union_checksums() - veto_surface_checksums())[0]


# --------------------------------------------------------------------------
# happy path — training identities supplied => every overlap a RECOMPUTED zero
# --------------------------------------------------------------------------

def test_clean_mint_with_training_index_is_nonempty_disjoint_and_verifies():
    pools = _clean_pool_identities()
    train = _training_ids()
    manifest = mint_phase_a_split(_INDEX, pools, training_identities=train)
    assert manifest["status"] == "MINTED_OFFLINE_FIXTURE"
    for lang in NOMINATION_LANGUAGES:
        assert manifest["split"][lang], f"{lang} split is empty"
    assert manifest["aggregate_overlap_counts"] == {
        "candidate_exposed": 0, "sealed": 0, "veto_surface": 0,
        "training_exposed": 0}
    assert verify_frozen_manifest(manifest, _INDEX, pools,
                                  training_identities=train) == []


def test_mint_is_deterministic():
    train = _training_ids()
    a = mint_phase_a_split(_INDEX, _clean_pool_identities(),
                           training_identities=train)
    b = mint_phase_a_split(_INDEX, _clean_pool_identities(),
                           training_identities=train)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_frozen_manifest_carries_identities_only_never_text_or_audio():
    manifest = mint_phase_a_split(_INDEX, _clean_pool_identities(),
                                  training_identities=_training_ids())
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
    for rows in manifest["split"].values():
        for c in rows:
            assert len(c) == 64 and set(c) <= _HEX


# --------------------------------------------------------------------------
# Codex round 34 finding 1: training overlap is NEVER zero without an anti-join
# --------------------------------------------------------------------------

def test_training_overlap_without_index_is_unverified_never_zero():
    manifest = mint_phase_a_split(_INDEX, _clean_pool_identities())
    assert manifest["aggregate_overlap_counts"]["training_exposed"] == "UNVERIFIED"
    assert manifest["exclusion_provenance"]["training_exposed_unique"] == "UNVERIFIED"
    for lang in NOMINATION_LANGUAGES:
        assert manifest["per_language"][lang]["removed_training"] == "UNVERIFIED"


def test_frozen_mint_refuses_without_the_training_identity_index():
    with pytest.raises(MintRefusal, match="training identity index"):
        mint_phase_a_split(_INDEX, _clean_pool_identities(), status="FROZEN")


def test_a_training_row_in_a_nomination_pool_is_anti_joined_out():
    pools = _clean_pool_identities()
    train = _training_ids()
    leaked = sorted(train)[0]
    key = _first_pool("english")
    pools[key] = pools[key] + [leaked]
    manifest = mint_phase_a_split(_INDEX, pools, training_identities=train)
    assert leaked not in manifest["split"]["english"]
    assert manifest["per_language"]["english"]["removed_training"] == 1
    assert manifest["aggregate_overlap_counts"]["training_exposed"] == 0


def test_verifier_rejects_a_numeric_training_claim_without_the_index():
    """The exact round-34 inaccuracy: a manifest claiming training overlap 0
    when no identity index exists to check it must FAIL verification."""
    pools = _clean_pool_identities()
    manifest = mint_phase_a_split(_INDEX, pools,
                                  training_identities=_training_ids())
    failures = verify_frozen_manifest(manifest, _INDEX, pools)  # no index here
    assert any("unverifiable" in f.lower() for f in failures)


# --------------------------------------------------------------------------
# adversarial leakage — a poisoned pool row must NEVER reach the frozen split
# --------------------------------------------------------------------------

def test_a_candidate_exposed_row_in_a_nomination_pool_is_filtered_out():
    leaked = _nonveto_candidate()                        # a REAL candidate id
    pools = _clean_pool_identities()
    key = _first_pool("english")
    pools[key] = pools[key] + [leaked]
    manifest = mint_phase_a_split(_INDEX, pools,
                                  training_identities=_training_ids())
    assert leaked not in manifest["split"]["english"]
    assert manifest["per_language"]["english"]["removed_candidate"] >= 1
    assert manifest["aggregate_overlap_counts"]["candidate_exposed"] == 0


def test_a_sealed_row_in_a_nomination_pool_is_filtered_out():
    sealed_key = sealed_pool_keys(_INDEX)[0]
    pools = _clean_pool_identities()
    leaked = pools[sealed_key][0]                          # a fixture sealed id
    key = _first_pool("swahili")
    pools[key] = pools[key] + [leaked]
    manifest = mint_phase_a_split(_INDEX, pools,
                                  training_identities=_training_ids())
    assert leaked not in manifest["split"]["swahili"]
    assert manifest["per_language"]["swahili"]["removed_sealed"] >= 1
    assert manifest["aggregate_overlap_counts"]["sealed"] == 0


def test_a_veto_surface_row_in_a_nomination_pool_refuses():
    veto = next(iter(veto_surface_checksums()))
    pools = _clean_pool_identities()
    key = _first_pool("french")
    pools[key] = pools[key] + [veto]
    with pytest.raises(MintRefusal, match="veto"):
        mint_phase_a_split(_INDEX, pools, training_identities=_training_ids())


def test_an_empty_language_split_refuses():
    leaked = _nonveto_candidate()
    pools = _clean_pool_identities()
    for key in nomination_pool_keys(_INDEX)["english"]:
        pools[key] = [leaked]
    with pytest.raises(MintRefusal, match="EMPTY"):
        mint_phase_a_split(_INDEX, pools, training_identities=_training_ids())


def test_missing_pool_identities_refuses():
    pools = _clean_pool_identities()
    pools.pop(_first_pool("pidgin"))
    with pytest.raises(MintRefusal, match="not provided"):
        mint_phase_a_split(_INDEX, pools, training_identities=_training_ids())


def test_a_malformed_identity_refuses():
    pools = _clean_pool_identities()
    pools[_first_pool("english")].append("NOT-A-SHA")
    with pytest.raises(MintRefusal, match="malformed"):
        mint_phase_a_split(_INDEX, pools, training_identities=_training_ids())


# --------------------------------------------------------------------------
# Codex round 34 finding 4 (reproduced): duplicates refuse, within AND across
# --------------------------------------------------------------------------

def test_within_language_duplicate_identities_refuse():
    pools = _clean_pool_identities()
    key = _first_pool("english")
    pools[key] = pools[key] + [pools[key][0]]
    with pytest.raises(MintRefusal, match="duplicate identities within"):
        mint_phase_a_split(_INDEX, pools, training_identities=_training_ids())


def test_cross_language_duplicate_identities_refuse():
    """Round-34 reproduction (c): the same checksum under english AND french
    used to mint successfully — it must now refuse."""
    pools = _clean_pool_identities()
    dup = _ck("shared-across-langs", 0)
    pools[_first_pool("english")].append(dup)
    pools[_first_pool("french")].append(dup)
    with pytest.raises(MintRefusal, match="MORE THAN ONE language"):
        mint_phase_a_split(_INDEX, pools, training_identities=_training_ids())


# --------------------------------------------------------------------------
# Codex round 34 finding 3 (reproduced): the verifier RECOMPUTES, never trusts
# --------------------------------------------------------------------------

def test_verifier_catches_a_candidate_leak_even_with_a_fixed_aggregate():
    """Round-34 reproduction (b): insert a REAL candidate-exposed identity and
    update the per-language aggregate so the declared numbers are internally
    consistent — the old verifier returned []; it must now fail on the
    RECOMPUTED candidate overlap AND the re-derived eligible set."""
    pools = _clean_pool_identities()
    train = _training_ids()
    manifest = mint_phase_a_split(_INDEX, pools, training_identities=train)
    leak = _nonveto_candidate()
    manifest["split"]["english"].append(leak)
    manifest["per_language"]["english"]["split_aggregate_sha256"] = \
        mint_mod._agg(manifest["split"]["english"])[1]
    failures = verify_frozen_manifest(manifest, _INDEX, pools,
                                      training_identities=train)
    assert any("CANDIDATE_EXPOSED" in f for f in failures), failures


def test_verifier_catches_a_sealed_identity_smuggled_into_the_split():
    pools = _clean_pool_identities()
    train = _training_ids()
    manifest = mint_phase_a_split(_INDEX, pools, training_identities=train)
    sealed_id = pools[sealed_pool_keys(_INDEX)[0]][0]
    manifest["split"]["swahili"].append(sealed_id)
    manifest["per_language"]["swahili"]["split_aggregate_sha256"] = \
        mint_mod._agg(manifest["split"]["swahili"])[1]
    failures = verify_frozen_manifest(manifest, _INDEX, pools,
                                      training_identities=train)
    assert any("SEALED" in f for f in failures), failures


def test_verifier_catches_a_veto_identity_smuggled_into_the_split():
    pools = _clean_pool_identities()
    train = _training_ids()
    manifest = mint_phase_a_split(_INDEX, pools, training_identities=train)
    manifest["split"]["english"].append(next(iter(veto_surface_checksums())))
    manifest["per_language"]["english"]["split_aggregate_sha256"] = \
        mint_mod._agg(manifest["split"]["english"])[1]
    failures = verify_frozen_manifest(manifest, _INDEX, pools,
                                      training_identities=train)
    assert any("veto" in f for f in failures), failures


def test_verifier_catches_a_cross_language_duplicate():
    pools = _clean_pool_identities()
    train = _training_ids()
    manifest = mint_phase_a_split(_INDEX, pools, training_identities=train)
    moved = manifest["split"]["english"][0]
    manifest["split"]["french"].append(moved)
    manifest["per_language"]["french"]["split_aggregate_sha256"] = \
        mint_mod._agg(manifest["split"]["french"])[1]
    failures = verify_frozen_manifest(manifest, _INDEX, pools,
                                      training_identities=train)
    assert any("cross-language" in f for f in failures), failures


def test_verifier_catches_a_tampered_split_identity():
    pools = _clean_pool_identities()
    train = _training_ids()
    manifest = mint_phase_a_split(_INDEX, pools, training_identities=train)
    manifest["split_identity"]["unique"] += 1
    failures = verify_frozen_manifest(manifest, _INDEX, pools,
                                      training_identities=train)
    assert any("split_identity" in f for f in failures), failures


def test_verifier_catches_a_dropped_row_via_the_rederived_set():
    """Silently REMOVING an eligible row (biasing the split) is also caught —
    the verifier re-derives the full eligible set and requires equality."""
    pools = _clean_pool_identities()
    train = _training_ids()
    manifest = mint_phase_a_split(_INDEX, pools, training_identities=train)
    dropped = manifest["split"]["english"].pop()
    manifest["per_language"]["english"]["split_aggregate_sha256"] = \
        mint_mod._agg(manifest["split"]["english"])[1]
    failures = verify_frozen_manifest(manifest, _INDEX, pools,
                                      training_identities=train)
    assert dropped and any("re-derived" in f for f in failures), failures


# --------------------------------------------------------------------------
# Codex round 34 finding 2 (reproduced): the trusted path verifies every byte
# --------------------------------------------------------------------------

_SYN_KMS = "arn:aws:kms:eu-central-1:558069890522:key/synthetic-cmk"


def _synthetic_world(n: int = 3):
    """A synthetic exposure index + packet + object store whose pins are
    COMPUTED from the fixture bytes, so the live path's integrity checks can be
    exercised end-to-end offline — and each dimension mutated adversarially."""
    langs = list(NOMINATION_LANGUAGES)
    sources, store = [], {}

    def add(key, cls, role, language):
        rows = [{"audio_checksum_sha256": _ck(key, i), "language": language}
                for i in range(n)]
        body = "".join(json.dumps(r) + "\n" for r in rows).encode()
        sources.append({"key": key, "class": cls, "role": role,
                        "language": language, "rows": n,
                        "sha256": hashlib.sha256(body).hexdigest(),
                        "s3_version_id": f"v-{key}"})
        store[key] = body

    for lang in langs:
        add(f"eval/{lang}/dev/manifest.jsonl", "BASE_EXPOSED",
            "eval_dev_half", lang)
    add("eval/english/sealed/manifest.jsonl", "SEALED",
        "sealed_holdout_half", "english")
    add("eval/kw/candidate/manifest.jsonl", "CANDIDATE_EXPOSED",
        "kinyarwanda_eval", "kinyarwanda")
    index = {"pinned_sources": sources}
    packet = {"aws": {"kms_key": _SYN_KMS},
              "pinned_objects": [{"key": s["key"], "sha256": s["sha256"],
                                  "s3_version_id": s["s3_version_id"]}
                                 for s in sources]}

    def reader(key, s3_version_id):
        return {"body": store[key], "version_id": f"v-{key}",
                "kms_key_arn": _SYN_KMS}

    return index, packet, store, reader


def test_live_mint_happy_path_is_frozen_and_verified():
    index, packet, _, reader = _synthetic_world()
    manifest = live_mint(index, packet, s3_reader=reader,
                         training_identities=_training_ids())
    assert manifest["status"] == "FROZEN"
    assert manifest["aggregate_overlap_counts"] == {
        "candidate_exposed": 0, "sealed": 0, "veto_surface": 0,
        "training_exposed": 0}


def test_live_mint_refuses_fabricated_bytes():
    """Round-34 reproduction (a): a reader returning bytes that match NO
    declared sha (UNVERIFIED_BYTES_ACCEPTED=FROZEN) must now refuse on the
    ACTUAL-bytes hash check."""
    index, packet, _, _ = _synthetic_world()

    def evil(key, s3_version_id):
        ck = hashlib.sha256(f"fabricated:{key}".encode()).hexdigest()
        return {"body": json.dumps({"audio_checksum_sha256": ck}).encode()
                + b"\n", "version_id": f"v-{key}", "kms_key_arn": _SYN_KMS}

    with pytest.raises(MintRefusal, match="unverified bytes"):
        live_mint(index, packet, s3_reader=evil,
                  training_identities=_training_ids())


def test_live_mint_refuses_a_wrong_version_id_echo():
    index, packet, store, _ = _synthetic_world()

    def wrong_version(key, s3_version_id):
        return {"body": store[key], "version_id": "v-SOMETHING-ELSE",
                "kms_key_arn": _SYN_KMS}

    with pytest.raises(MintRefusal, match="VersionId"):
        live_mint(index, packet, s3_reader=wrong_version,
                  training_identities=_training_ids())


def test_live_mint_refuses_a_wrong_kms_key():
    index, packet, store, _ = _synthetic_world()

    def wrong_kms(key, s3_version_id):
        return {"body": store[key], "version_id": f"v-{key}",
                "kms_key_arn": "arn:aws:kms:eu-central-1:999:key/other"}

    with pytest.raises(MintRefusal, match="CMK"):
        live_mint(index, packet, s3_reader=wrong_kms,
                  training_identities=_training_ids())


def test_live_mint_refuses_a_wrong_row_count():
    index, packet, store, _ = _synthetic_world()

    def truncated(key, s3_version_id):
        body = store[key]
        cut = body.decode().splitlines()[0].encode() + b"\n"
        # keep the sha consistent with the TRUNCATED bytes so ONLY the row
        # count check can catch it
        for pin in index["pinned_sources"] + packet["pinned_objects"]:
            if pin["key"] == key:
                pin["sha256"] = hashlib.sha256(cut).hexdigest()
        return {"body": cut, "version_id": f"v-{key}", "kms_key_arn": _SYN_KMS}

    with pytest.raises(MintRefusal, match="rows"):
        live_mint(index, packet, s3_reader=truncated,
                  training_identities=_training_ids())


def test_live_mint_refuses_a_wrong_row_language():
    index, packet, store, _ = _synthetic_world()
    key = "eval/english/dev/manifest.jsonl"
    rows = [{"audio_checksum_sha256": _ck(key, i), "language": "french"}
            for i in range(3)]
    body = "".join(json.dumps(r) + "\n" for r in rows).encode()
    for pin in index["pinned_sources"] + packet["pinned_objects"]:
        if pin["key"] == key:
            pin["sha256"] = hashlib.sha256(body).hexdigest()
    store[key] = body

    def reader(key, s3_version_id):
        return {"body": store[key], "version_id": f"v-{key}",
                "kms_key_arn": _SYN_KMS}

    with pytest.raises(MintRefusal, match="language"):
        live_mint(index, packet, s3_reader=reader,
                  training_identities=_training_ids())


def test_live_mint_refuses_a_bare_bytes_reader():
    """A reader that returns raw bytes without echoed VersionId/KMS metadata
    cannot prove what it fetched — refused."""
    index, packet, store, _ = _synthetic_world()
    with pytest.raises(MintRefusal, match="echoed"):
        live_mint(index, packet, s3_reader=lambda key, s3_version_id: store[key],
                  training_identities=_training_ids())


def test_live_mint_refuses_a_packet_that_does_not_pin_a_read():
    index, packet, _, reader = _synthetic_world()
    packet["pinned_objects"] = packet["pinned_objects"][1:]
    with pytest.raises(MintRefusal, match="does not pin"):
        live_mint(index, packet, s3_reader=reader,
                  training_identities=_training_ids())


def test_live_mint_refuses_a_packet_pin_that_disagrees_with_the_index():
    index, packet, _, reader = _synthetic_world()
    packet["pinned_objects"][0]["sha256"] = "0" * 64
    with pytest.raises(MintRefusal, match="disagrees"):
        live_mint(index, packet, s3_reader=reader,
                  training_identities=_training_ids())


def test_live_mint_refuses_without_a_reader():
    index, packet, _, _ = _synthetic_world()
    with pytest.raises(LiveMintForbidden, match="reader"):
        live_mint(index, packet, s3_reader=None,
                  training_identities=_training_ids())


def test_live_mint_refuses_without_the_training_identity_index():
    index, packet, _, reader = _synthetic_world()
    with pytest.raises(MintRefusal, match="training identity index"):
        live_mint(index, packet, s3_reader=reader)


# --------------------------------------------------------------------------
# AWS is IMPOSSIBLE in the test path; no token authorization exists at all
# --------------------------------------------------------------------------

def _forbid_aws(monkeypatch):
    real_import = builtins.__import__

    def no_aws(name, *a, **k):
        if name.split(".")[0] in ("boto3", "botocore"):
            raise AssertionError("AWS must be impossible in test mode")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_aws)


def test_pure_mint_and_live_refusals_touch_no_aws_sdk(monkeypatch):
    _forbid_aws(monkeypatch)
    manifest = mint_phase_a_split(_INDEX, _clean_pool_identities(),
                                  training_identities=_training_ids())
    assert manifest["aggregate_overlap_counts"]["sealed"] == 0
    index, packet, _, reader = _synthetic_world()
    with pytest.raises(LiveMintForbidden):
        live_mint(index, packet, s3_reader=None,
                  training_identities=_training_ids())
    with pytest.raises(MintRefusal):
        live_mint(index, packet, s3_reader=reader)  # no training index
    frozen = live_mint(index, packet, s3_reader=reader,
                       training_identities=_training_ids())
    assert frozen["status"] == "FROZEN"


def test_no_token_authorization_exists():
    """Codex round 34 finding 5: the magic-string authorization is GONE — no
    token constant, no env-var gate; authorization is the protected-environment
    role trust asserted via caller identity."""
    assert not hasattr(mint_mod, "LIVE_AUTHORIZATION_TOKEN")
    assert not hasattr(mint_mod, "LIVE_AUTHORIZATION_ENV")
    source = (_REPO / "scripts/mint_arm2_nomination_split.py").read_text()
    assert "OWNER-AUTHORIZED-ARM2-NOMINATION-LIVE-MINT" not in source
    assert "get_caller_identity" in source and "assumed-role" in source


def test_cli_offline_mints_nothing():
    with pytest.raises(SystemExit, match="mints nothing"):
        main([])


def test_cli_live_fails_closed_without_aws_sdk(monkeypatch):
    """--live outside the protected workflow: with boto3 unavailable it must
    refuse cleanly BEFORE any read, pointing at the protected workflow."""
    _forbid_aws(monkeypatch)
    with pytest.raises((SystemExit, AssertionError)) as excinfo:
        main(["--live"])
    if isinstance(excinfo.value, SystemExit):
        assert "protected workflow" in str(excinfo.value)


# --------------------------------------------------------------------------
# the live-mint packet: complete pins, REAL Deny statements, role + environment
# --------------------------------------------------------------------------

def _packet() -> dict:
    return json.loads(_PACKET_PATH.read_bytes())


def test_live_mint_packet_pins_every_pool_the_mint_reads():
    packet = _packet()
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


def test_packet_policy_denies_are_real_statements():
    """Codex round 34 finding 5 (second half): 'explicitly denied' must be
    POLICY, not an informational list beside an Allow-only document."""
    policy = _packet()["minimal_read_role"]["policy"]
    denies = [s for s in policy["Statement"] if s["Effect"] == "Deny"]
    assert denies, "the policy carries no Deny statements"
    deny_actions = {a for s in denies for a in s["Action"]}
    for required in ("s3:PutObject", "s3:DeleteObject", "s3:ListBucket"):
        assert required in deny_actions, f"{required} is not denied"
    # reads outside the pinned objects are denied via NotResource
    assert any("NotResource" in s and "s3:GetObject" in s["Action"]
               for s in denies), "no NotResource deny for out-of-pin reads"


def test_packet_authorization_is_environment_and_role_not_a_token():
    packet = _packet()
    auth = packet["authorization_mechanism"]
    assert auth["protected_environment"] == "arm2-nomination-mint"
    assert "environment:arm2-nomination-mint" in auth["role_trust"]
    role = packet["minimal_read_role"]
    assert role["role_name"] == "medzen-arm2-nomination-mint-role"
    assert role["role_arn"].endswith(":role/medzen-arm2-nomination-mint-role")
    assert "token" in auth["replaces"].lower()


def test_iam_policy_file_matches_the_packet_policy_exactly():
    """The committed IAM file (what terraform attaches) and the packet's
    reviewed policy must be the SAME document, byte-for-byte semantics."""
    committed = json.loads(
        (_REPO / "platform/iam/medzen-arm2-nomination-mint-role.json").read_bytes())
    assert committed == _packet()["minimal_read_role"]["policy"]


def test_terraform_role_is_dark_and_bound_to_the_protected_environment():
    tf = (_REPO / "infra/arm2_nomination_mint_role.tf").read_text()
    assert 'default     = false' in tf
    assert "environment:arm2-nomination-mint" in tf
    assert "arm2-nomination-mint-exec.yml@refs/heads/master" in tf
    assert "medzen-arm2-nomination-mint-role.json" in tf
