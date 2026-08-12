# ASR base-model AWS change packet 2026-002F — Scout-diagnosed successor

Status: **DRAFT — INDEPENDENT REVIEW AND EXACT OWNER APPROVAL REQUIRED — NOT EXECUTABLE**

## Required approval phrase

> Approve ASR base-model AWS change packet 2026-002F only, authorizing numbered attempt 7 for one non-transferable 10,800-second offline evaluation attempt within a fresh $10 ceiling and continuing ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-002 at SHA-256 06189414e82c7e497fe7b45d5395af0f03de523bc54c17e1b1e3ae91229d744c.

No AWS execution is authorized by this draft. After independent review PASS and
that exact owner phrase, a write-once `ASR-BASE-MODEL-AWS-AUTH-2026-002F`
must be committed. A committed read-only run of the complete
`deadline_identity_and_acceptance` stage against the actual authorization,
bindings and this packet must then PASS before the attempt envelope or any AWS
call.

## Why attempt 6 stopped

Attempt 6 consumed its authorized window and stopped at
`image_publication_and_scan` with `SCOUT_EXECUTION_REFUSED`. It created no
runtime resources, started no GPU, staged no model/audio, spent $0, and cleaned
to zero. The immutable refusal is:

- `platform/evidence/ASR-BASE-MODEL-PACKET-2026-002E-A1-ATTEMPT-6-SCOUT-EXECUTION-REFUSAL.json`
- SHA-256 `095d2e08b22056714e069e0c1299e550dbbc839990f36fd3724088d0b6a4ba26`

Packet 002E-A1, its authorization, dry-run receipt and refusal are unchanged.
Attempt 6 may not be reused.

## Root cause and complete diagnosis

The committed diagnosis is
`platform/evidence/ASR-EVAL-RUNTIME-SCOUT-EXECUTION-DIAGNOSIS-2026-001.json`,
SHA-256 `24aac86d00585679965894ed20b2d85642b18f7f12f4a272a0a3932412608ac0`.

The exact attempt-6 command was reproduced against the exact bound image:

```text
docker scout cves --format sarif --only-severity critical,high \
  --output <OUTPUT> oci-dir://<EXACT_LAYOUT>
```

Docker Scout 1.18.3 read the OCI directory, stored the image for indexing, then
exited 1 in 1.11 seconds with `could not generate SBOM`; it produced no SARIF.

Each requested hypothesis was tested:

| Hypothesis | Result | Evidence |
|---|---|---|
| `HOME` absent | Ruled out | `HOME` was present in failing and passing executions. |
| Docker config/auth absent | Ruled out | Docker config existed; authenticated pinned Scout prerequisites and archive scan passed. No credential values were persisted. |
| Temporary/disk space insufficient | Ruled out | 154,798,366,720 free bytes at reproduction and 105,395,904,512 at committed preflight, above the 20 GiB gate. |
| Timeout | Ruled out | Refusal occurred in 1.11 s under an 1,800 s timeout; cold archive scan completed in 216.937 s. |
| Wrong image reference/corrupt image | Ruled out | Exact index, child, config, attestation, 21 reachable objects and 15 layers verified; local tag/digest and archive scans passed. |
| Scout OCI-directory ingestion | Confirmed | The verified payload fails only via `oci-dir://`; the same payload passes through `archive://` with exactly the accepted four findings. |

The root cause is a Docker Scout 1.18.3 OCI-directory SBOM-generation defect,
not an image, CVE, risk-acceptance, storage, timeout or authentication change.

## Class-level corrections

1. The live gate still reconstructs the exact ECR linux/amd64 child by digest
   and verifies every downloaded descriptor.
2. It creates only Docker's required `manifest.json` metadata, archives those
   verified payload bytes, and runs pinned Scout through `archive://`.
3. Any child/config/layer byte drift refuses before Scout.
4. Every external-tool invocation in the pilot artifact family uses one
   bounded diagnostics wrapper. Its write-once journal records command, exit
   code or timeout, duration, stdout/stderr byte counts and hashes, and at most
   4,096 sanitized characters per stream. Environment/credential values are
   never recorded.
5. Attempt 7 itself refuses before its envelope unless the committed real Scout
   preflight is present, hash-bound, zero-AWS, tied to the same exact image and
   reports the exact accepted finding set.

All 13 live executor modules are bound unconditionally in
`platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002F.json`, SHA-256
`c677eb2eb4c1889efaf0936006ef2e2b3faf1655ed6f2f7e0a08fa5ab9940d7c`.
No module can be omitted conditionally.

## $0 real-execution Scout preflight

The required preflight ran from a clean committed checkout against the actual
local image, actual Docker export, actual pinned Scout command and actual local
execution environment:

- receipt: `platform/evidence/ASR-EVAL-RUNTIME-SCOUT-PREFLIGHT-2026-001.json`
- receipt SHA-256: `cabd8497de52e02f180c5f9caf455413be7de6006fb281a65c122c109fb3bf4b`
- SARIF: `platform/evidence/ASR-EVAL-RUNTIME-SCOUT-PREFLIGHT-2026-001.sarif.json`
- SARIF SHA-256: `d42135a4647b6878cddc5490e2d6ab2c8b7b513d7356c909c86b9424e23fc30c`
- source commit: `60ac26e38e66e2e37938074d257c2363f7a924a9`
- bound bindings SHA-256 at execution: `18f5f32d1e52905e7550cf0348d09fca6ed1eb5b311614ff1fd036d0d9e80037`
- exact archive: 7,296,860,160 bytes, SHA-256
  `f39bde78be5d23551747aa28b7b163ce1aa263c493cd9aad989d05763b4c07f3`
- result: return code 0, 305 packages, 0 critical, exactly 4 accepted high,
  138.947 seconds
- AWS calls/mutations, kubectl calls, GPU and cost: all zero

The later bindings changes only populate the diagnosis/preflight hashes, bind
the preflight receipt, and bind the final rehearsal. Image, scanner, security
gate and execution scope did not change.

## Final committed-bindings cold rehearsal

The final cold rehearsal is
`platform/evidence/receipts/ASR-BASE-MODEL-2026-002F-COLD-v2/cold-rehearsal.json`,
SHA-256 `257598f6b5ba5a123e3e5b301a797167a4a1e25341848b239f8d36b8e5a1fb1d`.

It loads the committed 002F bindings, validates all 13 module hashes, exercises
all 11 stages with one full PASS and five injected refusal paths, proves the
wrong-digest and extra-finding refusals, and returns every scenario to zero.
There are zero real AWS/kubectl calls and zero mutations.

## Exact execution scope

Authorized after all gates only:

- numbered attempt 7 only;
- one GPU node maximum for 10,800 non-transferable seconds;
- fresh maximum cost ceiling $10 within the $300 project ceiling;
- read/verify the existing immutable ECR image; no upload;
- ECR Basic as supplementary zero-critical/zero-high OS gate;
- digest-verified archive Scout scan requiring exactly the four accepted
  PyTorch HIGH tuples;
- create-only research asset staging and the frozen 540-row, 47-language pilot;
- temporary private endpoints, strict network policy, evaluation volume and
  workload; S3/ECR-only egress and no inbound path;
- mandatory cleanup and CPU/GPU desired zero on every terminal outcome.

Explicitly prohibited:

- reuse of attempts 1–6 or any eighth attempt;
- Inspector Enhanced or registry scanning configuration mutation;
- image rebuild/upload, source, image, model, tokenizer, input, finding or
  scanner drift;
- IAM/KMS changes, internet egress, PHI, untrusted input or inbound traffic;
- serving, production, training, promotion, approved/asr, production SSM,
  MLflow registration or language-registry mutation;
- citing the offline risk acceptance as serving precedent.

## Immutable continuity

Unchanged image identity:

- index `sha256:506d6dd5933854fade34a05d5dfe6a35be7b97dc54da541f0814a3d3e4a6b2aa`
- linux/amd64 child `sha256:85a82f348f6157adb36016d5b8d6155866ee0c4d40ae1faf4d80df677d50d14e`
- risk acceptance SHA-256
  `06189414e82c7e497fe7b45d5395af0f03de523bc54c17e1b1e3ae91229d744c`
- pilot bundle SHA-256
  `1cdca3e75195c7c7417550154e36a1f372715e17efd13c835c87ee503fa84eee`
- pilot row-list SHA-256
  `2170eb450ae9b42c64e02f8753469eb7d74b7b3f2363ae3f770fbd3062e488b6`

## Post-approval final gate

After review and owner approval, write AUTH-2026-002F and commit it. Then run
and commit the complete read-only stage-1 validation against the actual packet,
authorization and bindings. That receipt must PASS, and the committed Scout
preflight must pass the runner's own binding check, before the attempt envelope
or any AWS call.

## Deviations

None. The failing ingestion mode is replaced by a byte-verified equivalent
accepted by the pinned scanner; no security finding is suppressed or waived.
