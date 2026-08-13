# ASR base-model AWS change packet 2026-002O — proven-sampler successor attempt 16

Status: **DRAFT — INDEPENDENT REVIEW AND EXACT OWNER APPROVAL REQUIRED — NOT EXECUTABLE**

## Required approval phrase

> Approve ASR base-model AWS change packet 2026-002O only, authorizing numbered attempt 16 for one non-transferable 10,800-second offline evaluation attempt within a fresh $10 ceiling and continuing ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-002 at SHA-256 06189414e82c7e497fe7b45d5395af0f03de523bc54c17e1b1e3ae91229d744c.

This draft authorizes nothing. After independent review PASS and the exact
owner phrase, write-once `ASR-BASE-MODEL-AWS-AUTH-2026-002O` must bind this
packet. A committed, read-only `deadline_identity_and_acceptance` dry run must
then PASS against the actual authorization, bindings and packet before any AWS
call or attempt envelope.

## Attempt-15 refusal and bounded diagnosis

Attempt 15 is consumed. DRA stable readiness passed live, proving the prior API
egress correction. The sampler then invoked the driver-root binary without
entering the driver root, returned repeated `libnvidia-ml.so`-not-found text,
and refused before model or audio staging. Cleanup restored zero temporary
state. GPU lifetime to termination request was approximately 206 seconds.

- attempt-15 refusal SHA-256: `e26b1d686fc68a9e5b3a7a8e725745d90e0bbaf663460331a984b7926f51bbbe`;
- exact retained command diagnostic SHA-256: `36a19ba18574ae7880735fe9ae01a46371a6d78222d5236befd10f94c2c248f2`;
- successful B6A sampler receipt SHA-256: `8848c206ecbf459e5e0ffd754352b8eb3086d0b1a750e40c471f890ad8cebde1`;
- successful B6A script SHA-256: `b6aa0e0621fca7fc6ee9e9a2bb9f59ff543efbb71b06a35e5497919d8a573d96`;
- live-node command audit SHA-256: `f3c3649ff49d71f07fa1d87890f5e5d55590da0f920a148d6fa97b87d5092177`.

## Proven command binding

The executor no longer derives a sampler command. It imports this exact
in-container argv from one shared binding:

`/busybox/chroot /driver-root /usr/bin/nvidia-smi --query-gpu=index,memory.used,memory.total --format=csv,noheader,nounits`

The canonical NUL-delimited argv SHA-256 is
`04e6d317a48f3602402b011289224cb686ab7313aab6726051d2f089ac5bd426`.
Stage 1 verifies, before any mutation, that:

1. the historical PASS receipt is byte-identical and binds the B6A script;
2. the script bytes have the receipt-bound SHA-256;
3. the unique sampler invocation extracted from that script has the same
   canonical argv bytes as the executor;
4. 120 samples are required, GPU index is zero, used memory does not exceed
   total memory and total memory remains stable.

The sampler writes bounded, sanitized, typed diagnostics before cleanup on
both PASS and refusal. The observed attempt-15 condition now refuses as
`GPU_SAMPLER_DRIVER_LIBRARY_NOT_FOUND`, not as a parser exception.

## Remaining live-node command audit

The audit covered the sampler, encrypted-volume mount preparation, node-local
staging, network-release/aggregate checks, the pilot workload, aggregate read
and cleanup. Only the sampler has a successful historical live receipt.

Node-local staging and the pilot workload have zero non-rehearsal live PASS
receipts through attempt 15. They are explicitly `NOT_HISTORICALLY_PROVEN`.
The successor persists their exact command-bundle or NUL-delimited container
argv hashes before their first live execution and carries those hashes into
the stage receipts. This establishes future provenance without inventing it.

## Local qualification and rehearsal

The focused local suite passes 179 tests, including:

- byte-equivalent executor versus receipt-bound historical sampler argv;
- the exact attempt-15 driver-library failure with typed diagnostic retention;
- the corrected 120-numeric-sample PASS path;
- no direct `/driver-root/usr/bin/nvidia-smi` invocation remaining;
- explicit non-proven classification for node-local staging and workload;
- all prior ASR-pilot packet and B6A sampler regressions.

The final receipt-last cold rehearsal is generated from the committed
`platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002O.json`. It executes
the real `LiveOperations` composition and uses fakes only at paid AWS/kubectl
and scanner boundaries. Its scenarios include clean PASS, delayed GPU-node
readiness PASS, the observed sampler driver-library refusal, DRA refusal,
never-ready refusal, isolation refusal, digest and finding refusals, deadline,
cleanup and prestage refusals. The receipt SHA-256 is populated only after the
packet, bindings and executable source are final.

## Host capacity

The standing 40-GiB pre-envelope disk rule remains unchanged. Before cleanup,
free space had fallen 171,831,296 bytes below that floor. A keep-list was
committed before deletion. Only the closed Scout-diagnosis scratch containing
five redundant full image representations was deleted; its immutable diagnosis
and exact scan proof remain committed. No Docker image, container, volume, AWS
object or repository evidence was deleted.

- cleanup keep-list SHA-256: `b8d1477560ad73d625c28c8586ec67ccb0b2de67c636894d504dc5e11d6825b1`;
- cleanup result SHA-256: `b83c997a725e067b661434c40b88663663c686a29ec57a3ed8858c8824746abf`;
- fresh qualification SHA-256: `b62b77545ea7f6bfc771c498ce2fa7a7359370241414f96cf541d648460039c2`;
- measured free bytes: `79,254,822,912` (73.81 GiB);
- headroom above the 40-GiB rule: `36,305,149,952` bytes;
- qualified local image and ECR index remain
  `sha256:506d6dd5933854fade34a05d5dfe6a35be7b97dc54da541f0814a3d3e4a6b2aa`.

The live prerequisite gate remeasures disk and all other enumerable host
resources before creating an attempt envelope. Qualification evidence is not a
bypass.

## Exact execution scope

Unchanged: qualified image and four accepted offline-only PyTorch HIGH tuples;
risk record; frozen 540-row/47-language evaluation; pre-staged
13,116,686,091-byte bundle; strict network isolation; one GPU maximum; 60-GiB
encrypted temporary volume; exact DRA image and manifest; status-keyed cleanup.

The exact plan contains no permanent create or update. It contains 18 temporary
create/delete entries, one bounded GPU scale-to-one-then-zero change, and the
same read-only ECR/S3 dependencies. No image upload occurs.

Only after review and exact authorization:

1. attempt 16 only, 10,800 seconds, one GPU node, fresh $10 ceiling;
2. pre-envelope local-resource gate and committed stage-1 dry validation;
3. exact digest rescan and verify-only S3 artifact gate;
4. temporary endpoints, strict network isolation and DRA;
5. proven sampler, first-live command identities, then the 540-row pilot;
6. immediate receipts and zero-state cleanup on every outcome.

Prohibited: attempt reuse or extension; IAM/KMS changes; registry-wide scanning;
internet egress; inbound routes; PHI or untrusted inputs; training; serving;
promotion; `approved/asr`; production SSM; MLflow registration; registry
language mutation.

## Cost and allowance

`COST-REGISTRY-2026-011` SHA-256
`4a66fec2362c62c29021cd253695c979fb0ea0e20dcf08587bed89e1765bb4b9`
conservatively recognizes the full attempt-15 $10 ceiling because AWS billing
has not yet attributed its 206 GPU seconds.

- project ceiling: $300;
- recognized committed guardrail: $124.4286064216;
- active reservations: $0;
- current guardrail headroom: $175.5713935784;
- requested non-transferable attempt-16 ceiling: $10;
- headroom if approved: $165.5713935784.

## Post-approval order

1. write and commit authorization 002O;
2. commit the real-artifact stage-1 dry validation;
3. remeasure the pre-envelope host gate;
4. only on PASS create the attempt envelope and execute once;
5. persist every stage receipt and always clean up;
6. commit terminal evidence and reconcile finalized billing later.

## Deviations

The B6A receipt binds the successful script by path and SHA-256 instead of
embedding the entire command string. The executor therefore compares its
canonical NUL-delimited argv bytes to the exact unique invocation extracted
from the receipt-bound script. This is stronger than comparison to an unbound
transcription.

Node-local staging and the pilot workload cannot be bound to successful
historical receipts because none exist. They remain explicitly unproven and
will produce first-live command-identity evidence. No provenance is invented.
There are no other deviations.
