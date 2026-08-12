from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import asr_eval_scout_preflight as preflight


def test_real_execution_preflight_is_zero_aws_and_uses_actual_archive_scan() -> None:
    source = inspect.getsource(preflight.qualify)
    assert "export_exact_image" in source
    assert "OciLayout" in source
    assert "scan_archive_with_scout" in source
    assert '"aws_calls": 0' in source
    assert '"aws_mutations": 0' in source
    assert '"gpu_started": False' in source


def test_preflight_requires_committed_bindings_and_clean_worktree() -> None:
    source = inspect.getsource(preflight.qualify)
    assert "read_committed_artifact" in source
    assert '"status", "--porcelain=v1"' in source
    assert "SCOUT_PREFLIGHT_WORKTREE_DIRTY" in source
