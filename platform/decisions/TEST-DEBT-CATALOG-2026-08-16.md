# Test-debt catalog — 2026-08-16

State: 81 failing tests after the registry family fix (was 85; the four
`test_a3_validator` failures are resolved by the first-class `data_only`
rung, commit eb9baf1). Every remaining family is cataloged below with a
verified first-failure root cause. None of these touches the running
evaluation campaign or the ASR executor family (374 tests, all green).

## Category A — missing heavy dependencies on this host (20 tests)

`test_b4_training` (11): `ModuleNotFoundError: transformers` at
`pipeline/train_asr.py:883`. `test_stage_execution` (6):
`ModuleNotFoundError: mlflow` at `pipeline/campaign_tracking.py:118`.
`test_eval_launch` (3): `ModuleNotFoundError: torch`.

These are training/tracking suites that ran on the training host or in
the runtime image, never on this laptop. **Recommendation:** mark with
`pytest.importorskip` so absence skips instead of failing (honest: the
suite documents its own environmental requirement), and run them in the
image or training environment where the dependencies exist. No product
defect is hidden here.

## Category B — stale tests against evolved artifacts (52 tests)

- `test_b5_gates` (24): the committed B5 gate report no longer lists
  hausa/igbo/pidgin/swahili/yoruba under `languages`, and
  `gate_state_counts["FAIL"]` is nonzero. The B5 report artifact
  evolved after these tests froze its earlier shape. B5 is blocked
  until the base-model decision record publishes; regenerate the gate
  report and re-freeze these tests as part of B5 reactivation — not as
  a drive-by edit.
- `test_speech_orchestrator` (12) + `test_speech_orchestrator_streaming`
  (16): golden-contract mismatches (first divergence inside the shared
  config loader's error path). The orchestrator's goldens predate later
  registry/config changes. Needs a deliberate golden regeneration with
  review of each diff — the goldens exist precisely to make silent
  contract drift loud, so regenerating them without reading the diffs
  would defeat their purpose.
- `test_full_b6_plan`, `test_b6_cpu_scale_zero`,
  `test_b6_predeployment_boundaries` (2), `test_llm_contract`,
  `test_b2_ingest`, `test_b6a_local_engineering_006` (6 total, various):
  same class — assertions about plan/config texts that changed after
  the tests froze them.

## Category C — hash-pinned historical packets (2 tests)

`test_b6a_auth_003c_d` (2): asserts SHA-256 of repo files equal values
frozen when packet 003C-D was authored; the files have legitimately
changed since. These tests conflate "the packet was valid then" (a
property of committed history) with "the file never changes" (false by
design). **Recommendation:** repoint them at the packet's committed
evidence rather than the live worktree, mirroring how the ASR packet
tests handle supersession.

## Suggested order of work

1. Category A (mechanical `importorskip`, ~30 min, zero risk).
2. Category C (2 tests, small, clarifies a principle).
3. Category B orchestrator goldens (needs focused review time).
4. Category B b5_gates — bundle into B5 reactivation after the
   base-model decision record.
