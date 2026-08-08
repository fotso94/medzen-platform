# MedZen Speech Platform — Architecture (A1 + A2)

Materialization of Part A of `MedZen_Speech_Platform_Base_v5.pdf`.
Region **eu-central-1**. This document and `platform/services.yaml` are the
authority; everything under `platform/iam/` and `platform/k8s/base/` is
generated and must not be hand-edited.

```
edit platform/services.yaml  →  python platform/generate.py  →  commit both
```

---

## A1 · Topology and trust boundaries

Three planes. They share **S3, ECR and the registry — nothing else**.

```
┌─ TRAINING PLANE (offline, EC2 spot) ────────────────────────────────┐
│  trainer  →  MLflow  →  gates  →  s3://medzen-speech/approved/      │
│  IAM: no secrets, no Bedrock, no eval writes (explicit Deny)        │
└──────────────────────────┬──────────────────────────────────────────┘
                           │ signed artifact + SHA-256
┌─ SERVING PLANE (EKS) ─────▼─────────────────────────────────────────┐
│  MedZen backend → internal ALB → speech-orchestrator (only route)   │
│                                    ├→ asr-runtime    (GPU)          │
│                                    ├→ llm-gateway ─→ rag-index      │
│                                    └→ tts-gateway                   │
│  Everything except the orchestrator is ClusterIP.                   │
└──────────────────────────┬──────────────────────────────────────────┘
                           │ registry alias (git → SSM)
┌─ CONTROL PLANE ───────────▼─────────────────────────────────────────┐
│  registry/languages/*.yaml + registry/gates/*.yaml                  │
│  The ONLY path from a trained model to production traffic.          │
└─────────────────────────────────────────────────────────────────────┘
```

### The four invariants

1. **One public surface.** Only `speech-orchestrator` has an ALB route. ASR,
   LLM, TTS and RAG have no authentication of their own and must never be
   externally routable — the security-group rule is the enforcement, not
   convention.
2. **Training cannot reach production.** `medzen-trainer-role` carries explicit
   `Deny` on `secretsmanager:*` and `bedrock:*`, and on writes to `eval/*`.
   This is the isolation SageMaker sells, obtained structurally for free.
3. **Models are data, not images.** Containers hold runtime code only. The
   `model-loader` init container pulls the authorized artifact, verifies its
   manifest and file/tree SHA-256 values, writes an atomic marker, then exits
   0. The ASR runtime validates that marker, loads the model and runs the
   serving-path inference smoke before becoming Ready. Production promotion
   and rollback remain registry alias changes — no image rebuild in either
   direction.
4. **The registry is the only door.** A model reaches traffic only by passing
   its gates, being registered, and having its language's `approved_version`
   bumped in a reviewed PR. Nobody can push weights directly.

### Request flow — file mode

```
POST /v1/conversations/speech
  orchestrator: authenticate → VAD segment → resolve language from registry
              → EMERGENCY DETECTION (pre-LLM, no shed path)
  asr-runtime : transcribe with registry decode_strategy → verbatim + normalized
  llm-gateway : retrieve (rag-index) → Bedrock with per-language policy → answer
  tts-gateway : Fish → self-hosted (if approved) → text-only
  orchestrator: assemble response with EVERY model version + latency breakdown
```

### Request flow — streaming

```
WS /v1/conversations/stream
  audio frames ⇄ partial transcripts ⇄ response events ⇄ audio chunks
  bounded queues (partials 4, audio 8) · drop-oldest for partials only
  cancel/barge-in propagates to all three within 250 ms
  per-provider circuit breakers, state exported as a metric
```

---

## A2 · Services

Full machine-readable spec: `platform/services.yaml`.

| Service | Pool | Port | Replicas | Role | Why it is separate |
|---|---|---|---|---|---|
| `speech-orchestrator` | CPU | 8080 | 2–6 HPA | `medzen-orch-role` | Only public surface; owns policy and session so models stay stateless |
| `asr-runtime` | **GPU** | 8081 | 1 fixed | `medzen-asr-role` | Different hardware, slowest start; an API deploy must never restart it |
| `llm-gateway` | CPU | 8082 | 2–4 HPA | `medzen-llm-role` | Hides Bedrock↔Qwen swap behind one interface |
| `tts-gateway` | CPU | 8080 | 1–3 HPA | `medzen-tts-role` | Not yet built in this repository; will hide Fish↔self-hosted↔text-only fallback and own the cache |
| `rag-index` | CPU | 8083 | 2–4 HPA | `medzen-rag-role` | Content versioning is independent of model versioning |
| `model-loader` | init | — | per pod | shares the ASR pod role | Verification-only artifact fetch; Kubernetes gives init and main containers the same Pod identity, and failure leaves the pod unready |
| `trainer` | EC2 | — | spot | `medzen-trainer-role` | Offline; plain Docker so it runs unchanged on SageMaker later |

### Settings that are deliberate, not defaults

- **`asr-runtime` startupProbe = 30 × 10s (5 min).** The image is 6–8 GB and the
  model takes 30–60s to load. Web-app probe defaults crash-loop this pod and the
  events do not point at the cause.
- **No HPA on `asr-runtime`.** A second replica means a second GPU node and
  ~5 min of provisioning. Fixed at 1 for the MVP.
- **GPU taint + toleration.** Without the taint, CPU pods schedule onto the most
  expensive node in the cluster.
- **`emptyDir` 20 Gi for `/models`.** Sized for a large-v3 CTranslate2 artifact
  plus headroom; node-local, so pod restart re-verifies the checksum.
- **ListBucket is condition-scoped to its prefix.** A resource path does not
  restrict `s3:ListBucket` — only `s3:prefix` does.

---

## Open decisions — must close before B1

| # | Decision | Blocks | Owner |
|---|---|---|---|
| 1 | eu-central-1 **account id** → `services.yaml: meta.account_id` | all IAM/ECR ARNs | B0.1 |
| 2 | **VPC id + ≥2 private subnet ids** | Terraform network module | B0.1 |
| 3 | Bedrock **residency**: `eu.` cross-region profile vs locked Frankfurt | `llm-role` ARNs | B0.3 |
| 4 | **G-family availability** in eu-central-1 (g6 vs g5 vs g4dn) | GPU node group + quota | B0.4 |
| 5 | Bucket names — confirm `medzen-speech` / `medzen-audio-cache` are free | S3 module | B0.1 |

Until #1 is answered every generated ARN contains `REPLACE_AT_B0`; `scripts/validate_architecture.py` fails on that string by design, so it cannot silently reach Terraform.

---

## Deviation policy

If implementation forces a change to the topology, a role, a probe or a
resource limit: edit `services.yaml`, regenerate, and note it here. The plan
document stays the truth. Undocumented drift between this repo and the cluster
is a defect, not a shortcut.
