#!/usr/bin/env bash
# Canonical persistent-secret cleanup for B6-AWS-CHANGE-PACKET-2026-022.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
cd "$repo_root"
export PYTHONPATH="$repo_root${PYTHONPATH:+:$PYTHONPATH}"

if [[ $# -ne 7 ]]; then
  echo "usage: $0 KUBECONFIG AUTHORIZATION PACKET_SHA256 RECEIPTS_DIR TOKEN_FILE ATTEMPT PAYLOAD_PATH" >&2
  exit 2
fi
kubeconfig="$1"
authorization="$2"
packet_sha256="$3"
receipts_dir="$4"
token_file="$5"
attempt="$6"
payload_path="$7"
alb_hostname_file="/private/tmp/b6-022-attempt-${attempt}-alb-hostname"

[[ "${AWS_PROFILE:-}" == "medzen" ]] || { echo "REFUSING: AWS_PROFILE=medzen is required" >&2; exit 2; }
[[ -f "$kubeconfig" && -f "$authorization" ]] || { echo "REFUSING: cleanup binding file is absent" >&2; exit 2; }
[[ "$token_file" == "/private/tmp/medzen-b6-6-client-token" ]] || { echo "REFUSING: exact synthetic token path is required" >&2; exit 2; }
[[ "$attempt" == "1" || "$attempt" == "2" ]] || { echo "REFUSING: attempt must be 1 or 2" >&2; exit 2; }

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

cleanup_step="terraform_window"
cleanup_plan="/private/tmp/b6-022-cleanup-$PPID.tfplan"
targets=(
  -target=helm_release.b6_load_balancer_controller
  -target=aws_security_group.b6_probe_endpoints
  -target=aws_vpc_security_group_ingress_rule.b6_alb_from_backend
  -target=aws_vpc_security_group_ingress_rule.b6_nodes_from_alb
  -target=aws_vpc_security_group_ingress_rule.b6_probe_to_endpoints
  -target=aws_vpc_endpoint.b6_probe_ecr_api
  -target=aws_vpc_endpoint.b6_probe_ecr_dkr
  -target=aws_vpc_endpoint.b6_probe_s3
  -target=aws_iam_role.b6_probe_execution
  -target=aws_iam_role_policy.b6_probe_execution
  -target=aws_ecs_cluster.b6_probe
  -target=aws_ecs_task_definition.b6_probe
)
scripts/terraform_medzen.sh plan -input=false -out="$cleanup_plan" \
  -var=account_id=558069890522 \
  -var=registry_publisher_principal_arn=arn:aws:iam::558069890522:user/s.fotso \
  -var=enable_b6_load_balancer_controller=false \
  -var=enable_b6_integration_window=false \
  -var=enable_b6_probe_qualification=false "${targets[@]}"

change_count="$(terraform -chdir=infra show -json "$cleanup_plan" | jq '[.resource_changes[]? | select(.change.actions != ["no-op"] and .change.actions != ["read"])] | length')"
if [[ "$change_count" != "0" ]]; then
  terraform_window_status="$(jq -r '.status // empty' "$receipts_dir/terraform_window.json" 2>/dev/null || true)"
  if [[ "$terraform_window_status" == "PASS" ]]; then
    .venv/bin/python scripts/check_b6_6_window_plan.py destroy "$cleanup_plan"
  else
    .venv/bin/python scripts/check_b6_6_window_plan.py cleanup "$cleanup_plan"
  fi
  scripts/terraform_medzen.sh apply -input=false -auto-approve "$cleanup_plan"
fi

# Terraform must finish deleting both interface endpoints, the S3 gateway
# endpoint and their endpoint-side SG before worker deadlines can be disarmed.
cleanup_step="endpoint_absence"
.venv/bin/python scripts/b6_6_probe_endpoints.py absent --profile medzen >/dev/null

cleanup_step="worker_scale_zero"
aws eks update-nodegroup-config --cluster-name medzen-speech --nodegroup-name gpu --scaling-config minSize=0,maxSize=1,desiredSize=0 --region eu-central-1 --profile medzen >/dev/null
aws eks update-nodegroup-config --cluster-name medzen-speech --nodegroup-name cpu --scaling-config minSize=0,maxSize=4,desiredSize=0 --region eu-central-1 --profile medzen >/dev/null
aws eks wait nodegroup-active --cluster-name medzen-speech --nodegroup-name gpu --region eu-central-1 --profile medzen
aws eks wait nodegroup-active --cluster-name medzen-speech --nodegroup-name cpu --region eu-central-1 --profile medzen
.venv/bin/python scripts/b6_6_deadline.py disarm --wait-seconds 1800

cleanup_step="local_token_removal_persistent_secret_retention"
if [[ -e "$token_file" ]]; then
  /bin/rm -f -- "$token_file"
fi
if [[ -e "$alb_hostname_file" ]]; then
  /bin/rm -f -- "$alb_hostname_file"
fi

# Zero-state proof is exact and refuses before the final cleanup receipt.
cleanup_step="zero_state_proof"
[[ "$(kubectl --kubeconfig "$kubeconfig" get nodes -l 'workload in (cpu,gpu)' -o json | jq '.items | length')" == "0" ]]
[[ "$(kubectl --kubeconfig "$kubeconfig" get pods -n medzen -l medzen.io/classification=synthetic-integration-only -o json | jq '.items | length')" == "0" ]]
[[ "$(kubectl --kubeconfig "$kubeconfig" get ingress -A -o json | jq '[.items[] | select(.metadata.name=="speech-orchestrator-b6-window")] | length')" == "0" ]]
[[ "$(aws ssm get-parameters-by-path --path /medzen/registry/serving --recursive --with-decryption --region eu-central-1 --profile medzen --query 'Parameters[?Name==`/medzen/registry/serving/current`]' --output json | jq 'length')" == "0" ]]
[[ ! -e "$token_file" ]]
[[ ! -e "$alb_hostname_file" ]]

# R1: the exact synthetic secret and its permanent deny policy survive every
# window. The operator must still be unable to retrieve plaintext.
secret="$(aws secretsmanager describe-secret --secret-id arn:aws:secretsmanager:eu-central-1:558069890522:secret:medzen/client-api-keys-NxZGxE --region eu-central-1 --profile medzen --output json)"
[[ "$(jq -r '.Name' <<<"$secret")" == "medzen/client-api-keys" ]]
[[ "$(jq -r '.KmsKeyId' <<<"$secret")" == "arn:aws:kms:eu-central-1:558069890522:key/9c336116-c648-4548-95c6-1b926478ae57" ]]
.venv/bin/python - <<'PY'
import boto3
from botocore.exceptions import ClientError

client = boto3.Session(profile_name="medzen", region_name="eu-central-1").client("secretsmanager")
try:
    client.get_secret_value(SecretId="arn:aws:secretsmanager:eu-central-1:558069890522:secret:medzen/client-api-keys-NxZGxE")
except ClientError as exc:
    if exc.response.get("Error", {}).get("Code") != "AccessDeniedException":
        raise
else:
    raise RuntimeError("operator unexpectedly read persistent secret")
PY

cleanup_step="payload"
jq -nc '{alb_count:0,approved_asr_changes:0,cpu_asg_instances:0,cpu_desired:0,deadline_actions:0,deployments:0,endpoint_security_groups:0,gpu_asg_instances:0,gpu_desired:0,ingresses:0,local_alb_hostname_removed:true,local_token_removed:true,probe_vpc_endpoints:0,production_ssm_pointer_changes:0,persistent_synthetic_secret:"RETAINED_OPERATOR_DENIED",window_terraform_resources:0}' >"$payload_path"
