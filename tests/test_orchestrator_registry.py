from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SERVICE_ROOT = ROOT / "services/speech-orchestrator"
sys.path.insert(0, str(SERVICE_ROOT))

from medzen_speech_orchestrator.registry import (  # noqa: E402
    LocalParameterStore,
    RegistryRefusal,
    RegistryRouter,
)


FIXTURE = ROOT / "platform/testdata/registry-ssm/b6-local-v1.json"


def fixture_value() -> dict:
    return json.loads(FIXTURE.read_bytes())


def fixture_root(value: dict | None = None) -> str:
    value = value or fixture_value()
    manifests = [
        item["Name"] for item in value["parameters"]
        if item["Name"].endswith("/_manifest")
    ]
    assert len(manifests) == 1
    return manifests[0].removesuffix("/_manifest")


def write_fixture(tmp_path: Path, value: dict) -> Path:
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
    return path


def test_fixture_is_current_ssm_shaped_and_content_addressed():
    subprocess.run(
        [sys.executable, "scripts/generate_b6_orchestrator_fixtures.py", "--check"],
        cwd=ROOT,
        check=True,
    )
    value = fixture_value()
    root = fixture_root(value)
    snapshot = root.rsplit("/", 1)[1]
    assert root == f"/medzen/registry/test/b6/{snapshot}"
    assert len(snapshot) == 64
    assert all(set(item) == {"Name", "Type", "Value", "Version"}
               for item in value["parameters"])
    assert all(item["Type"] == "SecureString" and item["Version"] == 1
               for item in value["parameters"])
    router = RegistryRouter(LocalParameterStore(FIXTURE), root)
    assert router.snapshot_sha256 == snapshot
    assert router.resolve(None).alias == "english"
    assert router.resolve("en") == router.resolve("eng")
    assert router.resolve("english").response_code == "en"


def test_fixture_contains_no_serving_alias_or_model_adoption_fields():
    raw = FIXTURE.read_text()
    for forbidden in (
        "/medzen/registry/serving/current",
        "approved_version",
        '"artifact"',
        "production",
    ):
        assert forbidden not in raw


def test_parameter_hash_tamper_fails_closed(tmp_path: Path):
    value = fixture_value()
    route = next(item for item in value["parameters"] if "/routes/" in item["Name"])
    decoded = json.loads(route["Value"])
    decoded["llm"]["model_version"] = "unbound-model"
    route["Value"] = json.dumps(decoded, sort_keys=True, separators=(",", ":"))
    path = write_fixture(tmp_path, value)
    with pytest.raises(RegistryRefusal, match="hash mismatch"):
        RegistryRouter(LocalParameterStore(path), fixture_root(value))


def test_missing_and_unexpected_parameters_fail_closed(tmp_path: Path):
    missing = fixture_value()
    missing["parameters"] = [
        item for item in missing["parameters"] if "/routes/" not in item["Name"]
    ]
    with pytest.raises(RegistryRefusal, match="missing"):
        RegistryRouter(
            LocalParameterStore(write_fixture(tmp_path, missing)), fixture_root(missing)
        )
    unexpected = fixture_value()
    extra = dict(unexpected["parameters"][-1])
    extra["Name"] = fixture_root(unexpected) + "/unexpected"
    unexpected["parameters"].append(extra)
    with pytest.raises(RegistryRefusal, match="unexpected"):
        RegistryRouter(
            LocalParameterStore(write_fixture(tmp_path, unexpected)),
            fixture_root(unexpected),
        )


def test_unknown_language_never_falls_back_silently():
    router = RegistryRouter(LocalParameterStore(FIXTURE), fixture_root())
    with pytest.raises(RegistryRefusal, match="not present"):
        router.resolve("fr")


def test_root_and_snapshot_material_are_cryptographically_bound():
    value = fixture_value()
    manifest_parameter = next(
        item for item in value["parameters"] if item["Name"].endswith("/_manifest")
    )
    manifest = json.loads(manifest_parameter["Value"])
    root_hash = fixture_root(value).rsplit("/", 1)[1]
    source = json.loads((
        ROOT / "platform/testdata/registry-ssm/source/b6-local-v1.json"
    ).read_text().replace(
        "$B6_ORCHESTRATOR_AUDIO_SHA256",
        json.loads((
            ROOT / "platform/testdata/orchestrator/asr-fixture.json"
        ).read_bytes())["audio_sha256"],
    ))
    calculated = hashlib.sha256(
        json.dumps(source, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert root_hash == calculated == manifest["snapshot_sha256"]


def test_non_versioned_or_production_roots_are_rejected():
    store = LocalParameterStore(FIXTURE)
    for root in (
        "/medzen/registry/test/b6/current",
        "/medzen/registry/serving/current",
        "/medzen/registry/test/b6/" + "A" * 64,
    ):
        with pytest.raises(RegistryRefusal, match="versioned B6 test"):
            RegistryRouter(store, root)
