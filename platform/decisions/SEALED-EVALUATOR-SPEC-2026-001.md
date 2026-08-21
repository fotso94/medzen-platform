# SEALED-EVALUATOR-SPEC-2026-001 — acceptance criteria for the pilot's sealed evaluator

Source: Codex reviews #13-#14 (every reproduced bypass and gap becomes a
requirement). No sealed evaluation may run until an evaluator meeting ALL
of these ships with tests; `launch_sealed_eval.py launch` refuses until
then — the hold is enforced in code, not policy.

1. STRUCTURED COMPOSITION, not arbitrary user-data: the evaluator is
   generated from the packet's typed fields; there is no free-form script
   to smuggle a different holdout through (kills acquire-A/read-B — a
   comment mentioning holdout A must be worthless).
2. GIT-BLOB MATERIALIZATION: the exact bytes verified at git HEAD are
   materialized to a temp file and THOSE bytes go to EC2 (kills the
   working-tree TOCTOU).
3. EVERY BINDING VALIDATED AND USED: holdout S3 VersionId fetched via
   that VersionId; image digest pinned in the run; results written ONLY
   under the packet's results prefix; all three refused when malformed.
4. OWNER-AUTHORIZED PACKET: the packet sha256 must appear in a committed
   owner-authorization record (ledger-v5 discipline) — committed ≠
   approved.
5. BUDGET + WATCHDOG: a cost ceiling validated pre-launch and an
   EXTERNALLY enforced termination (scheduled check that kills the
   instance past max_hours) — a tag is not a watchdog.
6. STORAGE: explicit block-device mapping — sized for models + audio,
   KMS-encrypted with the project key, delete-on-termination.
7. EXACTLY-ONCE ACROSS CONTROLLERS: distributed conditional consumption
   (S3 conditional write, as the budget mechanism already demonstrates)
   or a formally designated single controller with enforcement.
8. IDEMPOTENT LAUNCH: client tokens + ambiguous-launch recovery.
9. REHEARSAL: a complete successful-launch rehearsal (stubbed AWS) in CI
   covering packet load, durable acquisition, composition, and launch
   ordering — not only refusal paths.
