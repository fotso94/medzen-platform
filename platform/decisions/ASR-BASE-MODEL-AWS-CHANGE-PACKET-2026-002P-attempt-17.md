# ASR base-model AWS change packet 2026-002P — numeric staging and audited-workload successor attempt 17

Status: **DRAFT — INDEPENDENT REVIEW AND EXACT OWNER APPROVAL REQUIRED — NOT EXECUTABLE**

## Required approval phrase

> Approve ASR base-model AWS change packet 2026-002P only, authorizing numbered attempt 17 for one non-transferable 10,800-second offline evaluation attempt within a fresh $10 ceiling and continuing ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-002 at SHA-256 06189414e82c7e497fe7b45d5395af0f03de523bc54c17e1b1e3ae91229d744c.

This draft authorizes nothing. After independent review PASS and the exact
owner phrase, write-once `ASR-BASE-MODEL-AWS-AUTH-2026-002P` must bind the
final packet SHA-256. A committed, read-only
`deadline_identity_and_acceptance` dry validation must then PASS against the
actual authorization, bindings and packet before any AWS call or attempt
envelope.

## Attempt-16 refusal and zero state

Attempt 16 is consumed. The proven sampler returned 120 numeric readings and
the DRA reached Ready. Node staging then refused because five staging calls
used `sudo -u '#10001'`; Amazon Linux interpreted `#10001` as a literal account
name and returned `sudo: unknown user #10001`. No model or audio reached the
node. Status-keyed cleanup restored GPU desired size zero and removed every
temporary endpoint, volume, security group and Kubernetes object. Observed GPU
lifetime to termination request was approximately 359 seconds.

- refusal SHA-256:
  `4f32505301c25d57510465db35edd213c498834a7f1d12a7a12b0a3cf7d6f025`;
- cost observation SHA-256:
  `266063399cfba297d1cc01fa538e3380c24b51bf0df95a6734dd9e7e8d3ec92f`.

Both records and all attempt-1-through-16 history remain write-once.

## Numeric-identity staging fix and complete staging audit

All five name-resolving invocations were removed. The single canonical wrapper
is now:

`/usr/bin/sudo /usr/sbin/chroot --userspec=10001:10001 / /usr/bin/env -i HOME=/tmp PATH=/usr/local/bin:/usr/bin:/bin /bin/sh -ec <script>`

The shared staging module constructs the wrapper once and all live/rehearsal
call sites consume it. The complete staging audit found and closed:

1. five account-name lookups — now one numeric UID/GID transition;
2. inherited environment — now `env -i` with explicit `HOME` and `PATH`;
3. relative executables — now absolute and preflight-checked;
4. shell ambiguity — now an explicit Bash prelude and strict error mode;
5. URL/command lifetime mismatch — URLs are valid 3,600 seconds against a
   1,800-second SSM timeout plus a 600-second safety margin;
6. post-model diagnostic leakage — pre-model failure may retain bounded safe
   stderr; post-model failure retains only hashes and typed reason codes.

The command bundle has nine audited commands, zero account-name lookups, zero
ambient environment reads and zero relative staging executables.

## Node-equivalent failure/fix qualification

The local qualification used the pinned Amazon Linux 2023 OCI index
`sha256:6d8e068b91f351df5bf6acd4bd261316e42747ad4bae76689ff6f4939e2180a2`
and linux/amd64 child
`sha256:47821fb77b737fb67c93e451c0953e7d3325ee9d41f8d3ecc799fd9b96e6ca9c`,
matching the target node's AL2023 userspace and architecture.

It reproduced the exact attempt-16 refusal with no passwd entry for UID 10001,
then proved the fixed path for numeric identity, controlled environment,
directory creation, copy/hash readback, multipart concatenation, extraction,
base64 receipt creation and numeric ownership. Each test container was
destroyed. The qualification made zero AWS and Kubernetes calls and cost $0.

- qualification evidence SHA-256:
  `988463400ccf1b9d00128b20cb4176ebeb7819efe6de462e5085b1f64c53cc2d`.

## Attempt-1-through-16 lessons applied to the last never-run command

The pilot Kubernetes workload had no non-rehearsal PASS receipt, so it remains
`NOT_HISTORICALLY_PROVEN`. The review-blocking audit applies every prior lesson
before its first live execution. In particular it now has:

- unconditional source/hash bindings and a persisted NUL-delimited argv hash;
- the same renderer, verifier and stage composition in live and rehearsal;
- exact scan-passed image digest and frozen, node-local, read-only inputs;
- explicit `HOME`, `PATH`, offline caches and numeric UID/GID 10001;
- three absolute `/opt/venv/bin/python` invocations and final-process `exec`;
- a 900-second monotonic listener timeout with distinct exit code 71;
- a 9,000-second Kubernetes active deadline and matching bounded wait;
- network probe before model import and fail-closed cross-pod isolation proof;
- bounded Job, Pod, Event and log diagnostics persisted before cleanup;
- external-workdir-only evidence and status-keyed cleanup.

No successful historical receipt is invented. The workload command will
produce its first-live identity and terminal receipt if separately approved.

- lessons audit SHA-256:
  `52171824d1a2d24dddf5bc0411cba5295e4dafa4358c80792e020552ee1a7236`;
- canonical workload argv SHA-256:
  `64255a2fc39890d5145937f59b1b66bf3b33a2634d43fa39d2303810c7f58997`.

## Cost reconciliation for attempts 11–16

Read-only Cost Explorer reconciliation used the named `medzen` profile and
confirmed account `558069890522`. AWS currently exposes a single estimated
August 13 daily g6.xlarge pool, not hourly or per-attempt data:

- actual reported usage: `0.031389` g6.xlarge hours (`113.0004` seconds);
- gross usage: `$0.0315898896`;
- credit: `-$0.0315898896`;
- reported net: `$0.00`;
- immutable receipt intervals: `1,383` seconds total for attempts 11–16;
- per-attempt dollar allocation: `NOT_AVAILABLE` and not fabricated.

The 1,270-second difference is recorded as an estimated/different-metering
boundary. Shared endpoint, EBS and transfer lines are not attributed because
the daily account rows include unrelated activity. Credits do not release
guardrail headroom; all six $10 ceilings remain conservatively recognized.

- reconciliation SHA-256:
  `bd4bfff912fd93b96a7336b0a564c8e329e38cdc08f5ae6b373243e2cc58153e`;
- `COST-REGISTRY-2026-012` SHA-256:
  `747b8d47d7aa57029c9d10f24be8eb6a9656ff9cd6911391c3831ad196f3b972`;
- project ceiling: `$300`;
- recognized committed guardrail: `$134.4286064216`;
- active reservations: `$0`;
- headroom before this request: `$165.5713935784`;
- requested attempt-17 ceiling: `$10`;
- headroom if approved: `$155.5713935784`.

This reconciliation is not audited financial advice and requires owner or
qualified-finance review.

## Unchanged security, data and image boundaries

Unchanged and hash-bound:

- qualified image OCI index:
  `sha256:506d6dd5933854fade34a05d5dfe6a35be7b97dc54da541f0814a3d3e4a6b2aa`;
- linux/amd64 child:
  `sha256:85a82f348f6157adb36016d5b8d6155866ee0c4d40ae1faf4d80df677d50d14e`;
- local tag: `medzen-asr-eval-runtime:pilot-5d1b8a0`;
- risk acceptance SHA-256:
  `06189414e82c7e497fe7b45d5395af0f03de523bc54c17e1b1e3ae91229d744c`;
- four accepted offline-only PyTorch HIGH tuples and no extras;
- ECR Basic supplementary OS gate at zero critical/high;
- frozen 540-row, 47-language pilot and three candidate models;
- pre-staged 13,116,686,091-byte create-only bundle with in-attempt upload zero;
- default-deny execution with S3/ECR endpoint-only egress, no inbound network,
  no PHI, no untrusted inputs and container destruction after the window;
- pre-envelope local-resource gate including the 40-GiB free-space floor.

The risk exception remains offline-evaluation-only and non-precedential for
serving. No image, model, risk tuple, frozen input or pre-staged object changes.

## Exact execution scope

The exact plan contains no permanent create or update. It contains 18
temporary create/delete entries, one bounded GPU scale-to-one-then-zero change,
one GPU maximum, a 60-GiB encrypted temporary volume and the existing proven
DRA image/manifest. No image or artifact upload occurs.

Only after review and exact authorization:

1. attempt 17 only, 10,800 seconds, one GPU node, fresh $10 ceiling;
2. pre-envelope local-resource gate and committed real-artifact stage-1 dry
   validation;
3. digest rescan and verify-only pre-staged artifact gate;
4. temporary endpoints, strict network isolation and DRA;
5. proven sampler, numeric node staging, then the audited 540-row pilot;
6. immediate receipts and zero-state cleanup on every outcome.

Prohibited: attempt reuse or extension; IAM/KMS changes; registry-wide scanning;
internet egress; inbound routes; PHI or untrusted inputs; training; serving;
promotion; `approved/asr`; production SSM; MLflow registration; registry
language mutation.

## Local verification and receipt-last rehearsal

The focused hardening and cost suites pass `12/12`. The broader packet suite
and final counts are populated only after the packet/bindings are committed.

The cold rehearsal executes real `LiveOperations`, real local filesystem
composition and the same node-staging/workload code as live. Only paid
AWS/kubectl and scanner boundaries are faked with hash-bound recorded-real
shapes. In addition to all standing scenarios, it includes:

- exact attempt-16 unknown-user reproduction with diagnostics before cleanup;
- corrected numeric-staging PASS;
- pilot-workload refusal with Job/Pod/Event/log diagnostics before cleanup;
- clean PASS, deadline and cleanup paths.

- executor source commit:
  `8929fe85326919a10bc30c8f6740139846814702`;
- 25 unconditionally bound executor modules;
- final bindings SHA-256:
  `5779875cd1c85fd486c43f1e93759f99206c19a317032e95c05087bb80e28d54`;
- final receipt-last cold-rehearsal SHA-256: `PENDING_RECEIPT_LAST`.

## Post-approval order

1. write and commit authorization 002P;
2. run and commit the real-artifact stage-1 dry validation;
3. remeasure the pre-envelope host gate;
4. only on PASS create the attempt envelope and execute once;
5. persist every stage receipt and always clean up;
6. commit terminal evidence and reconcile finalized billing later.

## Deviations and limitations

The node-equivalent qualification uses a pinned Amazon Linux 2023 userspace
container, not the exact EKS AMI kernel, kubelet, driver or mounted filesystem.
Those boundaries remain fail-closed and first-live.

AWS Cost Explorer does not provide enabled hourly data for this account. The
daily pool is therefore recorded without a fabricated per-attempt split, while
the conservative guardrail remains higher than the actual observed pool.

The workload command has never run live. Its complete lessons audit, local
composition and failure rehearsal reduce risk but do not reclassify it as
historically proven. There are no silent deviations.
