# B0 — Region & account preparation · STATUS

Region **eu-central-1**, account **558069890522**, profile `medzen`.
All findings below are from read-only calls. **No resource was created, modified
or deleted.**

| # | Item | Status |
|---|---|---|
| B0.1 | VPC + subnets identified | ✅ done — **with a finding, see §1** |
| B0.2 | Bedrock model access | ✅ already granted |
| B0.3 | Residency decision + smoke test | ✅ done — `eu.` profile verified working |
| B0.4 | GPU service quota | ❌ **BLOCKED — action required** |
| B0.5 | TTS gateway in eu-central-1 | ✅ **already deployed** — see §3 |
| B0.6 | Tear down other-region experiments | ✅ nothing to do — we created nothing |

---

## 1. The VPC is not empty — it hosts MedZen production

`vpc-051aa9df8b64bf141` (default, `172.31.0.0/16`, 3 public subnets, 25 ENIs)
already contains:

| Workload | Exposure | Notes |
|---|---|---|
| `medzen-ehrbase-alb` | **internet-facing** | EHRbase — openEHR clinical record repository. PHI. |
| `cache-proxy-prod` | internal | production |
| `cache-proxy-test` | internal | |
| `medzen-tts-dev` | internal | the speech gateway, already live |

Plus 21 `medzen*` ECR repos (full Supabase stack, PowerSync, otel-sidecar,
`bedrock-openai-shim`) and ~90 `/medzen-health-pro/{dev,test,prod}/*` SSM
parameters holding the mobile app's config.

**Why this matters for the subnet decision.** Option B (public subnets) was
chosen when the VPC looked empty. It is not empty. Putting EKS GPU nodes with
public IPs into the same three subnets as an internet-facing clinical-records
ALB changes the risk profile: a single mis-scoped security group is the only
thing between the two. Option A (add 3 private subnets + 1 NAT, ~$35/mo) is
purely additive to this VPC, changes nothing that exists, and removes that
class of mistake.

**Recorded decision: option B**, with `subnet_type: public` and a co-tenancy
warning in `platform/services.yaml`. Flagged for revisit before production.

## 2. Bedrock — decided, verified

```
FAIL   anthropic.claude-sonnet-5                  on-demand throughput isn't supported
PASS   eu.anthropic.claude-sonnet-5               -> MEDZEN OK
PASS   eu.anthropic.claude-sonnet-4-5-20250929    -> MEDZEN OK
PASS   eu.anthropic.claude-haiku-4-5-20251001     -> MEDZEN OK
```

Bare model ids cannot be invoked at all — current Claude models on Bedrock
require an inference profile. **"Locked single-region Frankfurt" is therefore
not an available option.** All 12 `eu.` Claude profiles are ACTIVE.

- Primary: `eu.anthropic.claude-sonnet-5`
- Cheap path: `eu.anthropic.claude-haiku-4-5-20251001-v1:0`
- Routing: EU regions only, never outside the EU
- No use-case form required in this region (unlike us-east-1)

## 3. The TTS gateway is already running

```
cluster       medzen-tts-dev        launch  FARGATE
service       medzen-tts-gateway    1/1 running, ACTIVE
task def      medzen-tts-gateway:3
ALB           medzen-tts-dev        internal
created       2026-07-27
registry      /medzen/tts/dev/voices
ECR           medzen-tts-gateway    3 images
```

This is **not** the code in `../medzen-tts-gateway` — the deployed voice
registry uses a richer schema than mine:

```json
{"pidgin": {"default": "female", "display_name": "Nigerian Pidgin English",
            "voices": {"female": {"reference_id": "7b85fba3..."},
                       "male":   {"reference_id": "49dfb66e..."}}}}
```

Multiple voices per language with a default — better than my single `voice_id`.
**Reconciliation needed** (tracked, not yet done):

1. Align `schemas/language.schema.json` `tts` block to multi-voice, so the
   platform registry can describe what is actually deployed.
2. Decide whether `medzen-tts-gateway` stays on ECS or moves to EKS at B6.
   It works, it is internal, and moving it buys tidiness rather than function —
   staying on ECS is defensible.
3. Reconcile registry paths: live gateway reads `/medzen/tts/dev/voices`;
   the platform registry publishes `/medzen/registry/*`. Two sources of truth
   for voices is one too many.

## 4. GPU quota — the blocker

```
All G and VT Spot Instance Requests   (L-3819A6DF)    0 vCPUs
Running On-Demand G and VT instances  (L-DB2E81BA)    4 vCPUs
```

g6/g5/g4dn/g6e `.xlarge` are all offered in all three AZs, spot ~$0.44–0.48/hr.
But a `g6.xlarge` is 4 vCPUs, so today the account can run **one** GPU instance
on demand and **zero** on spot. Training and serving cannot coexist, and the
spot-based training plan cannot run at all.

**Request both to 16 vCPUs** (= 4× g6.xlarge): serving + trainer + the later
Qwen3-8B and Qwen3-TTS pods, without a second ticket.

```bash
aws service-quotas request-service-quota-increase --profile medzen \
  --region eu-central-1 --service-code ec2 \
  --quota-code L-3819A6DF --desired-value 16     # spot G/VT

aws service-quotas request-service-quota-increase --profile medzen \
  --region eu-central-1 --service-code ec2 \
  --quota-code L-DB2E81BA --desired-value 16     # on-demand G/VT
```

Justification: *Multilingual speech AI platform — GPU inference for ASR serving
on EKS plus periodic model fine-tuning on EC2 spot.*

Nothing in B0 or B1 needs a GPU, so this can run in the background.

## 5. Other regions — nothing of ours

`us-east-1` and `eu-west-3` contain unrelated projects (`ai-dev-platform`,
`companyos`, `talentscout`, `zivvo`) and three `medzen*` secrets predating this
work (Feb–Apr 2026). **We created nothing in any region.** B0.6 is a no-op —
there is nothing to tear down, and nothing should be deleted.
