# Trainer base image: decision and evidence

Recorded 2026-07-30. Review with the CVE allowlist by **2026-10-28**.

## Decision

`python:3.12-slim-trixie@sha256:cab2dbf575e971934a81e4622f5aba17aa7929719bd7e31033a3a83b97fd0464`

with the CVE exceptions in [`cve_allowlist.json`](cve_allowlist.json), enforced by an
exception-aware gate. `SCAN_MAX_CRITICAL` and `SCAN_MAX_HIGH` remain **0**, so any
finding not named in the allowlist still fails the build.

## What was measured, not assumed

Two full images were built, pushed and scanned by ECR. Both were rejected by the
gate and both remain `adoptable: false`:

| Tag | Base | Digest | ECR findings | Outcome |
|---|---|---|---|---|
| `2aa5323b…` | bookworm | `sha256:763319fd…46530` | 3 CRITICAL, 5 HIGH, 3 MEDIUM | exit 34, not adoptable |
| `d44cd40b…` | trixie | `sha256:20befc20…5ffdc` | 4 CRITICAL, 8 HIGH, 3 MEDIUM | exit 34, not adoptable |

Raw findings are committed under [`evidence/`](evidence/).

### The base upgrade did not help, and that is the key finding

The perl CVE set is **byte-identical** between Debian 12's perl 5.36.0-7+deb12u3 and
Debian 13's perl 5.40.1-6 — the same ten CVEs, none fixed in either. These are
unfixed **upstream** perl issues, so no Debian base clears them. Trixie additionally
surfaced glibc (1 CRITICAL, 1 HIGH) and sqlite3 (2 HIGH).

**All 15 findings across both images are `fix_available: false`.** There is nothing to
upgrade to.

### Why the affected packages cannot simply be removed

| Package | Status | Why it stays |
|---|---|---|
| `perl-base` | `Essential: yes` | Removing it breaks dpkg/apt |
| `util-linux` | `Essential: yes` | Same |
| `glibc` | unavoidable | PyTorch publishes only manylinux/glibc wheels; there are no musl builds, so Alpine cannot run this trainer |
| `sqlite3` | in use | MLflow's tracking backend |

`CRITICAL=0` is therefore **not currently reachable for any glibc-based
PyTorch-capable image** under ECR basic scanning.

## Minimal and distroless bases were evaluated, not dismissed

Measured locally with `docker scout` (cheap — no 6.18 GB build), then inspected:

| Base | Python | bash | sh | pip | scout CRITICAL/HIGH |
|---|---|---|---|---|---|
| `python:3.12-slim-trixie` | 3.12.13 | yes | yes | yes | 1 / 2 |
| `python:3.13-slim-trixie` | 3.13.x | yes | yes | yes | 1 / 2 |
| `python:3.12-slim-bookworm` | 3.12.x | yes | yes | yes | 1 / 2 |
| `gcr.io/distroless/python3-debian12` | **3.11.2** | no | no | no | 0 / 0 |
| `cgr.dev/chainguard/python:latest` | **3.14.6** | no | no | no | 0 / 0 |

Both zero-finding options were **rejected on architecture, not on convenience**:

1. **No shell.** `container_entrypoint.sh` and `bootstrap_trainer.sh MODE=verify`
   cannot execute. Those scripts are the single shared authority for the torch pin,
   the import gate, the stale-package check and `pip check` across BOTH the EC2 venv
   path and the container. Reimplementing them in Python would create a second copy
   of the gates, and "two copies drift, one file cannot" is precisely why they were
   consolidated.
2. **Interpreter change.** Distroless moves back to Python 3.11, Chainguard forward
   to 3.14 — where torch 2.13.0 likely publishes no `cp314` wheels, so the pinned
   install would fail outright. The validated environment is 3.12.
3. **Unverified against the authoritative scanner.** The 0/0 above is Docker Scout,
   not ECR. Those databases disagree materially (see below), and distroless-debian12
   still contains glibc.

Revisiting distroless is reasonable **after** B7, when the serving images are built
and a Python-native gate implementation can be shared across both. It is not a
cheap swap today.

## The scanners disagree, and ECR is the authority

For the same `python:3.12-slim-trixie`:

- **ECR basic scanning:** 15 findings — perl, glibc, sqlite3, util-linux
- **Docker Scout:** 3 findings — perl only

Scout did not report the glibc CRITICAL that ECR did. "Clean" is therefore
scanner-dependent, and the gate uses ECR because that is what actually guards the
registry we deploy from. Scout is used only as a cheap pre-filter.

A further limit worth stating plainly: **ECR basic scanning covers OS packages only.**
It examined none of the 163 installed Python packages. That gap is covered separately
by [`../pipeline/audit_python_deps.sh`](../pipeline/audit_python_deps.sh), which runs
`pip-audit` at build time against an exported freeze and reported
**0 vulnerabilities** for this image.

## What was explicitly not done

- **Thresholds were not lowered.** `SCAN_MAX_CRITICAL=0`, `SCAN_MAX_HIGH=0`.
  Exceptions are per-CVE, justified, and expiring.
- **Bookworm was not chosen despite its shorter list.** It reported no glibc or
  sqlite3 findings, but glibc 2.36 is unlikely to be genuinely unaffected — more
  probably those CVEs are not yet triaged for Debian 12 in ECR's data. Selecting the
  base with fewer *reported* findings would be gaming the scanner.
- **Amazon Inspector was not enabled.** It would cover Python packages, but it is an
  account-wide setting affecting every repository, and the build-time `pip-audit`
  gate addresses the same gap without that blast radius.
