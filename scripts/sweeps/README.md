# HISTORICAL / PROVENANCE-ONLY executors

These scripts are byte-exact copies of the EC2 user-data that actually ran
(Codex review #12: warnings prepended in-file broke the byte-match; they
now live HERE, in sidecar metadata).

- `av-ingest-userdata.sh` — as run by boxes r2/r3/r4 (the r1 box ran a
  pre-EXIT-trap variant of the same script; r1 died to an EC2 host
  failure before the trap existed).
- `v2-sweep-userdata.sh` — the 20-checkpoint dev-selection sweep box.
- `v2-sealed-userdata.sh` — the aborted sealed-run box (never produced
  results; see B5-KW-V2-FUTILITY-2026-002).

NOT SAFE FOR REUSE AS-IS: unversioned S3 reads, mutable trainer-image
tag in the sweep scripts, overwrite-capable result uploads, and no
sealed-gate integration. Any future sealed evaluator MUST be started via
`scripts/launch_sealed_eval.py`, which acquires the ledger consumption
ATOMICALLY before any selection or audio read.
