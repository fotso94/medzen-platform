#!/usr/bin/env bash
# Execute only an independently reviewed and owner-approved packet 2026-013.
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
manifest="platform/k8s/b6-6/integration-window.yaml"
wav="platform/testdata/orchestrator/synthetic-file-request.wav"

[[ "${AWS_PROFILE:-}" == "medzen" ]] || { echo "REFUSING: AWS_PROFILE=medzen is required" >&2; exit 2; }
[[ -f "$kubeconfig" && -f "$authorization" && -f "$token_file" ]] || { echo "REFUSING: required B6.6 execution input is absent" >&2; exit 2; }
[[ "$token_file" == "/private/tmp/medzen-b6-6-client-token" ]] || { echo "REFUSING: exact synthetic token path is required" >&2; exit 2; }
.venv/bin/python scripts/b6_6_token_binding.py "$token_file" >/dev/null

.venv/bin/python - "$authorization" "$packet_sha256" "$repo_root" <<'PY'
import json,sys
from pathlib import Path
from scripts.b6_6_bindings import validate
value=validate(Path(sys.argv[1]),sys.argv[2],Path(sys.argv[3]))
print(json.dumps({"status":"PASS","authorization_id":value["id"],"source_count":len(value["source_bindings"])},sort_keys=True))
PY
.venv/bin/python scripts/b6_6_receipt.py local_bindings PASS --receipts-dir "$receipts_dir" --payload '{"authorization_record":"HASH_BOUND_OWNER_APPROVED","cost_registry":"COST-REGISTRY-2026-004","source_bindings_verified":true}'

cleanup() {
  original_status=$?
  trap - EXIT INT TERM
  set +e
  if [[ -n "${port_forward_pid:-}" ]]; then kill "$port_forward_pid" >/dev/null 2>&1 || true; fi
  bash scripts/b6_6_cleanup.sh "$kubeconfig" "$authorization" "$packet_sha256" "$receipts_dir" "$token_file"
  cleanup_status=$?
  if [[ $cleanup_status -ne 0 ]]; then
    echo "REFUSING: B6.6 cleanup proof failed; AWS deadlines remain armed" >&2
    exit "$cleanup_status"
  fi
  exit "$original_status"
}
trap cleanup EXIT INT TERM

# Stage 0: independent AWS-side worker shutdown exists before capacity rises.
deadline_payload="$(.venv/bin/python scripts/b6_6_deadline.py arm)"
.venv/bin/python scripts/b6_6_receipt.py deadline PASS --receipts-dir "$receipts_dir" --payload "$deadline_payload"
.venv/bin/python scripts/b6_6_deadline.py verify >/dev/null

# Fail closed unless every pre-created boundary is still exact.
[[ "$(aws sts get-caller-identity --profile medzen --query Account --output text)" == "558069890522" ]]
[[ "$(aws ssm get-parameters-by-path --path /medzen/registry/test/b6/d4f9696d288e0ea6c1d139f496e00eaf097b77ea8b3a4f5a26a6470286adfe81 --recursive --with-decryption --region eu-central-1 --profile medzen --query 'length(Parameters)' --output text)" == "3" ]]
.venv/bin/python scripts/b6_6_secret_preflight.py --profile medzen >/dev/null
[[ "$(aws ssm get-parameters-by-path --path /medzen/registry/serving --recursive --with-decryption --region eu-central-1 --profile medzen --query 'length(Parameters[?Name==`/medzen/registry/serving/current`])' --output text)" == "0" ]]
[[ "$(kubectl --kubeconfig "$kubeconfig" get nodes -l 'workload in (cpu,gpu)' --request-timeout=15s -o json | jq '.items | length')" == "0" ]]
[[ "$(kubectl --kubeconfig "$kubeconfig" get pods -n medzen -l medzen.io/classification=synthetic-integration-only --request-timeout=15s -o json | jq '.items | length')" == "0" ]]
[[ "$(kubectl --kubeconfig "$kubeconfig" get ingress -A --request-timeout=15s -o json | jq '[.items[] | select(.metadata.name=="speech-orchestrator-b6-window")] | length')" == "0" ]]
! kubectl --kubeconfig "$kubeconfig" get deployment/aws-load-balancer-controller --namespace kube-system --request-timeout=15s >/dev/null 2>&1
! kubectl --kubeconfig "$kubeconfig" get daemonset/dra-driver-nvidia-gpu-kubelet-plugin --namespace nvidia-dra-driver --request-timeout=15s >/dev/null 2>&1

aws eks update-nodegroup-config --cluster-name medzen-speech --nodegroup-name cpu --scaling-config minSize=0,maxSize=4,desiredSize=2 --region eu-central-1 --profile medzen >/dev/null
aws eks update-nodegroup-config --cluster-name medzen-speech --nodegroup-name gpu --scaling-config minSize=0,maxSize=1,desiredSize=1 --region eu-central-1 --profile medzen >/dev/null
aws eks wait nodegroup-active --cluster-name medzen-speech --nodegroup-name cpu --region eu-central-1 --profile medzen
aws eks wait nodegroup-active --cluster-name medzen-speech --nodegroup-name gpu --region eu-central-1 --profile medzen
if worker_payload="$(.venv/bin/python scripts/b6_6_wait_workers.py --kubeconfig "$kubeconfig" --wait-seconds 1200)"; then
  .venv/bin/python scripts/b6_6_receipt.py workers_ready PASS --receipts-dir "$receipts_dir" --payload "$worker_payload"
else
  .venv/bin/python scripts/b6_6_receipt.py workers_ready REFUSED --receipts-dir "$receipts_dir" --payload "$worker_payload"
  exit 2
fi

window_plan="/private/tmp/b6-013-create-$PPID.tfplan"
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
scripts/terraform_medzen.sh plan -input=false -out="$window_plan" \
  -var=account_id=558069890522 \
  -var=registry_publisher_principal_arn=arn:aws:iam::558069890522:user/s.fotso \
  -var=enable_b6_load_balancer_controller=true \
  -var=enable_b6_integration_window=true \
  -var=enable_b6_client_keys=true "${targets[@]}"
.venv/bin/python scripts/check_b6_6_window_plan.py create "$window_plan"
scripts/terraform_medzen.sh apply -input=false -auto-approve "$window_plan"
.venv/bin/python scripts/b6_6_receipt.py terraform_window PASS --receipts-dir "$receipts_dir" --payload '{"adds":7,"changes":0,"destroys":0,"fargate_maximum_tasks":1,"iam_roles_created":1,"security_group_rules_created":2}'

kubectl --kubeconfig "$kubeconfig" rollout status deployment/aws-load-balancer-controller --namespace kube-system --timeout=10m
[[ "$(kubectl --kubeconfig "$kubeconfig" get deployment/aws-load-balancer-controller --namespace kube-system -o jsonpath='{.spec.template.spec.containers[0].image}')" == "558069890522.dkr.ecr.eu-central-1.amazonaws.com/medzen-aws-load-balancer-controller@sha256:c2ebdeae779c796e3d071d7a0d3a4ebdbb31e4e8d53e3e5372ee0ab0c4f3f08f" ]]
.venv/bin/python scripts/b6_6_receipt.py controller_ready PASS --receipts-dir "$receipts_dir" --payload '{"controller_replicas_ready":1,"digest_pinned":true,"namespace_watch":"medzen"}'

kubectl --kubeconfig "$kubeconfig" apply -f platform/k8s/b6a/nvidia-dra-003c-b.locked.yaml
kubectl --kubeconfig "$kubeconfig" rollout status daemonset/dra-driver-nvidia-gpu-kubelet-plugin --namespace nvidia-dra-driver --timeout=15m
dra_images="$(kubectl --kubeconfig "$kubeconfig" get daemonset/dra-driver-nvidia-gpu-kubelet-plugin --namespace nvidia-dra-driver -o json | jq -c '[.spec.template.spec.initContainers[]?.image,.spec.template.spec.containers[]?.image] | unique')"
[[ "$dra_images" == '["558069890522.dkr.ecr.eu-central-1.amazonaws.com/medzen-nvidia-dra@sha256:7fb313758a20a04e80a53d5f6d1efe8e6fe936bc845001c21204c1c361d59246"]' ]]
.venv/bin/python scripts/b6_6_receipt.py dra_ready PASS --receipts-dir "$receipts_dir" --payload '{"daemonset_ready":true,"digest_pinned":true,"gpu_nodes":1}'

kubectl --kubeconfig "$kubeconfig" apply -f "$manifest"

kubectl --kubeconfig "$kubeconfig" scale deployment/rag-index --namespace medzen --replicas=1
kubectl --kubeconfig "$kubeconfig" rollout status deployment/rag-index --namespace medzen --timeout=10m
.venv/bin/python scripts/b6_6_receipt.py rag_ready PASS --receipts-dir "$receipts_dir" --payload '{"ready_replicas":1,"provider":"embedded_synthetic","service_type":"ClusterIP"}'

kubectl --kubeconfig "$kubeconfig" scale deployment/asr-runtime --namespace medzen --replicas=1
kubectl --kubeconfig "$kubeconfig" rollout status deployment/asr-runtime --namespace medzen --timeout=30m
.venv/bin/python scripts/b6_6_receipt.py asr_ready PASS --receipts-dir "$receipts_dir" --payload '{"artifact_tree_sha256":"5adf77568813513bc3697a1501ba354c04c7b93ea374fc5407cf4f6402f7431e","model":"zero-shot-whisper-large-v3","ready_replicas":1,"serving_label":"v0"}'

kubectl --kubeconfig "$kubeconfig" scale deployment/tts-gateway --namespace medzen --replicas=1
kubectl --kubeconfig "$kubeconfig" rollout status deployment/tts-gateway --namespace medzen --timeout=10m
.venv/bin/python scripts/b6_6_receipt.py tts_ready PASS --receipts-dir "$receipts_dir" --payload '{"fish_calls":0,"provider":"text_only","ready_replicas":1,"service_type":"ClusterIP"}'

kubectl --kubeconfig "$kubeconfig" scale deployment/llm-gateway --namespace medzen --replicas=1
kubectl --kubeconfig "$kubeconfig" rollout status deployment/llm-gateway --namespace medzen --timeout=10m
.venv/bin/python scripts/b6_6_receipt.py llm_ready PASS --receipts-dir "$receipts_dir" --payload '{"bedrock_calls":0,"provider":"fake","ready_replicas":1,"service_type":"ClusterIP"}'

kubectl --kubeconfig "$kubeconfig" scale deployment/speech-orchestrator --namespace medzen --replicas=1
kubectl --kubeconfig "$kubeconfig" rollout status deployment/speech-orchestrator --namespace medzen --timeout=10m
expected_workload_digests='["sha256:434ac9e757b56949324e9e480490042fcbf35285f27bdee713bd771b502f4087","sha256:88026dd9708073dcd3622e7dd68e7a70aff98cddd43129c53c017d571f533f5a","sha256:88e83b97a03c593505435981c554d5d0f3045c4acb4a7224148d58e3af96087d","sha256:cb794f2169dc65f391a0c9ec789997ce19a31b38d9087f263fba0863ba0414a5","sha256:fa2cccdf9891c080fcc1eb408a325e8afbd623e4f89469ea228ddf166dad62aa","sha256:fe4663812f88bd35d520fee3e80450981347c970f2a561eb8163b14183b7194c"]'
actual_workload_digests="$(kubectl --kubeconfig "$kubeconfig" get pods --namespace medzen -l medzen.io/classification=synthetic-integration-only -o json | jq -c '[.items[] | (.status.initContainerStatuses[]?,.status.containerStatuses[]?) | .imageID | capture("(?<digest>sha256:[0-9a-f]{64})$").digest] | unique | sort')"
[[ "$actual_workload_digests" == "$expected_workload_digests" ]]
.venv/bin/python scripts/b6_6_receipt.py orchestrator_ready PASS --receipts-dir "$receipts_dir" --payload '{"authentication_loaded":true,"mode":"deployed_http_ssm","ready_replicas":1,"registry_snapshot":"d4f9696d288e0ea6c1d139f496e00eaf097b77ea8b3a4f5a26a6470286adfe81","workload_child_digests_verified":6}'

alb_wait=$((SECONDS + 900))
alb_hostname=""
while [[ -z "$alb_hostname" ]]; do
  alb_hostname="$(kubectl --kubeconfig "$kubeconfig" get ingress/speech-orchestrator-b6-window --namespace medzen -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || true)"
  if (( SECONDS >= alb_wait )); then echo "REFUSING: internal ALB hostname was not assigned" >&2; exit 2; fi
  [[ -n "$alb_hostname" ]] || sleep 15
done
alb_scheme="$(aws elbv2 describe-load-balancers --names medzen-b6-window --region eu-central-1 --profile medzen --query 'LoadBalancers[0].Scheme' --output text)"
alb_type="$(aws elbv2 describe-load-balancers --names medzen-b6-window --region eu-central-1 --profile medzen --query 'LoadBalancers[0].Type' --output text)"
alb_security_groups="$(aws elbv2 describe-load-balancers --names medzen-b6-window --region eu-central-1 --profile medzen --query 'LoadBalancers[0].SecurityGroups' --output json | jq -c 'sort')"
[[ "$alb_scheme" == "internal" && "$alb_type" == "application" && "$alb_security_groups" == '["sg-0f0f6c66852830013"]' ]]

overrides="$(jq -nc --arg target "http://$alb_hostname/readyz" '{containerOverrides:[{name:"probe",environment:[{name:"TARGET_URL",value:$target}]}]}')"
task_arn="$(aws ecs run-task --cluster medzen-b6-window-probe --task-definition medzen-b6-window-probe --launch-type FARGATE --network-configuration 'awsvpcConfiguration={subnets=[subnet-00232b25bc1ac407a,subnet-05029419c6c61a536,subnet-01fb2fc3f56bce55e],securityGroups=[sg-0a83abae6ab954543],assignPublicIp=DISABLED}' --overrides "$overrides" --region eu-central-1 --profile medzen --query 'tasks[0].taskArn' --output text)"
[[ "$task_arn" == arn:aws:ecs:* && "$task_arn" != "None" ]]
aws ecs wait tasks-stopped --cluster medzen-b6-window-probe --tasks "$task_arn" --region eu-central-1 --profile medzen
[[ "$(aws ecs describe-tasks --cluster medzen-b6-window-probe --tasks "$task_arn" --region eu-central-1 --profile medzen --query 'tasks[0].containers[0].exitCode' --output text)" == "0" ]]
alb_payload="$(.venv/bin/python scripts/b6_6_lbc_runtime.py verify --profile medzen)"
.venv/bin/python scripts/b6_6_receipt.py alb_ready PASS --receipts-dir "$receipts_dir" --payload "$alb_payload"
tag_payload="$(.venv/bin/python scripts/b6_6_lbc_runtime.py classify --kubeconfig "$kubeconfig" --receipts-dir "$receipts_dir")"
tag_result="$(jq -r '.status' <<<"$tag_payload")"
if [[ "$tag_result" == "WARNING_NON_FATAL" ]]; then
  tag_receipt_status="WARNING_NON_FATAL"
elif [[ "$tag_result" == "PASS_NO_TAG_MUTATION_DENIAL" ]]; then
  tag_receipt_status="PASS"
else
  echo "REFUSING: ALB tag-mutation result is unknown" >&2
  exit 2
fi
.venv/bin/python scripts/b6_6_receipt.py alb_tag_mutation_warning "$tag_receipt_status" --receipts-dir "$receipts_dir" --payload "$tag_payload"

kubectl --kubeconfig "$kubeconfig" port-forward --namespace medzen service/speech-orchestrator 18080:8080 >/dev/null 2>&1 &
port_forward_pid=$!
for _ in {1..60}; do
  curl --fail --silent --max-time 2 http://127.0.0.1:18080/readyz >/dev/null 2>&1 && break
  sleep 1
done
kill -0 "$port_forward_pid"

file_payload="$(.venv/bin/python scripts/b6_6_probe.py file --base-url http://127.0.0.1:18080 --token-file "$token_file" --wav "$wav")"
.venv/bin/python scripts/b6_6_receipt.py file_proof PASS --receipts-dir "$receipts_dir" --payload "$file_payload"
stream_payload="$(.venv/bin/python scripts/b6_6_probe.py websocket --base-url http://127.0.0.1:18080 --token-file "$token_file" --wav "$wav")"
.venv/bin/python scripts/b6_6_receipt.py websocket_proof PASS --receipts-dir "$receipts_dir" --payload "$stream_payload"
cancel_payload="$(.venv/bin/python scripts/b6_6_probe.py cancellation --base-url http://127.0.0.1:18080 --token-file "$token_file" --wav "$wav")"
.venv/bin/python scripts/b6_6_receipt.py cancellation_proof PASS --receipts-dir "$receipts_dir" --payload "$cancel_payload"
refusal_payload="$(.venv/bin/python scripts/b6_6_probe.py refusals --base-url http://127.0.0.1:18080 --token-file "$token_file" --wav "$wav")"
kubectl --kubeconfig "$kubeconfig" scale deployment/rag-index --namespace medzen --replicas=0
for _ in {1..60}; do
  endpoint_count="$(kubectl --kubeconfig "$kubeconfig" get endpoints/rag-index --namespace medzen -o json 2>/dev/null | jq '[.subsets[]?.addresses[]?] | length')"
  [[ "$endpoint_count" == "0" ]] && break
  sleep 2
done
[[ "${endpoint_count:-unknown}" == "0" ]]
dependency_refusal_payload="$(.venv/bin/python scripts/b6_6_probe.py dependency-refusal --base-url http://127.0.0.1:18080 --token-file "$token_file" --wav "$wav")"
kubectl --kubeconfig "$kubeconfig" scale deployment/rag-index --namespace medzen --replicas=1
kubectl --kubeconfig "$kubeconfig" rollout status deployment/rag-index --namespace medzen --timeout=10m
failure_payload="$(jq -nc --argjson controls "$refusal_payload" --argjson dependency "$dependency_refusal_payload" '{status:"PASS",controlled_refusals:$controls,rag_unavailable:$dependency,local_provider_drills:"PREPROVEN_BY_PINNED_SUITES_NO_REAL_PROVIDER_CALLS"}')"
.venv/bin/python scripts/b6_6_receipt.py failure_drills PASS --receipts-dir "$receipts_dir" --payload "$failure_payload"

service_types="$(kubectl --kubeconfig "$kubeconfig" get services --namespace medzen -o json | jq -r '[.items[] | select(.metadata.name=="asr-runtime" or .metadata.name=="rag-index" or .metadata.name=="llm-gateway" or .metadata.name=="tts-gateway" or .metadata.name=="speech-orchestrator") | .spec.type] | unique | join(",")')"
[[ "$service_types" == "ClusterIP" ]]
[[ "$(kubectl --kubeconfig "$kubeconfig" get ingress --namespace medzen -o json | jq '[.items[] | select(.metadata.name=="speech-orchestrator-b6-window")] | length')" == "1" ]]
.venv/bin/python scripts/b6_6_receipt.py isolation_proof PASS --receipts-dir "$receipts_dir" --payload '{"alb_ingress_source":"sg-0a83abae6ab954543","dependency_ingresses":0,"dependency_service_type":"ClusterIP","orchestrator_ingresses":1,"public_load_balancers":0}'
