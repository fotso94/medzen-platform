#!/usr/bin/env bash
# Packet 2026-018 stage 0: restore, rotate and verify fresh synthetic credentials.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
cd "$repo_root"
export PYTHONPATH="$repo_root${PYTHONPATH:+:$PYTHONPATH}"

if [[ $# -ne 4 ]]; then
  echo "usage: $0 AUTHORIZATION PACKET_SHA256 RECEIPTS_DIR TOKEN_FILE" >&2
  exit 2
fi
authorization="$1"
packet_sha256="$2"
receipts_root="$3"
token_file="$4"
credential_receipts="$receipts_root/credential"
expected_root="$repo_root/platform/evidence/receipts/B6-2026-018-LIVE"
executor="scripts/b6_6_images_before_endpoints_credential_stage.py"

[[ "${AWS_PROFILE:-}" == "medzen" ]] || { echo "REFUSING: AWS_PROFILE=medzen is required" >&2; exit 2; }
[[ -f "$authorization" ]] || { echo "REFUSING: owner authorization is absent" >&2; exit 2; }
[[ "$receipts_root" == "$expected_root" ]] || { echo "REFUSING: exact packet-2026-018 receipt directory is required" >&2; exit 2; }
[[ ! -e "$receipts_root" ]] || { echo "REFUSING: packet-2026-018 receipt directory already exists" >&2; exit 2; }
[[ "$token_file" == "/private/tmp/medzen-b6-6-client-token" ]] || { echo "REFUSING: exact synthetic token path is required" >&2; exit 2; }
[[ ! -e "$token_file" ]] || { echo "REFUSING: local synthetic token unexpectedly exists" >&2; exit 2; }

.venv/bin/python - "$authorization" "$packet_sha256" "$repo_root" <<'PY'
import sys
from pathlib import Path
from scripts.b6_6_images_before_endpoints_bindings import validate
validate(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]))
PY

plan_prefix="/private/tmp/b6-018-credential-$PPID"
mutation_started=0

cleanup_after_refusal() {
  original_status=$?
  trap - EXIT INT TERM
  if [[ $original_status -eq 0 || $mutation_started -eq 0 ]]; then exit "$original_status"; fi
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
      .venv/bin/python scripts/check_b6_client_secret_restoration_2026_015_plan.py --mode cleanup "$cleanup_plan" && \
        scripts/terraform_medzen.sh apply -input=false -auto-approve "$cleanup_plan"
    fi
  fi
  cleanup_status=0
  if [[ ! -e "$credential_receipts/cleanup.json" ]]; then
    .venv/bin/python "$executor" cleanup \
      --authorization "$authorization" --packet-sha256 "$packet_sha256" \
      --receipts-dir "$credential_receipts" --apply
    cleanup_status=$?
  fi
  [[ $cleanup_status -eq 0 ]] || { echo "REFUSING: packet-2026-018 stage-0 cleanup proof failed" >&2; exit 70; }
  exit "$original_status"
}
trap cleanup_after_refusal EXIT INT TERM

.venv/bin/python "$executor" preflight \
  --authorization "$authorization" --packet-sha256 "$packet_sha256" \
  --receipts-dir "$credential_receipts" --apply

mutation_started=1
.venv/bin/python "$executor" restore \
  --authorization "$authorization" --packet-sha256 "$packet_sha256" \
  --receipts-dir "$credential_receipts" --apply

scripts/terraform_medzen.sh import -input=false \
  -var=account_id=558069890522 \
  -var=registry_publisher_principal_arn=arn:aws:iam::558069890522:user/s.fotso \
  -var=enable_b6_client_keys=true \
  'aws_secretsmanager_secret.b6_client_keys[0]' \
  'arn:aws:secretsmanager:eu-central-1:558069890522:secret:medzen/client-api-keys-NxZGxE'
state_json="$(scripts/terraform_medzen.sh state pull)"
import_payload="$(jq -nc \
  --arg lineage "$(jq -r '.lineage' <<<"$state_json")" \
  --argjson serial "$(jq -r '.serial' <<<"$state_json")" \
  '{state_lineage:$lineage,state_serial:$serial,address:"aws_secretsmanager_secret.b6_client_keys[0]",secret_arn:"arn:aws:secretsmanager:eu-central-1:558069890522:secret:medzen/client-api-keys-NxZGxE"}')"
.venv/bin/python "$executor" record-terraform-import \
  --authorization "$authorization" --packet-sha256 "$packet_sha256" \
  --receipts-dir "$credential_receipts" --payload-json "$import_payload" --apply

normalize_plan="${plan_prefix}-normalize.tfplan"
scripts/terraform_medzen.sh plan -input=false -out="$normalize_plan" \
  -var=account_id=558069890522 \
  -var=registry_publisher_principal_arn=arn:aws:iam::558069890522:user/s.fotso \
  -var=enable_b6_client_keys=true -target=aws_secretsmanager_secret.b6_client_keys
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
  -var=enable_b6_client_keys=true -target=aws_secretsmanager_secret.b6_client_keys
.venv/bin/python scripts/check_b6_client_secret_restoration_2026_015_plan.py --mode residual-secret "$normalize_residual"
state_json="$(scripts/terraform_medzen.sh state pull)"
normalization_payload="$(jq -nc \
  --arg mode "$normalize_mode" \
  --arg plan "$(shasum -a 256 "$normalize_plan" | awk '{print $1}')" \
  --arg residual "$(shasum -a 256 "$normalize_residual" | awk '{print $1}')" \
  --arg lineage "$(jq -r '.lineage' <<<"$state_json")" \
  --argjson serial "$(jq -r '.serial' <<<"$state_json")" \
  '{mode:$mode,plan_sha256:$plan,residual_plan_sha256:$residual,state_lineage:$lineage,state_serial:$serial}')"
.venv/bin/python "$executor" record-terraform-normalization \
  --authorization "$authorization" --packet-sha256 "$packet_sha256" \
  --receipts-dir "$credential_receipts" --payload-json "$normalization_payload" --apply

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
.venv/bin/python "$executor" record-terraform-reconciliation \
  --authorization "$authorization" --packet-sha256 "$packet_sha256" \
  --receipts-dir "$credential_receipts" --payload-json "$reconciliation_payload" --apply

.venv/bin/python "$executor" rotate \
  --authorization "$authorization" --packet-sha256 "$packet_sha256" \
  --receipts-dir "$credential_receipts" --apply
.venv/bin/python "$executor" verify \
  --authorization "$authorization" --packet-sha256 "$packet_sha256" \
  --receipts-dir "$credential_receipts" --apply
.venv/bin/python scripts/b6_6_successor_token_binding.py \
  "$token_file" "$credential_receipts/verification.json" >/dev/null

mutation_started=0
trap - EXIT INT TERM
echo "PASS_B6_6_IMAGES_BEFORE_ENDPOINTS_CREDENTIAL_STAGE_0"
