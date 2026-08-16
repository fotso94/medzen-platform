# B8 rollback runbook — canary triggers and the alias restore

Rollback is an **alias switch, never an image rebuild**: the SSM
parameter's version history is the rollback evidence, restore is a new
version carrying the previous value, and the whole operation takes
seconds. This runbook is drilled once for real before any production
traffic (activation checklist item 6); until that drill passes, the
rollback execution stays human-triggered.

## Triggers (§A5, wired in infra/b7_canary_alarms.tf, dark until activation)

| Alarm | Condition | Meaning |
|---|---|---|
| error-rate | target 5XX > 2% of requests for 5 consecutive minutes | the canary is failing requests |
| p95-latency | p95 > 1.5x recorded baseline for 10 consecutive minutes | the canary is degrading service |
| readiness | any orchestrator target unhealthy for 3 minutes | the canary cannot hold traffic |

Any alarm entering ALARM state pages through the
`medzen-b7-canary-triggers` SNS topic via EventBridge.

## Response procedure

1. **Acknowledge the page.** Confirm which alarm fired in CloudWatch;
   one glance at the alarm name identifies the §A5 trigger class.
2. **Plan the rollback (read-only):**

   ```bash
   python scripts/b7_alias_rollback.py --parameter <alias-parameter>
   ```

   The dry-run prints current vs previous version and the exact value
   that would be restored. The script refuses: the production pointer
   (`/medzen/registry/serving/current` — packet-only), anything outside
   `/medzen/registry/`, single-version parameters, and no-op restores.
3. **Execute:**

   ```bash
   python scripts/b7_alias_rollback.py --parameter <alias-parameter> --execute
   ```

   The restore is a NEW parameter version (history intact) and the
   script verifies the readback matches before reporting PASS.
4. **Verify recovery**: the same alarms must return to OK within their
   evaluation windows; the orchestrator's version endpoint must report
   the restored artifact identity.
5. **Record**: commit the script's JSON output plus the alarm timeline
   as an evidence record; open the incident review that decides whether
   the promoted artifact is withdrawn or repaired.

## Why execution is not yet unattended

Wiring an unattended mutation before the drill would automate an
untested path. After the first successful real drill (its receipts
committed), a follow-up packet may connect EventBridge to an automation
target executing step 3 — with the same refusal guards compiled in.
