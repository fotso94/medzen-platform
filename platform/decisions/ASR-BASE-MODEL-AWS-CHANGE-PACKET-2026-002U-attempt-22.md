# ASR base-model AWS change packet 2026-002U — corrected-image attempt 22

Status: **DRAFT — INDEPENDENT REVIEW AND EXACT OWNER APPROVAL REQUIRED — NOT EXECUTABLE**

## Required approval phrase

> Approve ASR base-model AWS change packet 2026-002U only, authorizing numbered attempt 22 for one non-transferable 10,800-second offline evaluation attempt within a fresh $10 ceiling and accepting ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-003 at SHA-256 43321489ce6f9f3a33f86445601b5bd65d99d2f1746747944b2a185742341034.

This draft authorizes nothing. After independent review PASS and that exact
delegated owner phrase, write-once `ASR-BASE-MODEL-AWS-AUTH-2026-002U` must
bind this packet's final SHA-256. A committed read-only
`deadline_identity_and_acceptance` validation must then PASS against the
actual authorization, bindings and packet before any attempt envelope or AWS
mutation.

## Attempt-21 result and corrected diagnosis

Attempt 21 is consumed. Eight of eleven execution stages passed, the GPU node
ran for at most 1,371 seconds, and cleanup was independently verified at zero.
The terminal failure occurred before inference in the pre-torch network proof:
the first ECR API connection was refused while strict VPC CNI policy was still
converging. The immutable refusal remains SHA-256
`6735ebd5bb1c4a598a4d3c6f4e0b9dc1819542bae3d8a8849819a9146bf894c6`.
The later correction record, SHA-256
`9cac93c3f233ef46f4097122bb711429a54b9c0e3aecca6618aaa8da551748f6`,
retracts the earlier head-minus-one diagnosis without changing history.

The corrected runtime polls the first allowed private endpoint every five
seconds for at most 120 seconds. Only after one successful connection does it
run the complete allowed-endpoint and prohibited-destination battery. PASS and
REFUSED receipts retain resolved IPs, exception class, errno and per-attempt
timing. Policy-agent diagnostics are best-effort on refusal and never replace
the typed proof result. Torch import before the complete gate passes remains
prohibited.

## Corrected exact image and scoped acceptance

The correction changes the image identity and therefore does not continue the
old acceptance by implication. The exact offline-only image is:

- source commit `7efa6e8c4be378e754e9edb8b64151aa89c0a366`;
- local/ECR tag `pilot-7efa6e8c`;
- OCI index `sha256:f14fe88a7ebb2c68bf2ed772ad2ce8913c1fa8117b2da5305af55298f1d15505`;
- linux/amd64 child `sha256:4d1ccde955f5ae074ed6470d7edb6d74f9d49cc6a6f44f9f0a2b7397a0cd3841`;
- config `sha256:2938427027f22b10f9dc5c89b3305b5689ea5c44b088839b85583e1575feeda3`;
- attestation `sha256:8d96d7c4b5b6f4a3c1677dc93301e2829afd0923b20eb269272b5e19dbf57e23`.

Qualification record `B6-ASR-EVAL-RUNTIME-LOCAL-QUALIFICATION-2026-007`,
SHA-256 `04bf743b7ee5864f9560f87f232df13a0791a434dbe26d8fe081459ffb42dbe9`,
proves read-only non-root startup, pinned runtime/model adapters, absent build
tooling and the corrected probe source. The exact child-bound BuildKit SPDX
attestation was scanned with Docker Scout 1.18.3 at zero critical and exactly
the four accepted PyTorch highs. SARIF SHA-256 is
`7dd64ffd92762e9d9d633ef97bb7b1b79915115f546d6a005fd428c170fe99f7`.

Risk record `ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-003`, SHA-256
`43321489ce6f9f3a33f86445601b5bd65d99d2f1746747944b2a185742341034`,
is draft and ineffective until the exact approval phrase. It remains limited
to one offline evaluation window with hash-frozen public inputs, no PHI, no
inbound network, private S3/ECR-only egress, one GPU maximum and mandatory
destruction. It is non-precedential for serving, training, production,
traffic, promotion or later evaluation windows.

## Local resource and scanner prerequisites

The new exact Docker archive is 7,300,902,912 bytes. With scanner scratch,
evidence reserve and safety margin, the calculated peak is 12,132,741,120
bytes; the stricter 40 GiB owner floor remains binding. Conservative cleanup
was authorized by a committed keep-list and restored 57.45 GiB while preserving
the exact new image and pinned CUDA base. The measured packet qualification
recorded 61,756,919,808 available bytes and 18,807,246,848 bytes of headroom
above the 40 GiB requirement.

Docker Scout authentication is passed through Docker Desktop's credential
store. The gate accepts either that configured helper or the complete
environment pair, records only the mode, and never reads into evidence or
persists credential values. The actual pinned scan already passed through this
credential-store path. Disk, memory, CPU, process limits, commands, Docker
daemon and Scout authentication are remeasured before the attempt envelope;
failure consumes no attempt and makes no AWS mutation.

## Cost reconciliation and allowance request

`COST-REGISTRY-2026-017`, SHA-256
`974d1419e86299334bda8eee8a0b1f6025ecf8fef0d6c691a501ab393e9d60f1`,
conservatively commits the full attempt-21 ceiling because current-day billing
is not final:

- aggregate ceiling: $300;
- recognized committed guardrail: $184.4286064216;
- active reservations before this request: $0;
- headroom before request: $115.5713935784;
- requested attempt-22 ceiling: $10;
- headroom if approved: $105.5713935784.

No pending or zero estimated charge expands headroom. Billing is reconciled
again after terminal evidence.

## Exact execution scope

Only after review PASS and exact owner approval:

1. run attempt 22 once, for at most 10,800 seconds and one GPU node;
2. remeasure local resources, validate the 40 GiB GPU root-volume prerequisite,
   and validate the committed real-artifact stage-one receipt before the
   attempt envelope;
3. publish the exact new image create-only to the existing evaluation ECR
   repository, temporarily merge the exact scan-on-push filter, wait for the
   child Basic scan at zero critical/high, pull the child by digest, byte-verify
   it and require the pinned Scout scan at exactly the four accepted tuples;
4. verify the already pre-staged model/audio bundle without uploading bytes in
   the timed window;
5. create only the temporary encrypted volume, endpoint/security-group,
   strict network-policy, namespace, DRA, claim and pilot resources enumerated
   by the machine plan; scale the existing GPU ASG to one then zero;
6. run the frozen 540-row, 47-language comparison of Whisper large-v3, Meta
   Omnilingual CTC-1B-v2 and Meta Omnilingual LLM-1B-v2;
7. persist every stage receipt immediately and run status-keyed cleanup on
   every terminal path.

Machine-plan counts: three permanent create-only ECR image objects, zero
permanent updates, nineteen temporary create-then-delete resources and one
bounded capacity change. The immutable ECR objects contain only the reviewed
offline evaluation image; no serving tag or production alias is created.

Prohibited: reuse or extension of attempts 1–21; IAM or KMS changes; Inspector
Enhanced or registry-wide scanning changes; internet or inbound access;
training; serving; promotion; `approved/asr`; production SSM; MLflow/model
registration; language-registry changes; or any resource not in the exact
machine plan.

## Binding and rehearsal gates

Bindings `ASR-BASE-MODEL-PILOT-BINDINGS-2026-002U` pin executor source commit
`9987558620519118ae75fc1c2a4874112439c646`, all 31 executor modules including
`services/asr-eval-runtime/medzen_asr_eval/network_probe.py`, the exact image,
qualification, scan, risk record, cost registry, host cleanup, prior write-once
history and every standing control.

The receipt-last cold rehearsal must be generated twice byte-identically from
the committed 002U bindings with actual `LiveOperations` composition and fakes
only at paid AWS/kubectl/tool boundaries. In addition to all standing injected
paths, it must prove four direct network-probe paths: already-converged PASS,
delayed convergence then PASS, never-converges typed timeout, and a prohibited
destination accepted only after convergence. The committed receipt path is
`platform/evidence/receipts/ASR-BASE-MODEL-2026-002U-COLD/cold-rehearsal.json`.
Its SHA-256 and this packet's SHA-256 are published in the independent review
package; no source or binding edit is permitted after receipt generation.

## Post-approval order

1. write and commit authorization 002U quoting the exact approval phrase;
2. run and commit the complete real-artifact stage-one dry validation;
3. run every pre-envelope prerequisite; only PASS may consume attempt 22;
4. execute once, persist every receipt and always clean up;
5. commit terminal evidence and reconcile billing.

## Deviations and limitations

The local Scout preflight uses the byte-verified BuildKit SPDX attestation whose
in-toto subject is the exact linux/amd64 child, rather than creating a second
full image archive. This preserves the single-representation disk control while
still executing the pinned Scout scanner over the exact child-bound package
inventory. The live post-publication gate remains stricter: it reconstructs
the exact ECR child by digest and scans those pulled bytes.

No AWS execution has occurred under this packet. Local qualification does not
claim that any of the 540 inference rows has passed.
