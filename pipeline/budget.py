"""A durable spend ledger, checked before every instance launch.

A per-instance watchdog bounds ONE instance. It says nothing about the fourth
instance in a sequence, or about a stage relaunched after a failure, and a plan
that budgets $6 while enforcing only "4 hours each" is not enforcing $6 -- four
sequential 4-hour g6.xlarge runs are $16.

So the ceiling is enforced where it can actually bind: a ledger that survives
the process, appended to before and after every stage, and a pre-launch check
that refuses when the WORST CASE of the next stage would exceed the ceiling.
Worst case, not expected case: a stage that hangs until its watchdog fires
costs its full watchdog, and a check against the expected cost would authorise
a launch that cannot afford to fail.

The ledger lives in S3 so it outlives any single machine, with a local mirror
so a network failure cannot silently reset the accounting to zero.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

BUCKET = "medzen-speech"
LEDGER_KEY = "candidates/budget/b4-corrected-ledger.jsonl"

# eu-central-1 on-demand, USD/hour.
RATES = {"g6.xlarge": 1.0064, "c6i.2xlarge": 0.34}

# Per-stage watchdogs. Each is the worst case that stage can cost.
WATCHDOG_S = {
    "builder": 1800,          # last build took 8 min
    "base_eval": 1800,        # 385 clips, one arm
    "sweep_run": 2700,        # 100 steps + validation
    "final_run": 10800,       # 600 steps + 6 checkpoint evaluations
}
STAGE_INSTANCE = {
    "builder": "c6i.2xlarge",
    "base_eval": "g6.xlarge",
    "sweep_run": "g6.xlarge",
    "final_run": "g6.xlarge",
}

CEILING_USD = 6.00


def worst_case_usd(stage: str) -> float:
    """What this stage costs if it runs to its watchdog and then dies."""
    if stage not in WATCHDOG_S:
        raise ValueError(f"unknown stage {stage!r}")
    return RATES[STAGE_INSTANCE[stage]] * WATCHDOG_S[stage] / 3600.0


@dataclass
class Ledger:
    entries: list[dict] = field(default_factory=list)

    @property
    def spent_usd(self) -> float:
        return round(sum(e.get("usd", 0.0) for e in self.entries), 4)

    @property
    def remaining_usd(self) -> float:
        return round(CEILING_USD - self.spent_usd, 4)

    def check(self, stage: str) -> dict:
        """Refuse when the worst case of `stage` would breach the ceiling."""
        wc = worst_case_usd(stage)
        ok = (self.spent_usd + wc) <= CEILING_USD
        verdict = {
            "stage": stage, "instance": STAGE_INSTANCE[stage],
            "watchdog_s": WATCHDOG_S[stage],
            "worst_case_usd": round(wc, 4),
            "already_spent_usd": self.spent_usd,
            "remaining_usd": self.remaining_usd,
            "would_total_usd": round(self.spent_usd + wc, 4),
            "ceiling_usd": CEILING_USD,
            "permitted": ok,
        }
        if not ok:
            raise SystemExit(
                f"REFUSING to launch {stage}: worst case ${wc:.2f} on top of "
                f"${self.spent_usd:.2f} already spent would reach "
                f"${self.spent_usd + wc:.2f}, over the ${CEILING_USD:.2f} "
                "ceiling. This is the worst case deliberately -- a stage that "
                "hangs until its watchdog fires costs this much, and a launch "
                "that cannot afford to fail must not start.")
        return verdict

    def record(self, stage: str, seconds: float, instance: str | None = None,
               note: str = "") -> dict:
        inst = instance or STAGE_INSTANCE[stage]
        usd = round(RATES[inst] * seconds / 3600.0, 4)
        e = {"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
             "stage": stage, "instance": inst, "seconds": round(seconds, 1),
             "usd": usd, "note": note}
        self.entries.append(e)
        return e


def load(cli, local_mirror: Path | None = None) -> Ledger:
    """Read the ledger. A missing object is an empty ledger; an UNREADABLE one
    is not -- that would reset the accounting to zero exactly when something is
    wrong."""
    from botocore.exceptions import ClientError
    try:
        raw = cli.get_object(Bucket=BUCKET, Key=LEDGER_KEY)["Body"].read()
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code not in ("NoSuchKey", "404", "NotFound"):
            raise SystemExit(
                f"REFUSING: cannot read the spend ledger ({code}). An "
                "unreadable ledger is not an empty one, and proceeding would "
                "restart the budget at zero.")
        raw = b""
    led = Ledger([json.loads(l) for l in raw.decode().splitlines() if l.strip()])
    if local_mirror and local_mirror.exists():
        mirror = [json.loads(l) for l in
                  local_mirror.read_text().splitlines() if l.strip()]
        if len(mirror) > len(led.entries):
            raise SystemExit(
                f"REFUSING: local ledger mirror has {len(mirror)} entries but "
                f"S3 has {len(led.entries)}. Spend may be unrecorded; "
                "reconcile before launching anything.")
    return led


def append(cli, entry: dict, local_mirror: Path | None = None) -> None:
    """Append durably: S3 first, then the mirror.

    Read-modify-write is not atomic, so this is not safe against concurrent
    writers -- which is why the orchestrator runs stages strictly sequentially
    and never launches two instances at once.
    """
    led = load(cli)
    led.entries.append(entry)
    body = "".join(json.dumps(e) + "\n" for e in led.entries).encode()
    cli.put_object(Bucket=BUCKET, Key=LEDGER_KEY, Body=body,
                   ContentType="application/x-ndjson")
    if local_mirror:
        local_mirror.parent.mkdir(parents=True, exist_ok=True)
        local_mirror.write_bytes(body)
