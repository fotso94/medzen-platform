# ASR base-model AWS change packet 2026-002Q — derived endpoint-policy successor attempt 18

Status: **DRAFT — INDEPENDENT REVIEW AND EXACT OWNER APPROVAL REQUIRED — NOT EXECUTABLE**

## Required approval phrase

> Approve ASR base-model AWS change packet 2026-002Q only, authorizing numbered attempt 18 for one non-transferable 10,800-second offline evaluation attempt within a fresh $10 ceiling and continuing ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-002 at SHA-256 06189414e82c7e497fe7b45d5395af0f03de523bc54c17e1b1e3ae91229d744c.

This draft authorizes nothing. After independent review PASS and the exact
owner phrase, write-once `ASR-BASE-MODEL-AWS-AUTH-2026-002Q` must bind this
packet's final SHA-256. The committed read-only
`deadline_identity_and_acceptance` validation must PASS against the real
authorization, bindings and packet before any AWS call or attempt envelope.

## Attempt-17 refusal and preserved progress

Attempt 17 is consumed. Eight of eleven live stages passed: the DRA became
Ready, the sampler returned numeric readings, and numeric-UID node staging
reached the first version-pinned S3 download. That download returned HTTP 403
because its `VersionId` changes the required endpoint-policy action from
`s3:GetObject` to `s3:GetObjectVersion`. Cleanup restored zero GPU desired
capacity and removed every temporary resource. No evaluation row ran.

- refusal SHA-256: `7c13b45fa917ed0db79114ef15518959e7e8a58cb11b7ac614a3bf2d2ddbe102`;
- observed GPU lifetime: 331 seconds;
- sampler and numeric-UID staging are now live-proven;
- all attempt-1-through-17 records remain write-once.

## Machine-derived endpoint policy

The handwritten endpoint action list is eliminated. The executor now records
every private-path S3 and ECR call, including operation, exact resource,
execution path and parameters. For S3 `GetObject`, the inventory records
whether a non-empty `VersionId` is present and stores only its SHA-256, never
the raw version value. Policy actions and resources are derived from this
inventory and validated before endpoint creation. Node staging independently
collects the requests it is about to issue and must match the same inventory
exactly before SSM is called.

The bound inventory contains 35 calls:

- 8 version-pinned S3 reads requiring `s3:GetObjectVersion` on only
  `arn:aws:s3:::medzen-speech/research/asr-base-model/pilot/1cdca3e75195c7c7417550154e36a1f372715e17efd13c835c87ee503fa84eee/*`;
- 5 unversioned Whisper-v0 S3 reads requiring `s3:GetObject` on only
  `arn:aws:s3:::medzen-speech/b6a/asr/v0/5adf77568813513bc3697a1501ba354c04c7b93ea374fc5407cf4f6402f7431e/*`;
- the existing unversioned `s3:GetObject` ECR-layer redirect on
  `arn:aws:s3:::prod-eu-central-1-starport-layer-bucket/*`;
- 22 ECR calls using the unchanged token and exact-repository pull actions.

The audit found no other S3 version-variant action. Bucket-wide access, a
broader research prefix, list/write/delete actions and any IAM-role change
remain absent. Missing coverage, duplicate observed calls, parameter/action
drift, resource drift or an unknown operation refuses before model staging.

- endpoint-policy qualification SHA-256:
  `cb0129ba36865f79ed16a1951679ccee1c6d68af4811f4b872c57a6149db1cab`;
- qualification: 35/35 calls covered, 0 uncovered, 0 AWS calls, $0;
- handwritten endpoint action lists permitted: false.

## Cost reconciliation and requested ceiling

Cost Explorer was queried read-only after attempt 17. AWS has not yet ingested
a separately attributable August 14 g6.xlarge row, so current-day zero is not
reported as actual cost and no per-attempt allocation is fabricated. The full
attempt-17 $10 ceiling remains conservatively committed.

- reconciliation SHA-256:
  `a170d02aa09d7b78f249f2b4b6df87b666bbbe7ae8e64aef764fff0786b4565a`;
- `COST-REGISTRY-2026-013` SHA-256:
  `6d1c71fa314425108a3035ea8a2b688c7f94e4cd45ad79e0dc5bf79efb2db475`;
- project ceiling: $300;
- recognized committed guardrail: $144.4286064216;
- active reservations: $0;
- headroom before request: $155.5713935784;
- requested attempt-18 ceiling: $10;
- headroom if approved: $145.5713935784.

The reconciliation needs owner or qualified-finance review and must be
refreshed when August 14 billing lands.

## Exact execution scope and unchanged boundaries

Only after review and exact authorization:

1. attempt 18 only, one GPU maximum, 10,800 seconds, fresh $10 ceiling;
2. pre-envelope host-capacity gate and committed real-artifact stage-1 dry run;
3. unchanged digest rescan and verify-only pre-staged bundle;
4. 18 temporary create/delete entries and one bounded scale-to-one-then-zero
   GPU change, with no permanent create or update;
5. exact private endpoints using the derived policies, strict isolation and
   the already proven DRA, sampler and numeric staging;
6. version-authorized downloads and the frozen 540-row, 47-language pilot;
7. immediate stage receipts and status-keyed zero-state cleanup on any result.

Attempt 18's only untested live territory is the version-authorized download
and the 540-row evaluation. The image, three candidate models, 540-row freeze,
pre-staged 13,116,686,091-byte bundle, scan gate, four accepted offline-only
PyTorch findings, endpoint-only egress and destruction-after-window rules are
unchanged. The risk acceptance remains offline-evaluation-only and cannot be
cited for serving.

Prohibited: attempt reuse or extension; IAM/KMS or registry-scanning changes;
internet or inbound access; PHI or untrusted inputs; training; serving;
promotion; `approved/asr`; production SSM; MLflow registration; registry
language changes; or any non-packet AWS mutation.

## Local qualification and cold rehearsal

The final bindings pin executor commit
`25c8cc12d5563d23bad65a7e159f95ac177f59c9`, 27 executor modules and the
endpoint-policy qualification. The receipt-last cold rehearsal must run the
real `LiveOperations` composition with fakes only at paid AWS/kubectl/scanner
boundaries. It must prove clean PASS plus refusal of missing
`s3:GetObjectVersion`, version-flag/action drift, observed-call drift, deadline,
cleanup, isolation, staging and workload failures. The final deterministic
receipt SHA-256 and final bindings SHA-256 are sealed here only after the
rehearsal is produced from a clean committed tree.

- final bindings SHA-256: `PENDING_RECEIPT_LAST_SEAL`;
- final cold-rehearsal SHA-256: `PENDING_RECEIPT_LAST_SEAL`;
- final test counts: `PENDING_RECEIPT_LAST_SEAL`.

## Post-approval order

1. write and commit authorization 002Q;
2. run and commit the complete real-artifact stage-1 dry validation;
3. remeasure the pre-envelope resource gate;
4. only on PASS create attempt 18's envelope and execute once;
5. preserve every receipt and always clean up;
6. commit terminal evidence and reconcile billing when available.

## Deviations and limitations

No requested policy or execution deviation is taken. AWS billing for attempt
17 remains pending at daily granularity; the conservative $10 recognition is
retained. Successful local coverage and rehearsal do not claim the versioned
download or 540-row workload has passed live.
