from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "services/asr-runtime/Dockerfile"
STANDARD = ROOT / "platform/standards/runtime-image-hardening-v1.md"


def _stages():
    text = DOCKERFILE.read_text()
    builder, runtime = text.split(
        "FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04@sha256:"
        "ac55d124da4882b497f732d8dfd9a702d5447a5f29d08d56da6f64f0a1eb34bc "
        "AS runtime",
        1,
    )
    return text, builder, runtime


def test_asr_uses_two_digest_pinned_named_stages():
    text, _, _ = _stages()
    pinned = (
        "nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04@sha256:"
        "ac55d124da4882b497f732d8dfd9a702d5447a5f29d08d56da6f64f0a1eb34bc"
    )
    assert text.count(f"FROM {pinned}") == 2
    assert "AS asr-builder" in text
    assert "AS runtime" in text


def test_venv_bootstrap_and_pip_are_confined_to_builder():
    _, builder, runtime = _stages()
    assert "python3.12-venv" in builder
    assert "pip install" in builder
    assert "python3.12-venv" not in runtime
    assert "pip install" not in runtime
    assert "requirements.txt" not in runtime
    assert "COPY --from=asr-builder /opt/venv /opt/venv" in runtime


def test_final_stage_preserves_security_and_serving_contract():
    _, _, runtime = _stages()
    for required in (
        "ca-certificates libsndfile1 python3.12",
        "USER 10001:10001",
        "HF_HUB_OFFLINE=1",
        "MEDZEN_INFERENCE_DEVICE=cuda",
        "uvicorn",
    ):
        assert required in runtime
    for forbidden in ("COPY artifacts", "git ", "gcc", "build-essential"):
        assert forbidden not in runtime


def test_active_standard_requires_local_and_automatic_remote_gates():
    text = " ".join(STANDARD.read_text().split())
    for required in (
        "ACTIVE FOR NEW SERVING IMAGE WORK",
        "Pin every base image by SHA-256 digest",
        "fixed non-root UID and GID",
        "read-only root filesystem",
        "automatic ECR scan",
        "zero critical and zero high findings",
        "deployable child digest",
        "never overrides an automatic ECR failure",
    ):
        assert required in text
