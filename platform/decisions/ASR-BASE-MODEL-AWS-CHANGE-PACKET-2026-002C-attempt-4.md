# ASR base-model AWS change packet 2026-002C — exact multipart publication successor

Status: **DRAFT — INDEPENDENT REVIEW AND EXACT OWNER APPROVAL REQUIRED — NOT EXECUTABLE**

## Required approval phrase

Usable only after independent review PASS of this committed packet and its
exact hashes:

> Approve ASR base-model AWS change packet 2026-002C only, authorizing numbered attempt 4 for one non-transferable 10,800-second offline evaluation attempt within a $10 ceiling and continuing ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-002 at SHA-256 06189414e82c7e497fe7b45d5395af0f03de523bc54c17e1b1e3ae91229d744c.

This packet is not authorization. After review and the exact owner phrase, a
new write-once `ASR-BASE-MODEL-AWS-AUTH-2026-002C` must capture that phrase,
this packet's SHA-256, the reviewed commit, expiry, and exactly attempt 4.

Before any live invocation, the actual committed authorization blob must be
passed read-only through
`scripts.asr_base_model_pilot_runner.validate_authorization_payload`. Its
write-once validation receipt must be committed. Fixture validation or
validation of an uncommitted copy does not satisfy the gate. **No AWS operation
is permitted before that committed dry validation passes.**

## Purpose and terminal history

This requests one fresh numbered attempt 4 after attempt 3 correctly stopped
at image publication. ECR refused one 4.33 GB layer because only 2.27 GB reached
the service before Docker requested completion. Attempt 3 started no GPU,
loaded no model or audio, and published no image/index/tag.

The following records are immutable and are referenced, never amended:

- packet 2026-002B: SHA-256
  `6ae7a9af79c99d68ff8178951833b9ffbb914bf17f1d230ee2de14535acafaed`;
- AUTH-2026-002B: SHA-256
  `90e5eaf25f4109b49aa26cbd1df0b424d2e8d844d497a6953b220cbeb741386d`;
- attempt-3 refusal: SHA-256
  `65cefdf67de29025960646dd085e773a48df7d354375613b04ae5cac1cf28289`.

Attempts 1, 2, and 3 are consumed and cannot be reused. No time, spend, or
authorization transfers from them.

## Required diagnosis and exact-image proof

The committed diagnosis is
`platform/evidence/ASR-EVAL-RUNTIME-LAYER-DIAGNOSIS-2026-001.json`, SHA-256
`e5bbadc75f691d8199e3c5a39d9803c1baebe035491432e2d60c570f28098736`.

It names the rejected layer:

| Property | Value |
|---|---|
| OCI digest | `sha256:1ef81fd1e44444eb44c30a37e6485d8cb605c0288699e7016f8ca53c308dcbfd` |
| Compressed bytes | `4,329,542,888` |
| RootFS diff ID | `sha256:873a87fcf1b2f1f5e448cf33735998b6fccd6fd6c689926ce2c45238254a91e4` |
| Dockerfile instruction | `COPY --from=eval-builder /opt/venv /opt/venv` |
| ECR received bytes | `2,272,854,016` |
| Missing bytes | `2,056,688,872` |

The content store and OCI export both pass digest and size verification. The
confirmed failure boundary is the remote Docker-to-ECR upload path. The exact
lower-level transport trigger is not provable from retained logs and is not
invented. ECR correctly refused the truncated bytes.

The committed exact-image upload/read-back proof is
`platform/evidence/ASR-EVAL-RUNTIME-EXACT-IMAGE-ROUNDTRIP-PROOF-2026-001.json`,
SHA-256
`4cfb2445df035724b68381d36f34702d22a3107dd4a5cdab0a1ac51ec9b61955`.
Against a loopback-only OCI Distribution registry, all 21 reachable objects
and 7,296,838,446 bytes were uploaded, read back, and rehashed twice with
byte-identical result SHA-256
`bdadf2c4321120e774f1bcbe2754773603a111162bc09e732eb8a97b38180743`.
That includes the entire rejected 4,329,542,888-byte layer, the linux/amd64
child, attestation, and top-level index.

## Exact bindings

| Binding | Value |
|---|---|
| Pre-packet executor commit | `9e7f575aa30062b03af7b0466c2eb5fc6741a39d` |
| Bindings manifest | `platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002C.json`, SHA-256 `20d0c90260d2a036dba8fbc6f387c29e0402c4e59ec87a9e06ef3dbf3449e1cf` |
| Qualification | `platform/evidence/B6-ASR-EVAL-RUNTIME-LOCAL-QUALIFICATION-2026-006.json`, SHA-256 `5982cf98afe6a442c4d79738d4107445cd9ecfad748e7827e4c8e234f122c0c8` |
| Cold rehearsal | `platform/evidence/receipts/ASR-BASE-MODEL-2026-002C-COLD/cold-rehearsal.json`, SHA-256 `ab3ede21558e0120d5f5c7059db75e731f1ea70eaef325b982a1077f5b39c109` |
| Risk acceptance | `platform/decisions/ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-002.json`, SHA-256 `06189414e82c7e497fe7b45d5395af0f03de523bc54c17e1b1e3ae91229d744c` |
| Input freeze | `f59692a7ab5da0a9b257792e04813ec2c4c2317ffb1d68d7e5586789afa9a0ad` |
| Pilot bundle | `1cdca3e75195c7c7417550154e36a1f372715e17efd13c835c87ee503fa84eee` |
| Cost registry | `platform/finance/COST-REGISTRY-2026-006.json`, SHA-256 `d80b1a00d87baa44e162078ff8b51fbda99b3e8733974761e156318e8429e9da` |

Executor source bindings:

| Source | SHA-256 |
|---|---|
| `scripts/asr_base_model_pilot_runner.py` | `62c87eeb0695f76a1c8c372da6d646f0eecb765f6fb4f52d5f9184f6a7a67961` |
| `scripts/asr_base_model_pilot_live.py` | `cf5328f25fa190abd0ea420e892575e0869134bd50fff2dba5e8a45095eea691` |
| `scripts/asr_eval_oci_publication.py` | `a7f3f287990114b660873122cd4a557dfde79ec0e177afada740fad1c7369405` |
| `scripts/asr_base_model_ecr_scanning.py` | `6e67bc09aa49289d3e0a2c3307dfcd99b9fabefb9a23faa30ac1a58dbfdc27f7` |
| `scripts/asr_base_model_pilot_fake.py` | `8d701cb55e111e8640ebc1ce864623de77a69bd16dc3db5e58274ce43fc612a6` |
| `scripts/asr_base_model_pilot_plan.py` | `e93e12e1e16e579175753cf16ba27cf8d6b0dbb2549911d3023059adbfe351ca` |
| `scripts/asr_base_model_pilot_k8s.py` | `0ab123f3b14c54d294a6ad993db03ff93a712ef6462b293796806c93bb567bfc` |
| `scripts/asr_base_model_pilot_cold_rehearsal.py` | `91daaa030a8529979183b05fd6c99d9937fc24658dda3f9b951b1777fae90810` |

## Corrected image-publication stage

The exact image identity is unchanged:

- local tag `medzen-asr-eval-runtime:pilot-5d1b8a0`;
- OCI index `sha256:506d6dd5933854fade34a05d5dfe6a35be7b97dc54da541f0814a3d3e4a6b2aa`;
- linux/amd64 child `sha256:85a82f348f6157adb36016d5b8d6155866ee0c4d40ae1faf4d80df677d50d14e`;
- config `sha256:5cdc428267ae873aaea299c1e64fd6fbdf1d84119c4c0b2ee8d307f722e2ff9a`;
- attestation `sha256:c8ad9bbae25dda5dbd3db33114fac380b9436076857aaa416b9ca33074e112e1`.

No file under `services/asr-eval-runtime/` changed. No rebuild or refreshed
scan finding set is claimed. Risk acceptance 2026-002 continues only for this
exact offline image, subject to its original expiry/drift rules and fresh owner
authorization.

The executor no longer uses `docker login`, `docker tag`, or `docker push`.
It must:

1. export the exact local image to a temporary OCI layout;
2. verify every reachable descriptor's SHA-256 and exact byte count;
3. call `BatchCheckLayerAvailability` in bounded batches;
4. upload only unavailable content blobs via `InitiateLayerUpload` and
   consecutive `UploadLayerPart` requests of at most 20 MiB;
5. verify each returned `uploadId` and `lastByteReceived` before the next part;
6. refuse before `CompleteLayerUpload` unless the exact descriptor byte count
   was sent;
7. publish exact child, attestation, and index bytes in dependency order using
   `PutImage`, with only the index receiving the immutable packet-bound tag;
8. read back and rehash all three manifests;
9. delete the temporary export;
10. run the authoritative ECR scan on the exact bound child; any new finding,
    absent accepted finding, severity drift, incomplete scan, or digest drift
    stops before artifact staging and compute.

Safe pre-model errors are persisted with a bounded reason code and sanitized
service text. Any incomplete layer may remain unreferenced inside ECR after a
refusal; no tag or image is considered published until exact manifest read-back
passes.

The ECR scan-rule merge and exact restoration behavior proven live by attempt
3 remains unchanged: merge the filter into the one existing `SCAN_ON_PUSH`
rule, require two stable exact observations, then restore the exact prior eight
filters during cleanup after every outcome.

## Rehearsal and tests

Two packet-002C cold rehearsals are byte-identical. The fake operations call
the actual multipart publisher core against a compact OCI layout and ECR API
fake. They cover one complete PASS and five injected refusal paths: layer-part truncation, manifest read-back
drift, isolation probe, deadline, and cleanup. Every scenario returns to zero
state and restores the exact original ECR scan configuration; no real AWS or
kubectl call occurs.

- scoped ASR input/runtime/OCI/executor/plan/packet suites: **97 passed, 0 failed, 0 skipped, 0 deselected**;
- cold rehearsal: **1 PASS + 5 injected refusals**, byte-identical twice;
- repository suite: **1,546 passed, 79 failed, 13 skipped, 6 deselected**.
  Of the failures, 59 are the independently disclosed stale generated-language
  and B5-scope mainline failures; 20 require heavyweight optional local
  packages (`transformers`, MLflow, or PEFT) absent from this shell. None is in
  the packet-002C scoped suite. These separate issues remain out of scope and
  must not be bundled here.

## Attempt-4 boundary

If independently reviewed and exactly approved:

- one new write-once authorization for numbered attempt 4;
- one non-transferable 10,800-second attempt and `$10` ceiling;
- at most one GPU node, CPU desired zero;
- exact frozen 540-row pilot only;
- Whisper large-v3, Meta CTC-1B-v2, and Meta LLM-1B-v2 only;
- S3/ECR private endpoints only, no public internet, inbound traffic, PHI,
  user audio, or untrusted inputs;
- status-keyed cleanup after every result;
- exact-image publication and authoritative scan before artifact/model/audio
  staging or GPU scale-up.

Deterministic outcomes remain `PASS_PILOT`, `INCOMPLETE_MEASUREMENT`,
`BLOCKED_INPUT_FREEZE`, `BLOCKED_IMAGE_SCAN`, `BLOCKED_NETWORK_ISOLATION`, and
`FAILED_CLOSED_EXECUTION`.

## Prohibited operations

- reuse of attempts 1, 2, or 3; a fifth attempt; time transfer; duration
  extension; or spend above the approved ceiling;
- AWS execution before independent review, exact owner approval, write-once
  authorization, and committed dry validation;
- IAM or KMS creation/modification;
- serving, production traffic, training, full-suite scoring, or declaring a
  winning base model;
- production SSM, `approved/asr/`, language registry, MLflow registration, or
  B5 status changes;
- any image, model, tokenizer, input, accepted finding, severity, or reviewed
  source drift.

## Deviations

1. Attempt 4 is requested because attempt 3 is terminal; no consumed attempt is
   reinterpreted or reused.
2. Direct ECR multipart APIs replace the Docker registry push. This is a
   disclosed correction at the confirmed failure boundary and preserves the
   exact image identity.
3. The retained image/tag after successful publication is create-only,
   non-serving evaluation evidence, matching the existing packet boundary.
4. The exact lower-level trigger that ended the attempt-3 transfer is recorded
   as not provable; the packet corrects the entire observable truncation class
   with bounded parts and continuity checks.
5. Risk acceptance 2026-002 is continued, not rewritten, because the image,
   packages, models, input freeze, and accepted findings are unchanged. Its
   original time-box and drift rules still apply.

No other adaptation is made.
