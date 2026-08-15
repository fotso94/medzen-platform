# ASR base-model AWS change packet 2026-002Z — token-budget-corrected attempt 27

Status: **DRAFT — INDEPENDENT SELF-REVIEW AND DELEGATED APPROVAL REQUIRED — NOT EXECUTABLE**

## Required approval phrase

> Approve ASR base-model AWS change packet 2026-002Z only, authorizing numbered attempt 27 for one non-transferable 10,800-second offline evaluation attempt within a fresh $10 ceiling and continuing ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-003 at SHA-256 43321489ce6f9f3a33f86445601b5bd65d99d2f1746747944b2a185742341034.

This draft authorizes nothing. After independent review PASS and the exact
delegated approval phrase, write-once `ASR-BASE-MODEL-AWS-AUTH-2026-002Z`
must bind this packet's final SHA-256, followed by the committed stage-1
dry validation, before any attempt envelope or AWS mutation.

## Attempt-26 terminal result

Attempt 26 is consumed (refusal record SHA-256 `ed2173dcfae69a86daf516743d1f9840e45d840009e8411e632fb854036222e5`).
It achieved the campaign's deepest progress: 1,580 of 2,700 row-inferences
completed — omniASR_CTC_1B_v2 finished all unconditioned rows and
omniASR_LLM_1B_v2 finished both modes — before Whisper's FIRST row raised
ValueError: max_new_tokens=448 equals the model max_length, which must
also fit the 3-token forced decoder prompt. The new phase journal
identified model, row and exception in seconds; telemetry ruled out
memory pressure (4.9 GiB RAM floor, 7,042 of 23,034 MiB VRAM peak); the
/models card-layout fix, DNS gate, pre-pull, attachment wait and all
diagnostic instruments are now live-proven. Cleanup PASS to zero state.

## Correction: Whisper token budget inside the image

Commit `5ebbaed` sets WHISPER_MAX_NEW_TOKENS = 440 (448 minus prompt
headroom) with the eos/cap thresholds updated consistently and a
regression test guarding the headroom. Because the fix lives in the
evaluation image, the image was rebuilt from that commit with the same
Dockerfile, base digest and dependency set:

- OCI index `sha256:6ea2fc5401a659cd4271c24c91f2b5f68619d47b897858053f6da4232cde645b`
- linux/amd64 child `sha256:136a972859d970113b7a8bd4f41350ca1a2239cf617f38cb07cc95597c06fe84`
- config `sha256:fb6b9c569a92122dd60564837fa1f5408c4a382b09d67a604ded8662185633fc`
- attestation manifest `sha256:8097da8038cb0ee968e49444f3fb11b11637a3b11a181ca7bd65d105ba3819b0`
- exact archive 7,301,067,264 bytes, SHA-256 `57c0eaa59287291fd12b02f414f371ab0fd957f15528cf0d9ee60e20ee183628`

The corrected backends.py and the `org.opencontainers.image.revision`
label were verified inside the built layers before binding. Publication
to ECR occurs IN-ATTEMPT through the proven bounded-part publisher with
layer-availability checks — unchanged blobs (the CUDA/Torch stack)
already exist in the repository, so only the small changed layers upload.
The dual scan gate (digest-pinned Scout + ECR Basic) runs against the new
digest inside the window as always. The dependency set is byte-identical
to the prior image, so the same four accepted torch HIGH findings are
expected; risk acceptance 003 continues for the identical offline
boundary, rebound to the new image identity per the attempt-22 precedent.

## Cost

`COST-REGISTRY-2026-022` (SHA-256 `c79b1c680513345dbd2a2c99cff07088e320861edaa8213fdf7690db661d5c6e`)
closes attempt 26: recognized guardrail $234.4286064216, headroom
$65.5713935784 before and $55.5713935784 after the fresh $10 — inside the
$300 ceiling.

## Exact execution scope

Identical to packet 2026-002Y except the image identity and
publication_required=true. All executor modules unchanged (the correction
is image content only); waiter/finalizer audit 003 remains current.

## Cold-rehearsal and review gates

Twice-run byte-identical cold rehearsal from the committed 002Z bindings;
the full self-review battery including the bindings self-reference check
(authorization block, write-once history through attempt 26).

## Post-approval order

1. Write-once AUTH-2026-002Z; commit, push. 2. Committed stage-1 dry
validation. 3. Execute attempt 27 once; on PASS_PILOT review the 540-row
aggregate before any successor scope.
