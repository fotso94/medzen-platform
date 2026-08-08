from __future__ import annotations

import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SAMPLER = ROOT / "scripts/b6a_003c_e_ssm_sampler.sh"


def test_003c_e_sampler_is_the_exact_interactively_proven_source() -> None:
    assert hashlib.sha256(SAMPLER.read_bytes()).hexdigest() == (
        "b6aa0e0621fca7fc6ee9e9a2bb9f59ff543efbb71b06a35e5497919d8a573d96"
    )
    text = SAMPLER.read_text()
    assert "/usr/local/bin/nerdctl" in text
    assert "--namespace k8s.io" in text
    assert "crictl" not in text
    assert "iteration < 120" in text
    assert "samples -eq 120" in text
    assert "/busybox/chroot /driver-root /usr/bin/nvidia-smi" in text
