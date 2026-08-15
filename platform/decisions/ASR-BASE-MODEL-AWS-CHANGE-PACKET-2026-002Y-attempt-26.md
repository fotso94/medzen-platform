# ASR base-model AWS change packet 2026-002Y — card-layout-corrected diagnostic attempt 26

Status: **DRAFT — INDEPENDENT SELF-REVIEW AND DELEGATED APPROVAL REQUIRED — NOT EXECUTABLE**

Engineering context: prepared by Claude as sole engineer under the owner's
2026-08-15 role transition; the independent-review battery and delegated
approval remain mandatory and are performed as a logically separate
self-review checkpoint.

## Required approval phrase

> Approve ASR base-model AWS change packet 2026-002Y only, authorizing numbered attempt 26 for one non-transferable 10,800-second offline evaluation attempt within a fresh $10 ceiling and continuing ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-003 at SHA-256 43321489ce6f9f3a33f86445601b5bd65d99d2f1746747944b2a185742341034.

This draft authorizes nothing. After independent review PASS and the exact
delegated approval phrase, write-once `ASR-BASE-MODEL-AWS-AUTH-2026-002Y`
must bind this packet's final SHA-256. A committed, read-only
`deadline_identity_and_acceptance` validation must then PASS against the
actual authorization, bindings, packet, all 33 executor-module hashes and
every write-once predecessor before an attempt envelope or AWS mutation.

## Attempt-25 terminal result

Attempt 25 is consumed. Eight of eleven stages passed, including the first
live proofs of exact-image pre-pull (224.3 s), the VPC-resolver DNS
consistency gate, the stable EBS attachment sequence
(attaching → attached → attached), the complete pre-Torch positive/negative
network probe, and the cross-pod inbound refusal. The pilot Job then reached
a failed terminal state roughly two minutes into the application phase. The
immutable refusal record (SHA-256 `320f830a1e93b4328c2e027d835df93deea73c79962b38222ca12a529d341963`)
honestly records the root cause as undiagnosed because the sanitized capture
retained only the 4,096-byte head of a 9,066-byte log. The corrected
131-file evidence set hash is bound by errata SHA-256
`c4bf979734950cd3c1867117fc06eba468970c5c4f880239d2f49971a0857da9`.

## Root cause and card-layout correction

Post-terminal static analysis identified the failure mechanism with high
confidence: the image's baked fairseq2 asset cards
(`services/asr-eval-runtime/assets/models.yaml`) resolve the omniASR
checkpoints and tokenizer at absolute `/models/...` paths — the exact layout
the historical docker preflight proved with `-v ...:/models` and the layout
`medzen_asr_eval.__main__` defaults to — while the pilot pod mounted the
staged weights only at `/input`. No `/models` path existed in the image or
the pod, so the first Meta backend load (`omniASR_CTC_1B_v2`, first in
sorted candidate order) failed at asset resolution. The timeline matches
exactly; `verify_model_root` passed conceptually because it checks
`model_root` directly and never consults the cards.

The correction (commit `46dcc68`):

1. The pilot pod now mounts the staged `input/models` tree read-only at
   `/models` via a subPath mount of the existing input volume. The staged
   layout already places `omniASR-CTC-1B-v2.pt`, `omniASR-LLM-1B-v2.pt`,
   `omniASR_tokenizer_written_v2.model` and `whisper-large-v3-ct2/` there.
2. The pilot driver passes `model_root=/models`, restoring the container's
   canonical layout for Whisper, verification and the cards alike.
3. A render-time validator (`validate_asset_card_mount_coverage`) refuses
   any workload whose asset-card absolute file paths are not covered by pod
   mounts, closing this class permanently. Three regression tests cover the
   card-path parse, the refusal path and the driver layout.

## Diagnostic retention correction

Because attempt 25 also proved the retention defect, this packet carries the
complete diagnostic upgrade (commit `336fb5d`):

1. Every post-model capture retains sanitized 4,096-byte head AND terminal
   tail windows, selected after full-value secret sanitization, with raw
   byte count and digest; a large probe preamble can no longer evict the
   terminal error.
2. Normalized container termination facts — exit code, reason, signal,
   OOMKilled, startedAt, finishedAt — are persisted as structured fields
   before any cleanup, independent of Pod JSON truncation.
3. The pilot driver writes an fsync-per-event JSONL phase journal on the
   host-mounted output path recording phase transitions, per-model load
   boundaries, exception class, bounded sanitized exception text and the
   completed-row counter; the executor reads it back post-terminal via SSM
   with existence, size and digest markers.
4. Bounded periodic RAM/VRAM telemetry samples run during Job polling using
   the exact B6A-proven `nvidia-smi` argv through the DRA node pod; at least
   two PASS samples are required at completion, and the summary is retained
   on every terminal path.
5. Cold rehearsal injects a large-preamble terminal-tail scenario plus
   delayed-device, journal and termination-fact assertions.

## Systemic remote-observation audit

`ASR-BASE-MODEL-WAITER-FINALIZER-AUDIT-2026-003` (SHA-256
`8c6630975a8e11fe72cd52337bec46335e8a343f97162ca73f2efc5fc960a424`) regenerates the systemic audit
at the current source commit: 15 Python waiter sites, 11 remote SSM
observation sites (the phase-journal post-terminal stable-file read and the
periodic telemetry sampling are newly classified), 3 contained finalizers,
zero raising finally blocks, zero unclassified SSM crossings and zero
one-shot asynchronous success gates.

## Exact unchanged image and risk continuation

The evaluation image is byte-identical to the attempt-22-published,
attempt-25-verified digests: OCI index
`sha256:f14fe88a7ebb2c68bf2ed772ad2ce8913c1fa8117b2da5305af55298f1d15505`,
linux/amd64 child
`sha256:4d1ccde955f5ae074ed6470d7edb6d74f9d49cc6a6f44f9f0a2b7397a0cd3841`.
No upload, no registry mutation, no IAM/KMS change and no production write
is in scope. The dual scan gate (digest-pinned Scout rescan + ECR Basic)
re-runs unchanged. `ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-003` continues
unchanged at SHA-256
`43321489ce6f9f3a33f86445601b5bd65d99d2f1746747944b2a185742341034` for the
same offline, frozen-input, no-PHI, no-inbound, S3/ECR-only,
destroyed-after-window boundary, and remains non-precedential for serving.

## Cost reconciliation and fresh allowance request

`COST-REGISTRY-2026-021` (SHA-256
`74f856a8772d2ef3d50b17857d36e1fb9eb2eaac3e0b76ae4cd09520d2494f35`) conservatively closes the
complete attempt-25 $10 ceiling: recognized committed guardrail
$224.4286064216, active reservations $0, headroom $75.5713935784 before this
request and $65.5713935784 after the fresh $10 attempt-26 reservation —
inside the $300 aggregate ceiling. Attempt 24/25 actual billing remains
pending AWS ingestion; estimated zero and credits do not expand headroom.

## Exact execution scope

Identical to packet 2026-002X except for the corrections above: one
10,800-second attempt window; deadline-first ordering; three temporary VPC
endpoints and one scoped security group; strict CNI mode for the window;
one g6.xlarge via the GPU node group; one 60-GiB encrypted gp3 volume with
stable attachment and bounded guest-device polling; digest-pinned image
pre-pull qualification; DNS-control and inbound-control pods; node-local
staging of the frozen bundle; the pilot Job (540 rows, three candidates)
with the /models mount; aggregate report; status-keyed cleanup to zero
state with independent read-back. 0 permanent creates, 0 permanent updates,
18 temporary create/delete resources, 1 bounded capacity change.

## Cold-rehearsal and review gates

The committed-bindings cold rehearsal must be generated twice byte-identically
from `platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002Y.json`
(SHA-256 `04699f0f7de3beb5ee8f4adfa60385e648af6dcd266d8532985ab1c7480d4bc8`) and must cover the
PASS paths and injected refusals including delayed device, never-ready
device, large-preamble tail retention, journal capture and termination-fact
normalization, with every scenario reaching zero state. The independent
self-review battery must reproduce: all claimed SHA-256 values, all 33
executor-module hashes, byte-equivalent rehearsal regeneration, focused
suites, build-context drift zero against image commit `7efa6e8`, and live
AWS zero state.

## Post-approval order

1. Write-once `ASR-BASE-MODEL-AWS-AUTH-2026-002Y` binding the approval
   phrase, this packet's SHA-256, the review head and the review ID.
   Commit and push.
2. Committed real-artifact stage-1 dry validation (zero AWS calls).
3. Execute attempt 26 once: stage transitions logged to the status file;
   terminal outcome committed and posted to the review channel. On
   PASS_PILOT the aggregate report is reviewed before any successor scope.
