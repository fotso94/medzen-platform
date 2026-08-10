#!/usr/bin/env bash
# Execute only an independently reviewed and owner-approved B6 packet 2026-015.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
cd "$repo_root"
export PYTHONPATH="$repo_root${PYTHONPATH:+:$PYTHONPATH}"

if [[ $# -ne 2 ]]; then
  echo "usage: $0 OWNER_AUTHORIZATION RECEIPTS_DIR" >&2
  exit 2
fi
authorization="$1"
receipts_dir="$2"
expected_receipts="$repo_root/platform/evidence/receipts/B6-2026-015-LIVE"
token_file="/private/tmp/medzen-b6-6-client-token"

[[ "${AWS_PROFILE:-}" == "medzen" ]] || { echo "REFUSING: AWS_PROFILE=medzen is required" >&2; exit 2; }
[[ -f "$authorization" ]] || { echo "REFUSING: owner authorization is absent" >&2; exit 2; }
[[ "$receipts_dir" == "$expected_receipts" ]] || { echo "REFUSING: exact receipt directory is required" >&2; exit 2; }
[[ ! -e "$receipts_dir" ]] || { echo "REFUSING: receipt directory already exists" >&2; exit 2; }
[[ ! -e "$token_file" ]] || { echo "REFUSING: local synthetic token unexpectedly exists" >&2; exit 2; }
[[ -z "$(git status --porcelain=v1)" ]] || { echo "REFUSING: execution requires a clean reviewed worktree" >&2; exit 2; }

plan_prefix="/private/tmp/b6-015-$PPID"
mutation_started=0

cleanup_after_refusal() {
  original_status=$?
  trap - EXIT INT TERM
  if [[ $original_status -eq 0 || $mutation_started -eq 0 ]]; then
    exit "$original_status"
  fi
  set +e
  /bin/rm -f -- "$token_file"
  cleanup_plan="${plan_prefix}-cleanup.tfplan"
  cleanup_targets=(
    -target=aws_secretsmanager_secret.b6_client_keys
    -target=aws_secretsmanager_secret_policy.b6_client_keys
    -target=aws_iam_role_policy.b6_client_keys_kms
  )
  scripts/terraform_medzen.sh plan -input=false -out="$cleanup_plan" \
    -var=account_id=558069890522 \
    -var=registry_publisher_principal_arn=arn:aws:iam::558069890522:user/s.fotso \
    -var=enable_b6_client_keys=false "${cleanup_targets[@]}"
  plan_status=$?
  if [[ $plan_status -eq 0 ]]; then
    change_count="$(terraform -chdir=infra show -json "$cleanup_plan" | jq '[.resource_changes[]? | select(.change.actions != ["no-op"] and .change.actions != ["read"])] | length')"
    if [[ "$change_count" != "0" ]]; then
      .venv/bin/python scripts/check_b6_client_secret_restoration_2026_015_plan.py --mode cleanup "$cleanup_plan"
      guard_status=$?
      if [[ $guard_status -eq 0 ]]; then
        scripts/terraform_medzen.sh apply -input=false -auto-approve "$cleanup_plan"
      fi
    fi
  fi
  .venv/bin/python scripts/run_b6_client_secret_restoration_2026_015.py cleanup \
    --authorization "$authorization" --receipts-dir "$receipts_dir" --apply
  cleanup_status=$?
  if [[ $cleanup_status -ne 0 ]]; then
    echo "REFUSING: packet 2026-015 cleanup proof failed; later B6.6 work remains blocked" >&2
    exit 70
  fi
  exit "$original_status"
}
trap cleanup_after_refusal EXIT INT TERM

# Stage 0: exact reviewed binding, pending-secret and zero-compute preflight.
[[ "$(aws sts get-caller-identity --profile medzen --query Account --output text)" == "558069890522" ]]
[[ "$(aws sts get-caller-identity --profile medzen --query Arn --output text)" == "arn:aws:iam::558069890522:user/s.fotso" ]]
for nodegroup in cpu gpu; do
  [[ "$(aws eks describe-nodegroup --cluster-name medzen-speech --nodegroup-name "$nodegroup" --region eu-central-1 --profile medzen --query 'nodegroup.scalingConfig.desiredSize' --output text)" == "0" ]]
done
[[ "$(aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names eks-cpu-32cfd795-fa28-d1d9-1b8c-2ed678be1772 eks-gpu-b8cfd795-fa28-70a1-b844-258a0f0adc26 --region eu-central-1 --profile medzen --query 'sum(AutoScalingGroups[].DesiredCapacity)' --output text)" == "0" ]]
[[ "$(aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names eks-cpu-32cfd795-fa28-d1d9-1b8c-2ed678be1772 eks-gpu-b8cfd795-fa28-70a1-b844-258a0f0adc26 --region eu-central-1 --profile medzen --query 'length(AutoScalingGroups[].Instances[])' --output text)" == "0" ]]
[[ "$(aws ssm get-parameters-by-path --path /medzen/registry/serving --recursive --with-decryption --region eu-central-1 --profile medzen --query 'length(Parameters[?Name==`/medzen/registry/serving/current`])' --output text)" == "0" ]]
.venv/bin/python scripts/run_b6_client_secret_restoration_2026_015.py preflight \
  --authorization "$authorization" --receipts-dir "$receipts_dir" --apply

# Stage 1: restore only the exact recoverable secret. No GetSecretValue call exists.
mutation_started=1
.venv/bin/python scripts/run_b6_client_secret_restoration_2026_015.py restore \
  --authorization "$authorization" --receipts-dir "$receipts_dir" --apply

# Stage 2: import only the restored exact ARN into its reviewed Terraform address.
scripts/terraform_medzen.sh import -input=false \
  -var=account_id=558069890522 \
  -var=registry_publisher_principal_arn=arn:aws:iam::558069890522:user/s.fotso \
  -var=enable_b6_client_keys=true \
  'aws_secretsmanager_secret.b6_client_keys[0]' \
  'arn:aws:secretsmanager:eu-central-1:558069890522:secret:medzen/client-api-keys-NxZGxE'
state_json="$(scripts/terraform_medzen.sh state pull)"
state_lineage="$(jq -r '.lineage' <<<"$state_json")"
state_serial="$(jq -r '.serial' <<<"$state_json")"
import_payload="$(jq -nc \
  --arg lineage "$state_lineage" \
  --argjson serial "$state_serial" \
  '{state_lineage:$lineage,state_serial:$serial,address:"aws_secretsmanager_secret.b6_client_keys[0]",secret_arn:"arn:aws:secretsmanager:eu-central-1:558069890522:secret:medzen/client-api-keys-NxZGxE"}')"
.venv/bin/python scripts/run_b6_client_secret_restoration_2026_015.py record-terraform-import \
  --authorization "$authorization" --receipts-dir "$receipts_dir" \
  --payload-json "$import_payload" --apply

# Stage 3: normalize only the proven imported representation, and only if needed.
normalize_plan="${plan_prefix}-normalize.tfplan"
scripts/terraform_medzen.sh plan -input=false -out="$normalize_plan" \
  -var=account_id=558069890522 \
  -var=registry_publisher_principal_arn=arn:aws:iam::558069890522:user/s.fotso \
  -var=enable_b6_client_keys=true \
  -target=aws_secretsmanager_secret.b6_client_keys
normalize_guard="$(.venv/bin/python scripts/check_b6_client_secret_restoration_2026_015_plan.py --mode normalize-if-needed "$normalize_plan")"
if [[ "$normalize_guard" == *"outcome=APPLY_EXACT_NORMALIZATION" ]]; then
  scripts/terraform_medzen.sh apply -input=false -auto-approve "$normalize_plan"
  normalize_mode="APPLIED_EXACT_NORMALIZATION"
elif [[ "$normalize_guard" == *"outcome=NO_NORMALIZATION_REQUIRED" ]]; then
  normalize_mode="NO_NORMALIZATION_REQUIRED"
else
  echo "REFUSING: unknown normalization guard result" >&2
  exit 2
fi
normalize_residual="${plan_prefix}-normalize-residual.tfplan"
scripts/terraform_medzen.sh plan -input=false -out="$normalize_residual" \
  -var=account_id=558069890522 \
  -var=registry_publisher_principal_arn=arn:aws:iam::558069890522:user/s.fotso \
  -var=enable_b6_client_keys=true \
  -target=aws_secretsmanager_secret.b6_client_keys
.venv/bin/python scripts/check_b6_client_secret_restoration_2026_015_plan.py --mode residual-secret "$normalize_residual"
state_json="$(scripts/terraform_medzen.sh state pull)"
normalization_payload="$(jq -nc \
  --arg mode "$normalize_mode" \
  --arg plan "$(shasum -a 256 "$normalize_plan" | awk '{print $1}')" \
  --arg residual "$(shasum -a 256 "$normalize_residual" | awk '{print $1}')" \
  --arg lineage "$(jq -r '.lineage' <<<"$state_json")" \
  --argjson serial "$(jq -r '.serial' <<<"$state_json")" \
  '{mode:$mode,plan_sha256:$plan,residual_plan_sha256:$residual,state_lineage:$lineage,state_serial:$serial}')"
.venv/bin/python scripts/run_b6_client_secret_restoration_2026_015.py record-terraform-normalization \
  --authorization "$authorization" --receipts-dir "$receipts_dir" \
  --payload-json "$normalization_payload" --apply

# Stage 4: create the resource policy and additive KMS policy in a separate plan.
reconcile_plan="${plan_prefix}-reconcile.tfplan"
boundary_targets=(
  -target=aws_secretsmanager_secret.b6_client_keys
  -target=aws_secretsmanager_secret_policy.b6_client_keys
  -target=aws_iam_role_policy.b6_client_keys_kms
)
scripts/terraform_medzen.sh plan -input=false -out="$reconcile_plan" \
  -var=account_id=558069890522 \
  -var=registry_publisher_principal_arn=arn:aws:iam::558069890522:user/s.fotso \
  -var=enable_b6_client_keys=true "${boundary_targets[@]}"
.venv/bin/python scripts/check_b6_client_secret_restoration_2026_015_plan.py --mode reconcile "$reconcile_plan"
scripts/terraform_medzen.sh apply -input=false -auto-approve "$reconcile_plan"
reconcile_residual="${plan_prefix}-reconcile-residual.tfplan"
scripts/terraform_medzen.sh plan -input=false -out="$reconcile_residual" \
  -var=account_id=558069890522 \
  -var=registry_publisher_principal_arn=arn:aws:iam::558069890522:user/s.fotso \
  -var=enable_b6_client_keys=true "${boundary_targets[@]}"
.venv/bin/python scripts/check_b6_client_secret_restoration_2026_015_plan.py --mode residual-all "$reconcile_residual"
state_json="$(scripts/terraform_medzen.sh state pull)"
reconciliation_payload="$(jq -nc \
  --arg plan "$(shasum -a 256 "$reconcile_plan" | awk '{print $1}')" \
  --arg residual "$(shasum -a 256 "$reconcile_residual" | awk '{print $1}')" \
  --arg lineage "$(jq -r '.lineage' <<<"$state_json")" \
  --argjson serial "$(jq -r '.serial' <<<"$state_json")" \
  '{plan_sha256:$plan,residual_plan_sha256:$residual,state_lineage:$lineage,state_serial:$serial,resource_policy_sha256:"318a323fe01349dca140c8eff48cfef9da1cda163b6cc7616d3da718c0d20cb1",kms_policy_sha256:"8a9c8064b7a66e8003e326b4ae02a1288c7d304fd471734146f70fbaacbd5dd4"}')"
.venv/bin/python scripts/run_b6_client_secret_restoration_2026_015.py record-terraform-reconciliation \
  --authorization "$authorization" --receipts-dir "$receipts_dir" \
  --payload-json "$reconciliation_payload" --apply

# Stages 5 and 6: create fresh material, then prove both prior versions unstaged.
.venv/bin/python scripts/run_b6_client_secret_restoration_2026_015.py rotate \
  --authorization "$authorization" --receipts-dir "$receipts_dir" --apply
.venv/bin/python scripts/run_b6_client_secret_restoration_2026_015.py verify \
  --authorization "$authorization" --receipts-dir "$receipts_dir" --apply

# No compute or serving state may have changed during this packet.
for nodegroup in cpu gpu; do
  [[ "$(aws eks describe-nodegroup --cluster-name medzen-speech --nodegroup-name "$nodegroup" --region eu-central-1 --profile medzen --query 'nodegroup.scalingConfig.desiredSize' --output text)" == "0" ]]
done
[[ "$(aws ssm get-parameters-by-path --path /medzen/registry/serving --recursive --with-decryption --region eu-central-1 --profile medzen --query 'length(Parameters[?Name==`/medzen/registry/serving/current`])' --output text)" == "0" ]]

mutation_started=0
trap - EXIT INT TERM
echo "PASS_B6_AWS_CHANGE_PACKET_2026_015_CREDENTIAL_RESTORATION"
