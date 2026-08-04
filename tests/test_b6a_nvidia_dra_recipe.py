from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/build_b6a_nvidia_dra.sh"
PATCH = ROOT / "platform/dependencies/nvidia-dra-v0.4.1/medzen-security.patch"
EVIDENCE = ROOT / "platform/evidence/B6A-NVIDIA-DRA-LOCAL-2026-001.json"


def test_recipe_is_source_pinned_local_only_and_fail_closed():
    text = SCRIPT.read_text()
    assert "764900e7833798a1528bd52a2af3e1b2d5f7a616" in text
    assert "09ceee5dde66ba9ce25c7cc69b1ebd5e6e3266fa" in text
    assert "sha256:f92b729f5f76b045df75ee1cb324ea68658bbc82feecd286c6ce08bf339fd74d" in text
    assert "golang.org/x/net@v0.55.0" in text
    assert "google.golang.org/grpc@v1.82.1" in text
    assert "--exit-code --only-severity critical,high" in text
    assert "buildx build --platform linux/amd64 --load" in text
    assert "docker push" not in text
    assert "kubectl" not in text
    assert "aws " not in text


def test_patch_pins_runtime_bases_and_replaces_vulnerable_helper():
    text = PATCH.read_text()
    assert "d60df2cd85036996175abc357b530d59675b598c6e585403c01f9ec7ead0daf1" in text
    assert "3be83724bcda99b72307e8d3cea256b3cfa5678b5198c1351bf66d2bc60d9cf9" in text
    assert "COPY /medzen/nvidia-cdi-hook" in text
    assert "FROM ${TOOLKIT_CONTAINER_IMAGE} AS toolkit" in text


def test_recipe_binds_reproducible_cdi_helper_hash():
    text = SCRIPT.read_text()
    expected = "ebbe5839a703c5aa16796bdbadbea68439831f832660de49fc5c0fb70863c5b6"
    assert f'EXPECTED_HOOK_SHA256="{expected}"' in text
    assert len(bytes.fromhex(expected)) == hashlib.sha256().digest_size


def test_evidence_binds_recipe_patch_and_zero_finding_scan():
    evidence = json.loads(EVIDENCE.read_text())
    assert evidence["recipe"]["script_sha256"] == hashlib.sha256(
        SCRIPT.read_bytes()
    ).hexdigest()
    assert evidence["recipe"]["patch_sha256"] == hashlib.sha256(
        PATCH.read_bytes()
    ).hexdigest()
    scan = ROOT / evidence["verification"]["scan_path"]
    assert evidence["verification"]["scan_sha256"] == hashlib.sha256(
        scan.read_bytes()
    ).hexdigest()
    assert evidence["verification"]["critical"] == 0
    assert evidence["verification"]["high"] == 0
    assert evidence["waiver"]["used"] is False
