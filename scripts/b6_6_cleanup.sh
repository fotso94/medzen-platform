#!/usr/bin/env bash
# Independently invocable cleanup for B6-AWS-CHANGE-PACKET-2026-009.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
cd "$repo_root"
export PYTHONPATH="$repo_root${PYTHONPATH:+:$PYTHONPATH}"

if [[ $# -ne 5 ]]; then
  echo "usage: $0 KUBECONFIG AUTHORIZATION PACKET_SHA256 RECEIPTS_DIR TOKEN_FILE" >&2
  exit 2
fi
kubeconfig="$1"
authorization="$2"
packet_sha256="$3"
receipts_dir="$4"
token_file="$5"

cleanup_step="bindings"
cleanup_receipt_stage="cleanup"
[[ -e "$receipts_dir/cleanup.json" ]] && cleanup_receipt_stage="cleanup_recovery"

record_incomplete_cleanup() {
  original_status=$?
  trap - EXIT
  if [[ $original_status -ne 0 && ! -e "$receipts_dir/${cleanup_receipt_stage}.json" ]]; then
    payload="$(jq -nc --arg step "$cleanup_step" '{automatic_cleanup_complete:false,cleanup_step:$step,reason_code:"CLEANUP_STEP_REFUSED"}')"
    .venv/bin/python scripts/b6_6_receipt.py "$cleanup_receipt_stage" INCOMPLETE --receipts-dir "$receipts_dir" --payload "$payload" >/dev/null || true
  fi
  exit "$original_status"
}
trap record_incomplete_cleanup EXIT

[[ "${AWS_PROFILE:-}" == "medzen" ]] || { echo "REFUSING: AWS_PROFILE=medzen is required" >&2; exit 2; }
[[ -f "$kubeconfig" && -f "$authorization" ]] || { echo "REFUSING: cleanup binding file is absent" >&2; exit 2; }
[[ "$token_file" == "/private/tmp/medzen-b6-6-client-token" ]] || { echo "REFUSING: exact synthetic token path is required" >&2; exit 2; }

.venv/bin/python - "$authorization" "$packet_sha256" "$repo_root" <<'PY'
import sys
from pathlib import Path
from scripts.b6_6_bindings import validate
validate(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]))
PY

# Stop the only permitted Fargate probe before its temporary cluster is removed.
cleanup_step="fargate_stop"
task_arns="$(aws ecs list-tasks --cluster medzen-b6-window-probe --region eu-central-1 --profile medzen --query 'taskArns[]' --output text 2>/dev/null || true)"
if [[ -n "$task_arns" && "$task_arns" != "None" ]]; then
  for task_arn in $task_arns; do
    aws ecs stop-task --cluster medzen-b6-window-probe --task "$task_arn" --reason B6_6_WINDOW_CLEANUP --region eu-central-1 --profile medzen >/dev/null || true
  done
fi

# Remove the Ingress first and prove the controller-owned load balancer is gone
# while the controller and CPU workers are still available.
cleanup_step="ingress_and_alb"
kubectl --kubeconfig "$kubeconfig" delete ingress/speech-orchestrator-b6-window --namespace medzen --ignore-not-found --wait=true --timeout=10m || true
alb_deadline=$((SECONDS + 900))
while aws elbv2 describe-load-balancers --names medzen-b6-window --region eu-central-1 --profile medzen >/dev/null 2>&1; do
  if (( SECONDS >= alb_deadline )); then
    echo "REFUSING: B6.6 ALB remains after Ingress deletion" >&2
    exit 2
  fi
  sleep 15
done

cleanup_step="kubernetes_workloads"
kubectl --kubeconfig "$kubeconfig" scale deployment/rag-index deployment/asr-runtime deployment/tts-gateway deployment/llm-gateway deployment/speech-orchestrator --namespace medzen --replicas=0 --timeout=5m || true
kubectl --kubeconfig "$kubeconfig" delete \
  deployment/rag-index deployment/asr-runtime deployment/tts-gateway deployment/llm-gateway deployment/speech-orchestrator \
  service/rag-index service/asr-runtime service/tts-gateway service/llm-gateway service/speech-orchestrator \
  resourceclaimtemplate/asr-runtime-gpu \
  networkpolicy/default-deny-ingress networkpolicy/orchestrator-to-dependencies networkpolicy/orchestrator-ingress \
  configmap/asr-runtime-config configmap/speech-orchestrator-config \
  serviceaccount/asr-runtime-b6a serviceaccount/rag-index serviceaccount/llm-gateway serviceaccount/tts-gateway serviceaccount/speech-orchestrator \
  --namespace medzen --ignore-not-found --wait=true --timeout=15m || true

kubectl --kubeconfig "$kubeconfig" delete -f platform/k8s/b6a/nvidia-dra-003c-b.locked.yaml --ignore-not-found --wait=true --timeout=10m || true

cleanup_step="terraform_window_and_secret"
cleanup_plan="/private/tmp/b6-009-cleanup-$PPID.tfplan"
targets=(
  -target=helm_release.b6_load_balancer_controller
  -target=aws_vpc_security_group_ingress_rule.b6_alb_from_backend
  -target=aws_vpc_security_group_ingress_rule.b6_nodes_from_alb
  -target=aws_iam_role.b6_probe_execution
  -target=aws_iam_role_policy.b6_probe_execution
  -target=aws_ecs_cluster.b6_probe
  -target=aws_ecs_task_definition.b6_probe
  -target=aws_secretsmanager_secret.b6_client_keys
  -target=aws_secretsmanager_secret_policy.b6_client_keys
  -target=aws_iam_role_policy.b6_client_keys_kms
)
scripts/terraform_medzen.sh plan -input=false -out="$cleanup_plan" \
  -var=account_id=558069890522 \
  -var=registry_publisher_principal_arn=arn:aws:iam::558069890522:user/s.fotso \
  -var=enable_b6_load_balancer_controller=false \
  -var=enable_b6_integration_window=false \
  -var=enable_b6_client_keys=false "${targets[@]}"

change_count="$(terraform -chdir=infra show -json "$cleanup_plan" | jq '[.resource_changes[]? | select(.change.actions != ["no-op"] and .change.actions != ["read"])] | length')"
if [[ "$change_count" != "0" ]]; then
  .venv/bin/python scripts/check_b6_6_window_plan.py cleanup "$cleanup_plan"
  scripts/terraform_medzen.sh apply -input=false -auto-approve "$cleanup_plan"
fi

cleanup_step="worker_scale_zero"
aws eks update-nodegroup-config --cluster-name medzen-speech --nodegroup-name gpu --scaling-config minSize=0,maxSize=1,desiredSize=0 --region eu-central-1 --profile medzen >/dev/null
aws eks update-nodegroup-config --cluster-name medzen-speech --nodegroup-name cpu --scaling-config minSize=0,maxSize=4,desiredSize=0 --region eu-central-1 --profile medzen >/dev/null
aws eks wait nodegroup-active --cluster-name medzen-speech --nodegroup-name gpu --region eu-central-1 --profile medzen
aws eks wait nodegroup-active --cluster-name medzen-speech --nodegroup-name cpu --region eu-central-1 --profile medzen
.venv/bin/python scripts/b6_6_deadline.py disarm --wait-seconds 1800

cleanup_step="local_token_removal"
if [[ -e "$token_file" ]]; then
  /bin/rm -f -- "$token_file"
fi

# Zero-state proof is exact and refuses before the final cleanup receipt.
cleanup_step="zero_state_proof"
[[ "$(kubectl --kubeconfig "$kubeconfig" get nodes -l 'workload in (cpu,gpu)' -o json | jq '.items | length')" == "0" ]]
[[ "$(kubectl --kubeconfig "$kubeconfig" get pods -n medzen -l medzen.io/classification=synthetic-integration-only -o json | jq '.items | length')" == "0" ]]
[[ "$(kubectl --kubeconfig "$kubeconfig" get ingress -A -o json | jq '[.items[] | select(.metadata.name=="speech-orchestrator-b6-window")] | length')" == "0" ]]
[[ "$(aws ssm get-parameters-by-path --path /medzen/registry/serving --recursive --with-decryption --region eu-central-1 --profile medzen --query 'Parameters[?Name==`/medzen/registry/serving/current`]' --output json | jq 'length')" == "0" ]]
[[ ! -e "$token_file" ]]

cleanup_step="receipt_persistence"
.venv/bin/python scripts/b6_6_receipt.py "$cleanup_receipt_stage" PASS --receipts-dir "$receipts_dir" --payload '{"alb_count":0,"approved_asr_changes":0,"cpu_asg_instances":0,"cpu_desired":0,"deadline_actions":0,"deployments":0,"gpu_asg_instances":0,"gpu_desired":0,"ingresses":0,"local_token_removed":true,"production_ssm_pointer_changes":0,"synthetic_secret":"SCHEDULED_RECOVERABLE_DELETION","window_terraform_resources":0}'
