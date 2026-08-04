# B1 — Terraform foundation

**Region** eu-central-1 · **Account** 558069890522 · **Profile** `medzen`

## The safety rule for this stack

The VPC hosts **MedZen production** — an internet-facing EHRbase ALB with
clinical records, `cache-proxy-prod`, the live TTS gateway, 25 ENIs.

Therefore: **Terraform never manages a network primitive here.** The VPC,
subnets, IGW and route tables are read-only `data` sources. Verified on every
plan — see "plan safety" below. Everything this stack creates is additive and
independently destroyable.

Two other things deliberately left alone:

- **`medzen-tts-gateway` ECR repo** — already exists and the running
  `medzen-tts-dev` ECS service depends on it. Taking Terraform ownership of a
  production dependency needs an explicit `terraform import`, not a plan side
  effect.
- **`/medzen-health-pro/*` SSM parameters** — the mobile app's config. Not ours.

## Order

```bash
./bootstrap.sh                 # once, after approval: state bucket + lock table
# uncomment the backend "s3" block in providers.tf
AWS_PROFILE=medzen ../scripts/terraform_medzen.sh init -migrate-state
AWS_PROFILE=medzen ../scripts/terraform_medzen.sh plan -out=medzen.tfplan
AWS_PROFILE=medzen ../scripts/terraform_medzen.sh apply medzen.tfplan
```

The local wrapper refuses unless both the explicit profile and resolved caller
are the approved MedZen identity. This matters because Terraform's S3 backend
does not inherit the provider block's `profile` variable; an unrelated default
AWS identity otherwise fails with a misleading state-object `403`.

## Plan safety check

Run this before any apply. It must print NONE twice.

```bash
AWS_PROFILE=medzen ../scripts/terraform_medzen.sh show -json medzen.tfplan | python3 -c "
import json,sys
rc = json.load(sys.stdin)['resource_changes']
d = [r['address'] for r in rc if 'delete' in r['change']['actions']]
n = [r['address'] for r in rc if r['type'].startswith(('aws_vpc','aws_subnet','aws_route','aws_internet_gateway','aws_nat'))]
print('TO DESTROY:', d or 'NONE'); print('NETWORK MANAGED:', n or 'NONE')"
```

## IAM comes from the architecture, not from here

`iam.tf` reads `../platform/iam/*.json`, which `platform/generate.py` renders
from `platform/services.yaml`. Change a permission in `services.yaml`,
regenerate, and Terraform picks it up. The cluster cannot drift from A2.

## GPU node group

Created at `desired_size = 0`. Quota `L-DB2E81BA` is CASE_OPENED as of
2026-07-29; the group exists now so the taint, labels and NVIDIA AMI type are
already correct when capacity lands. Then it is a one-line change:

```bash
AWS_PROFILE=medzen ../scripts/terraform_medzen.sh apply -var gpu_desired_size=1
```

`lifecycle.ignore_changes` on `desired_size` means a later autoscaler cannot
fight Terraform over it.

## Known TODOs before production

- `public_access_cidrs = ["0.0.0.0/0"]` on the EKS API endpoint — restrict to
  office/CI egress.
- Subnets are **public** (option B). Option A (private + NAT, ~$35/mo) is the
  intended end state, and is purely additive to this VPC.
