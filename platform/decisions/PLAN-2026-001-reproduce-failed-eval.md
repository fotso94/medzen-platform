# PLAN-2026-001 — Reproduce the failed-candidate evaluation

**Status: PREPARED, NOT LAUNCHED.** Awaiting approval. Option **A** selected.
Purpose: reproduce base-vs-failed-candidate with in-repo tooling so
`EVAL-2026-001` can move from EXTERNAL to independently reproduced — or not.

> ## DIAGNOSTIC_ONLY
> The Pidgin set shares **both speakers and sessions** with training and has
> already informed this investigation. This run can reproduce the failure. It
> **cannot** select a checkpoint, validate a fix, or support promotion. The
> evaluator writes `"purpose": "DIAGNOSTIC_ONLY"` into its own output so a
> later reader cannot mistake it for validation.

---

## The constraint you need to decide on

You asked for "pinned evaluator image/code at the approved commit and recorded
digest". Those cannot currently be the same object:

| | |
|---|---|
| Pinned image | `sha256:fc6972a5143a943ed4fcbbc6121eb1c0e43ff83065c75df07f1284ba60c94e8a` |
| Commit baked into it | `202b005` |
| Launch commit | **determined at publication time** — see below |
| `scripts/evaluate_candidate.py` at `202b005` | **does not exist** |

No commit is hardcoded here. `publish_bundle.py` publishes current `HEAD`,
which has already moved past the `7814832` an earlier draft of this plan named
and will move again as the launcher lands. The launch uses the final clean
commit, and its **full 40-char SHA and TAR_SHA256 are recorded from the
publisher's own output** at publication time — not predicted in advance.

The image predates the evaluator, the collator fix, and the corrected
`label_length.py`. Three ways forward:

**A — Image for environment, bundle for code (recommended).** Run the pinned
image for its verified dependency set, and inject the approved commit's code
through the existing publish-bundle trust chain: `TAR_SHA256` is passed in
user-data, never read from S3, and the archive is verified before anything
executes. Two recorded digests instead of one, both meaningful. No rebuild, no
new 6 GB artifact, ~9 minutes saved and one fewer thing to scan.

**B — Rebuild the image at the launch commit.** One digest covering everything.
Costs a builder instance (~9 min, ~$0.15), a fresh ECR scan, and produces a second 6 GB
image whose only purpose is a 30-minute evaluation.

**C — Reuse `fc6972a5` unmodified.** Not viable: the evaluator is not in it.

Everything below assumes **A**. Say the word and I will rewrite for **B**.

---

## Exact commands

**1 — publish the code bundle** (writes to `candidates/bootstrap/` only):

```
python scripts/publish_bundle.py
```

It refuses a dirty tree and prints `GIT_SHA=<40 hex>` and `TAR_SHA256=<64 hex>`.
Both go verbatim into the approval packet; the TAR hash is the root of trust for
step 2 and is passed in user-data, never read back from S3.

**2 — render user-data** from `pipeline/eval_userdata.sh`:

```
IMAGE_DIGEST=sha256:fc6972a5143a943ed4fcbbc6121eb1c0e43ff83065c75df07f1284ba60c94e8a \
GIT_SHA=<from step 1> TAR_SHA256=<from step 1> \
ADAPTER_URI=s3://medzen-speech/candidates/asr/23868bab2d8448759fc1b9ed26156952/final \
ADAPTER_SHA256=17e1b7381b7b3fdb362ecb692d72b92a2dc295d7ee79ff6367a8d6a9c7cd3195 \
WATCHDOG_SECONDS=1800 \
  envsubst '${IMAGE_DIGEST} ${GIT_SHA} ${TAR_SHA256} ${ADAPTER_URI} ${ADAPTER_SHA256} ${WATCHDOG_SECONDS}' \
  < pipeline/eval_userdata.sh > /tmp/ud.sh
bash -n /tmp/ud.sh && sha256sum /tmp/ud.sh
```

The user-data checksum goes in the approval packet so the launched bytes are
the reviewed bytes.

**3 — what the instance runs**, entrypoint overridden, verified code read-only:

```
docker run --rm --gpus all \
  -e MEDZEN_IMAGE_DIGEST=... -e MEDZEN_CODE_GIT_SHA=... -e MEDZEN_CODE_TAR_SHA256=... \
  -e MEDZEN_EVAL_CACHE=/cache \
  -v /opt/evalsrc/src:/opt/medzen:ro -v /opt/medzen-eval-out:/out -v /opt/medzen-eval-cache:/cache \
  --entrypoint python <image@digest> scripts/evaluate_candidate.py \
    --language pidgin --task tts --eval-version v1 --lang-token en \
    --adapter <uri> --expect-adapter-sha256 <hash> --out /out/evaluation.json
```

Two arms only: untouched base, and the failed final adapter. No checkpoint
adapters. Language `en` and task `transcribe` forced for both arms. Without
`--entrypoint` this would run the image's baked `202b005` **trainer**.

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
| Ceiling 30 min (1800 s watchdog) | **$0.503** |
| 100 GB gp3 for 0.5 h | ~$0.007 |
| **Maximum** | **≈ $0.51** |

Expected actual: ~12 min including image pull and base-model fetch — 44 clips ×
2 arms at roughly 0.4–1.2 s each is under two minutes of decoding.

## Fail-closed guards the evaluator now enforces

| Guard | On violation |
|---|---|
| `MEDZEN_IMAGE_DIGEST` / `_CODE_GIT_SHA` / `_CODE_TAR_SHA256` present and well-formed | refuse |
| Base `MANIFEST.json` raw sha256 == `6a1987d4…` | refuse |
| Eval manifest sha256 == `3f642616…` | refuse |
| Exactly one `--expect-adapter-sha256` per `--adapter`, 64 lowercase hex | refuse |
| Adapter hash matches | refuse |
| CUDA available (no CPU/MPS fallback) | refuse |
| Every returned sequence starts with the exact pinned prompt | refuse |
| Duplicate adapter result key | refuse |

Provenance comes from the environment, not `git rev-parse` — the published
bundle has no `.git`, so a git-derived record would silently claim no commit.

## Scope limits

Nothing is trained, swept, registered, promoted, merged, quantized or deployed.
No IAM change. The trainer role is already write-denied on `curated/*`, `raw/*`,
`eval/*` and `models/*`, so the instance physically cannot alter the eval set it
scores. Output is aggregate metrics plus per-utterance rows keyed by audio
checksum; the evaluator emits no transcript anywhere.

## What will be reported afterwards

| Check | External comparison target |
|---|---|
| Base WER / CER | **0.5133 / 0.3780** |
| Candidate WER / CER | **7.2231 / 4.2184** |

**These are targets, not values this evaluator is guaranteed to reproduce.**
The external run's evaluator code, library versions and text normalization were
never recorded. WER is highly sensitive to normalization -- punctuation, casing
and number handling alone move it by several points -- so a difference of a few
percent would say the two harnesses differ, not that the finding is wrong. What
must reproduce is the *shape*: base near 0.5 and candidate catastrophically
above 1.0, with the candidate's generated lengths far exceeding the base's.
| EOS rate, cap-hit rate | measured, both arms |
| Prompt / generated / total tokens | measured, both arms |
| Stopping reasons | `eos` vs `max_new_tokens` vs `other` |

The nearest comparable base figure is **0.5133** -- not the 0.5382 the coverage
audit previously reported bare, nor the 0.5257 selected decode policy. Four numbers
circulate for these same 44 clips and they differ by runtime, model and decode
policy rather than by model quality:

| WER | decode | runtime | model revision |
|---|---|---|---|
| 0.5694 | auto / native | mlx_whisper | `49e6aa28` |
| 0.5382 | native | mlx_whisper | `49e6aa28` |
| 0.5257 | en_token (selected policy) | mlx_whisper | `49e6aa28` |
| **0.5133** | **language=en forced** | **transformers/torch** | **`06f233fe`** |

Only the last shares this run's runtime and model revision, which is why it is
the comparison target. Even so its normalization is unrecorded, so agreement is
expected to be approximate. **Do not difference figures across rows.**

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
