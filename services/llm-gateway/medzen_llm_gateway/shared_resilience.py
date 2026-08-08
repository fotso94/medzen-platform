"""Load the repository's single A6 resilience implementation without
shadowing Python's standard-library ``platform`` module.

The eventual container build must copy ``platform/lib/resilience.py`` beside
this service and preserve this exact binding; B6.2 itself remains local-only.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[3] / "platform/lib/resilience.py"
SPEC = importlib.util.spec_from_file_location("medzen_shared_resilience", SOURCE)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("shared resilience implementation cannot be loaded")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

CircuitBreaker = MODULE.CircuitBreaker
State = MODULE.State
load_config = MODULE.load_config
