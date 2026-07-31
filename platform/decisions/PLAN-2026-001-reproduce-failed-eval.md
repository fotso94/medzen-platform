# PLAN-2026-001 — Reproduce the failed-candidate evaluation

**Status: PREPARED, NOT LAUNCHED.** Awaiting approval.
Purpose: reproduce base-vs-failed-candidate with in-repo tooling so
`EVAL-2026-001` can move from EXTERNAL to independently reproduced — or not.

---

## The constraint you need to decide on

You asked for "pinned evaluator image/code at the approved commit and recorded
digest". Those cannot currently be the same object:

| | |
|---|---|
| Pinned image | `sha256:fc6972a5143a943ed4fcbbc6121eb1c0e43ff83065c75df07f1284ba60c94e8a` |
| Commit baked into it | `202b005` |
| Approved commit | `7814832` (8 commits later) |
| `scripts/evaluate_candidate.py` at `202b005` | **does not exist** |

The image predates the evaluator, the collator fix, and the corrected
`label_length.py`. Three ways forward:

**A — Image for environment, bundle for code (recommended).** Run the pinned
image for its verified dependency set, and inject the approved commit's code
through the existing publish-bundle trust chain: `TAR_SHA256` is passed in
user-data, never read from S3, and the archive is verified before anything
executes. Two recorded digests instead of one, both meaningful. No rebuild, no
new 6 GB artifact, ~9 minutes saved and one fewer thing to scan.

**B — Rebuild the image at `7814832`.** One digest covering everything. Costs a
builder instance (~9 min, ~$0.15), a fresh ECR scan, and produces a second 6 GB
image whose only purpose is a 30-minute evaluation.

**C — Reuse `fc6972a5` unmodified.** Not viable: the evaluator is not in it.

Everything below assumes **A**. Say the word and I will rewrite for **B**.

---

## Exact commands

**1 — publish the code bundle** (writes to `candidates/bootstrap/`, nothing else):

```
python scripts/publish_bundle.py
```

Prints `GIT_SHA=7814832…` and `TAR_SHA256=<64 hex>`. Both are quoted in the
approval record and the second is the root of trust for step 2.

**2 — render user-data** (the evaluation variant, `pipeline/eval_userdata.sh`,
which I will write only once you pick A or B):

```
GIT_SHA=<40 hex> TAR_SHA256=<64 hex> \
IMAGE_DIGEST=sha256:fc6972a5143a943ed4fcbbc6121eb1c0e43ff83065c75df07f1284ba60c94e8a \
WATCHDOG_SECONDS=1800 \
  envsubst '${GIT_SHA} ${TAR_SHA256} ${IMAGE_DIGEST} ${WATCHDOG_SECONDS}' \
  < pipeline/eval_userdata.sh > /tmp/ud.sh
bash -n /tmp/ud.sh
```

**3 — the evaluation itself**, run inside the container:

```
python scripts/evaluate_candidate.py \
  --language pidgin --task tts --eval-version v1 \
  --lang-token en \
  --adapter s3://medzen-speech/candidates/asr/23868bab2d8448759fc1b9ed26156952/final \
  --expect-adapter-sha256 17e1b7381b7b3fdb362ecb692d72b92a2dc295d7ee79ff6367a8d6a9c7cd3195
```

Two arms only: untouched base, and the failed final adapter. No `--adapter`
for any checkpoint. Language `en` and task `transcribe` forced for both arms;
`transcribe` is pinned in the source and not a flag.

**4 — launch**

```
aws ec2 run-instances \
  --image-id ami-01b08a3e47b323a73 \
  --instance-type g6.xlarge \
  --subnet-id subnet-00232b25bc1ac407a \
  --security-group-ids sg-0ec6a550611714d0c \
  --associate-public-ip-address \
  --iam-instance-profile Name=medzen-trainer-profile \
  --metadata-options "HttpTokens=required,HttpEndpoint=enabled,HttpPutResponseHopLimit=2" \
  --block-device-mappings '[{"DeviceName":"/dev/xvda","Ebs":{"VolumeSize":100,"VolumeType":"gp3","DeleteOnTermination":true}}]' \
  --instance-initiated-shutdown-behavior terminate \
  --user-data file:///tmp/ud.sh \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=medzen-eval-repro},{Key=Project,Value=medzen-b4}]'
```

## Instance configuration

| | |
|---|---|
| Type | g6.xlarge (NVIDIA L4 24 GB), **on-demand, direct EC2 — not EKS** |
| Region / AZ | eu-central-1a |
| AMI | `ami-01b08a3e47b323a73` (DLAMI base, NVIDIA driver + docker) |
| Disk | 100 GB gp3, delete-on-termination |
| Termination | `instance-initiated-shutdown-behavior=terminate`; user-data `trap finish EXIT` shuts down on every exit path |
| Watchdog | **1800 s hard ceiling**, independent background timer → `shutdown -h now` |
| IMDS | v2 required, hop limit 2 (container needs the extra hop) |

## Maximum cost

| | |
|---|---|
| g6.xlarge on-demand, eu-central-1 | $1.0064/hr |
| Ceiling 30 min | **$0.503** |
| 100 GB gp3 for 0.5 h | ~$0.007 |
| **Maximum** | **≈ $0.51** |

Expected actual: ~12 min including image pull and base-model fetch — 44 clips ×
2 arms at roughly 0.4–1.2 s each is under two minutes of decoding.

## Scope limits

Nothing is trained, swept, registered, promoted, merged, quantized or deployed.
No IAM change. The trainer role is already write-denied on `curated/*`, `raw/*`,
`eval/*` and `models/*`, so the instance physically cannot alter the eval set it
scores. Output is aggregate metrics plus per-utterance rows keyed by audio
checksum; the evaluator emits no transcript anywhere.

## What will be reported afterwards

| Check | Expected |
|---|---|
| Base WER / CER | ≈ 0.513 / 0.378 |
| Candidate WER / CER | ≈ 7.22 / 4.22 |
| EOS rate, cap-hit rate | measured, both arms |
| Prompt / generated / total tokens | measured, both arms |
| Stopping reasons | `eos` vs `max_new_tokens` vs `other` |

If the numbers agree, the new evidence is bound to `EVAL-2026-001` and the
MLflow provenance. **If they materially differ, the run stops and is
investigated — the prior decision is not rewritten as "reproduced".**

## Still open before any retraining

- new reviewed deferral policy (19 rows, not 20), new adoption binding, new
  dataset fingerprint — see `label-audit-reaudit-2026-07-31.json`
- generation-config pinning in one place
- LoRA `task_type=SEQ_2_SEQ_LM`
- checkpoint validation metrics logged to MLflow
- a speaker/session-disjoint validation set **and** a separate untouched
  holdout. The coverage audit found 9 validation candidates and **zero**
  holdouts; Pidgin has neither, with only 2 speakers shared across both sides.
