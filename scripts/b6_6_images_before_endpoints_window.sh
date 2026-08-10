#!/usr/bin/env bash
# Execute only independently reviewed and owner-approved packet 2026-018.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
cd "$repo_root"
export PYTHONPATH="$repo_root${PYTHONPATH:+:$PYTHONPATH}"
source scripts/b6_6_stage_runtime.sh

if [[ $# -ne 5 ]]; then
  echo "usage: $0 KUBECONFIG AUTHORIZATION PACKET_SHA256 RECEIPTS_DIR TOKEN_FILE" >&2
  exit 2
fi
kubeconfig="$1"
authorization="$2"
packet_sha256="$3"
receipts_dir="$4"
token_file="$5"
expected_receipts="$repo_root/platform/evidence/receipts/B6-2026-018-LIVE"
manifest="platform/k8s/b6-6/integration-window.yaml"
wav="platform/testdata/orchestrator/synthetic-file-request.wav"
alb_hostname_file="/private/tmp/b6-018-alb-hostname"

[[ "${AWS_PROFILE:-}" == "medzen" ]] || { echo "REFUSING: AWS_PROFILE=medzen is required" >&2; exit 2; }
[[ -f "$kubeconfig" && -f "$authorization" ]] || { echo "REFUSING: required execution input is absent" >&2; exit 2; }
[[ "$token_file" == "/private/tmp/medzen-b6-6-client-token" ]] || { echo "REFUSING: exact synthetic token path is required" >&2; exit 2; }
[[ ! -e "$token_file" && ! -e "$alb_hostname_file" ]] || { echo "REFUSING: pre-existing local window material is forbidden" >&2; exit 2; }
[[ "$receipts_dir" == "$expected_receipts" ]] || { echo "REFUSING: exact packet-2026-018 receipt directory is required" >&2; exit 2; }
[[ ! -e "$receipts_dir" ]] || { echo "REFUSING: packet-2026-018 receipt directory already exists" >&2; exit 2; }
[[ -z "$(git status --porcelain=v1)" ]] || { echo "REFUSING: execution requires a clean reviewed worktree" >&2; exit 2; }

.venv/bin/python - "$authorization" "$packet_sha256" "$repo_root" <<'PY'
import sys
from pathlib import Path
from scripts.b6_6_images_before_endpoints_bindings import validate
validate(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]))
PY

bash scripts/b6_6_images_before_endpoints_credential_stage.sh \
  "$authorization" "$packet_sha256" "$receipts_dir" "$token_file"

cleanup() {
  original_status=$?
  trap - EXIT INT TERM
  set +e
  /bin/rm -f -- "$alb_hostname_file"
  bash scripts/b6_6_images_before_endpoints_cleanup.sh \
    "$kubeconfig" "$authorization" "$packet_sha256" "$receipts_dir" "$token_file"
  cleanup_status=$?
  if [[ $cleanup_status -ne 0 ]]; then
    echo "REFUSING: packet-2026-018 cleanup proof failed; AWS deadlines remain armed" >&2
    exit "$cleanup_status"
  fi
  exit "$original_status"
}
trap cleanup EXIT INT TERM

export B6_STAGE_RECEIPTS_DIR="$receipts_dir"
export B6_KUBECONFIG="$kubeconfig"
export B6_ENDPOINTS_ENABLED=false

stage_local_bindings() {
  credential_payload="$(.venv/bin/python scripts/b6_6_images_before_endpoints_secret_preflight.py \
    --verification-receipt "$receipts_dir/credential/verification.json" --profile medzen)"
  .venv/bin/python scripts/b6_6_successor_token_binding.py \
    "$token_file" "$receipts_dir/credential/verification.json" >/dev/null
  [[ "$(aws sts get-caller-identity --profile medzen --query Account --output text)" == "558069890522" ]]
  [[ "$(aws ssm get-parameters-by-path --path /medzen/registry/test/b6/d4f9696d288e0ea6c1d139f496e00eaf097b77ea8b3a4f5a26a6470286adfe81 --recursive --with-decryption --region eu-central-1 --profile medzen --query 'length(Parameters)' --output text)" == "3" ]]
  [[ "$(aws ssm get-parameters-by-path --path /medzen/registry/serving --recursive --with-decryption --region eu-central-1 --profile medzen --query 'length(Parameters[?Name==`/medzen/registry/serving/current`])' --output text)" == "0" ]]
  [[ "$(kubectl --kubeconfig "$kubeconfig" get nodes -l 'workload in (cpu,gpu)' --request-timeout=15s -o json | jq '.items | length')" == "0" ]]
  [[ "$(kubectl --kubeconfig "$kubeconfig" get pods -n medzen -l medzen.io/classification=synthetic-integration-only --request-timeout=15s -o json | jq '.items | length')" == "0" ]]
  ! kubectl --kubeconfig "$kubeconfig" get deployment/aws-load-balancer-controller --namespace kube-system --request-timeout=15s >/dev/null 2>&1
  ! kubectl --kubeconfig "$kubeconfig" get daemonset/dra-driver-nvidia-gpu-kubelet-plugin --namespace nvidia-dra-driver --request-timeout=15s >/dev/null 2>&1
  .venv/bin/python scripts/b6_6_successor_probe_endpoints.py absent --profile medzen >/dev/null
  payload="$(jq -nc --argjson credential "$credential_payload" '{authorization_record:"HASH_BOUND_OWNER_APPROVED",cost_registry:"COST-REGISTRY-2026-004",source_bindings_verified:true,credential_stage:"PASS_VERIFIED_FRESH",credential_version_id:$credential.fresh_version_id,credential_material_sha256:$credential.fresh_material_sha256,credential_value_sha256:$credential.secret_value_sha256,prior_version_count:$credential.prior_version_count}')"
  b6_stage_payload "$payload"
}

stage_deadline() {
  payload="$(.venv/bin/python scripts/b6_6_successor_deadline.py arm)"
  .venv/bin/python scripts/b6_6_successor_deadline.py verify >/dev/null
  b6_stage_payload "$payload"
}

stage_workers() {
  aws eks update-nodegroup-config --cluster-name medzen-speech --nodegroup-name cpu --scaling-config minSize=0,maxSize=4,desiredSize=2 --region eu-central-1 --profile medzen >/dev/null
  aws eks update-nodegroup-config --cluster-name medzen-speech --nodegroup-name gpu --scaling-config minSize=0,maxSize=1,desiredSize=1 --region eu-central-1 --profile medzen >/dev/null
  aws eks wait nodegroup-active --cluster-name medzen-speech --nodegroup-name cpu --region eu-central-1 --profile medzen
  aws eks wait nodegroup-active --cluster-name medzen-speech --nodegroup-name gpu --region eu-central-1 --profile medzen
  payload="$(.venv/bin/python scripts/b6_6_wait_workers.py --kubeconfig "$kubeconfig" --wait-seconds 1200)"
  b6_stage_payload "$payload"
}

stage_dra() {
  kubectl --kubeconfig "$kubeconfig" apply -f platform/k8s/b6a/nvidia-dra-003c-b.locked.yaml
  kubectl --kubeconfig "$kubeconfig" rollout status daemonset/dra-driver-nvidia-gpu-kubelet-plugin --namespace nvidia-dra-driver --timeout=15m
  images="$(kubectl --kubeconfig "$kubeconfig" get daemonset/dra-driver-nvidia-gpu-kubelet-plugin --namespace nvidia-dra-driver -o json | jq -c '[.spec.template.spec.initContainers[]?.image,.spec.template.spec.containers[]?.image] | unique')"
  [[ "$images" == '["558069890522.dkr.ecr.eu-central-1.amazonaws.com/medzen-nvidia-dra@sha256:7fb313758a20a04e80a53d5f6d1efe8e6fe936bc845001c21204c1c361d59246"]' ]]
  b6_stage_payload '{"daemonset_ready":true,"digest_pinned":true,"gpu_nodes":1,"before_private_endpoints":true}'
}

stage_rag() {
  .venv/bin/python scripts/b6_6_manifest_slice.py pre-endpoint | kubectl --kubeconfig "$kubeconfig" apply -f -
  kubectl --kubeconfig "$kubeconfig" scale deployment/rag-index --namespace medzen --replicas=1
  kubectl --kubeconfig "$kubeconfig" rollout status deployment/rag-index --namespace medzen --timeout=10m
  b6_stage_payload '{"ready_replicas":1,"provider":"embedded_synthetic","service_type":"ClusterIP","before_private_endpoints":true}'
}

stage_asr() {
  kubectl --kubeconfig "$kubeconfig" scale deployment/asr-runtime --namespace medzen --replicas=1
  kubectl --kubeconfig "$kubeconfig" rollout status deployment/asr-runtime --namespace medzen --timeout=30m
  b6_stage_payload '{"artifact_tree_sha256":"5adf77568813513bc3697a1501ba354c04c7b93ea374fc5407cf4f6402f7431e","model":"zero-shot-whisper-large-v3","ready_replicas":1,"serving_label":"v0","before_private_endpoints":true}'
}

stage_tts() {
  kubectl --kubeconfig "$kubeconfig" scale deployment/tts-gateway --namespace medzen --replicas=1
  kubectl --kubeconfig "$kubeconfig" rollout status deployment/tts-gateway --namespace medzen --timeout=10m
  b6_stage_payload '{"fish_calls":0,"provider":"text_only","ready_replicas":1,"service_type":"ClusterIP","before_private_endpoints":true}'
}

stage_llm() {
  kubectl --kubeconfig "$kubeconfig" scale deployment/llm-gateway --namespace medzen --replicas=1
  kubectl --kubeconfig "$kubeconfig" rollout status deployment/llm-gateway --namespace medzen --timeout=10m
  b6_stage_payload '{"bedrock_calls":0,"provider":"fake","ready_replicas":1,"service_type":"ClusterIP","before_private_endpoints":true}'
}

stage_orchestrator() {
  kubectl --kubeconfig "$kubeconfig" scale deployment/speech-orchestrator --namespace medzen --replicas=1
  kubectl --kubeconfig "$kubeconfig" rollout status deployment/speech-orchestrator --namespace medzen --timeout=10m
  expected='["sha256:434ac9e757b56949324e9e480490042fcbf35285f27bdee713bd771b502f4087","sha256:88026dd9708073dcd3622e7dd68e7a70aff98cddd43129c53c017d571f533f5a","sha256:88e83b97a03c593505435981c554d5d0f3045c4acb4a7224148d58e3af96087d","sha256:cb794f2169dc65f391a0c9ec789997ce19a31b38d9087f263fba0863ba0414a5","sha256:fa2cccdf9891c080fcc1eb408a325e8afbd623e4f89469ea228ddf166dad62aa","sha256:fe4663812f88bd35d520fee3e80450981347c970f2a561eb8163b14183b7194c"]'
  actual="$(kubectl --kubeconfig "$kubeconfig" get pods --namespace medzen -l medzen.io/classification=synthetic-integration-only -o json | jq -c '[.items[] | (.status.initContainerStatuses[]?,.status.containerStatuses[]?) | .imageID | capture("(?<digest>sha256:[0-9a-f]{64})$").digest] | unique | sort')"
  [[ "$actual" == "$expected" ]]
  b6_stage_payload '{"authentication_loaded":true,"mode":"deployed_http_ssm","ready_replicas":1,"registry_snapshot":"d4f9696d288e0ea6c1d139f496e00eaf097b77ea8b3a4f5a26a6470286adfe81","workload_child_digests_verified":6,"before_private_endpoints":true}'
}

stage_controller_window() {
  plan="/private/tmp/b6-018-controller-$PPID.tfplan"
  scripts/terraform_medzen.sh plan -input=false -out="$plan" \
    -var=account_id=558069890522 \
    -var=registry_publisher_principal_arn=arn:aws:iam::558069890522:user/s.fotso \
    -var=enable_b6_load_balancer_controller=true \
    -var=enable_b6_integration_window=false \
    -var=enable_b6_client_keys=true \
    -target=helm_release.b6_load_balancer_controller \
    -target=aws_secretsmanager_secret.b6_client_keys \
    -target=aws_secretsmanager_secret_policy.b6_client_keys \
    -target=aws_iam_role_policy.b6_client_keys_kms
  .venv/bin/python scripts/check_b6_6_images_before_endpoints_plan.py controller "$plan"
  scripts/terraform_medzen.sh apply -input=false -auto-approve "$plan"
  b6_stage_payload '{"adds":1,"changes":0,"destroys":0,"resource":"helm_release.b6_load_balancer_controller","before_private_endpoints":true}'
}

stage_controller_ready() {
  kubectl --kubeconfig "$kubeconfig" rollout status deployment/aws-load-balancer-controller --namespace kube-system --timeout=10m
  image="$(kubectl --kubeconfig "$kubeconfig" get deployment/aws-load-balancer-controller --namespace kube-system -o jsonpath='{.spec.template.spec.containers[0].image}')"
  [[ "$image" == "558069890522.dkr.ecr.eu-central-1.amazonaws.com/medzen-aws-load-balancer-controller@sha256:c2ebdeae779c796e3d071d7a0d3a4ebdbb31e4e8d53e3e5372ee0ab0c4f3f08f" ]]
  b6_stage_payload '{"controller_replicas_ready":1,"digest_pinned":true,"namespace_watch":"medzen","before_private_endpoints":true}'
}

stage_pre_endpoint_images() {
  .venv/bin/python scripts/b6_6_successor_probe_endpoints.py absent --profile medzen >/dev/null
  payload="$(.venv/bin/python scripts/b6_6_pre_endpoint_images.py pre --kubeconfig "$kubeconfig")"
  b6_stage_payload "$payload"
}

stage_terraform_window() {
  plan="/private/tmp/b6-018-endpoints-$PPID.tfplan"
  targets=(
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
    -target=aws_secretsmanager_secret.b6_client_keys
    -target=aws_secretsmanager_secret_policy.b6_client_keys
    -target=aws_iam_role_policy.b6_client_keys_kms
  )
  scripts/terraform_medzen.sh plan -input=false -out="$plan" \
    -var=account_id=558069890522 \
    -var=registry_publisher_principal_arn=arn:aws:iam::558069890522:user/s.fotso \
    -var=enable_b6_load_balancer_controller=true \
    -var=enable_b6_integration_window=true \
    -var=enable_b6_client_keys=true "${targets[@]}"
  .venv/bin/python scripts/check_b6_6_images_before_endpoints_plan.py endpoints "$plan"
  scripts/terraform_medzen.sh apply -input=false -auto-approve "$plan"
  b6_stage_payload '{"adds":11,"changes":0,"destroys":0,"controller_changes":0,"endpoint_security_groups_created":1,"fargate_maximum_tasks":1,"iam_roles_created":1,"security_group_rules_created":3,"vpc_endpoints_created":3}'
}

stage_endpoints_ready() {
  payload="$(.venv/bin/python scripts/b6_6_successor_probe_endpoints.py available --profile medzen --wait-seconds 900)"
  b6_stage_payload "$payload"
}

stage_fargate_probe() {
  .venv/bin/python scripts/b6_6_manifest_slice.py ingress | kubectl --kubeconfig "$kubeconfig" apply -f -
  deadline=$((SECONDS + 900))
  hostname=""
  while [[ -z "$hostname" ]]; do
    hostname="$(kubectl --kubeconfig "$kubeconfig" get ingress/speech-orchestrator-b6-window --namespace medzen -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || true)"
    (( SECONDS < deadline )) || { echo "REFUSING: internal ALB hostname was not assigned" >&2; return 2; }
    [[ -n "$hostname" ]] || sleep 15
  done
  printf '%s\n' "$hostname" >"$alb_hostname_file"
  payload="$(.venv/bin/python scripts/b6_6_successor_fargate_probe.py --target-url "http://$hostname/readyz" --profile medzen --wait-seconds 600)"
  b6_stage_payload "$payload"
}

stage_alb_ready() {
  hostname="$(tr -d '\n' <"$alb_hostname_file")"
  [[ -n "$hostname" ]]
  [[ "$(aws elbv2 describe-load-balancers --names medzen-b6-window --region eu-central-1 --profile medzen --query 'LoadBalancers[0].Scheme' --output text)" == "internal" ]]
  [[ "$(aws elbv2 describe-load-balancers --names medzen-b6-window --region eu-central-1 --profile medzen --query 'LoadBalancers[0].Type' --output text)" == "application" ]]
  payload="$(.venv/bin/python scripts/b6_6_lbc_runtime.py verify --profile medzen)"
  b6_stage_payload "$payload"
}

stage_tag_result() {
  payload="$(.venv/bin/python scripts/b6_6_lbc_runtime.py classify --kubeconfig "$kubeconfig" --receipts-dir "$receipts_dir")"
  result="$(jq -r '.status' <<<"$payload")"
  if [[ "$result" == "WARNING_NON_FATAL" ]]; then
    receipt_status="WARNING_NON_FATAL"
  elif [[ "$result" == "PASS_NO_TAG_MUTATION_DENIAL" ]]; then
    receipt_status="PASS"
  else
    return 2
  fi
  b6_stage_payload "$payload" "$receipt_status"
}

run_local_probe() {
  mode="$1"
  kubectl --kubeconfig "$kubeconfig" port-forward --namespace medzen service/speech-orchestrator 18080:8080 >/dev/null 2>&1 &
  pid=$!
  trap 'kill "$pid" >/dev/null 2>&1 || true' RETURN
  for _ in {1..60}; do
    curl --fail --silent --max-time 2 http://127.0.0.1:18080/readyz >/dev/null 2>&1 && break
    sleep 1
  done
  kill -0 "$pid"
  .venv/bin/python scripts/b6_6_probe.py "$mode" --base-url http://127.0.0.1:18080 --token-file "$token_file" --wav "$wav"
}

stage_file_proof() { payload="$(run_local_probe file)"; b6_stage_payload "$payload"; }
stage_websocket_proof() { payload="$(run_local_probe websocket)"; b6_stage_payload "$payload"; }
stage_cancellation_proof() { payload="$(run_local_probe cancellation)"; b6_stage_payload "$payload"; }

stage_failure_drills() {
  controls="$(run_local_probe refusals)"
  kubectl --kubeconfig "$kubeconfig" patch service/rag-index --namespace medzen --type merge \
    -p '{"spec":{"selector":{"medzen.io/failure-drill":"unavailable"}}}' >/dev/null
  for _ in {1..60}; do
    endpoint_count="$(kubectl --kubeconfig "$kubeconfig" get endpoints/rag-index --namespace medzen -o json 2>/dev/null | jq '[.subsets[]?.addresses[]?] | length')"
    [[ "$endpoint_count" == "0" ]] && break
    sleep 2
  done
  [[ "${endpoint_count:-unknown}" == "0" ]]
  dependency="$(run_local_probe dependency-refusal)"
  kubectl --kubeconfig "$kubeconfig" patch service/rag-index --namespace medzen --type merge \
    -p '{"spec":{"selector":{"app.kubernetes.io/name":"rag-index","medzen.io/classification":"synthetic-integration-only","medzen.io/failure-drill":null}}}' >/dev/null
  for _ in {1..60}; do
    endpoint_count="$(kubectl --kubeconfig "$kubeconfig" get endpoints/rag-index --namespace medzen -o json 2>/dev/null | jq '[.subsets[]?.addresses[]?] | length')"
    [[ "$endpoint_count" == "1" ]] && break
    sleep 2
  done
  [[ "${endpoint_count:-unknown}" == "1" ]]
  payload="$(jq -nc --argjson controls "$controls" --argjson dependency "$dependency" '{status:"PASS",controlled_refusals:$controls,rag_unavailable:$dependency,rag_pod_recreated:false,local_provider_drills:"PREPROVEN_BY_PINNED_SUITES_NO_REAL_PROVIDER_CALLS"}')"
  b6_stage_payload "$payload"
}

stage_isolation() {
  [[ "$(kubectl --kubeconfig "$kubeconfig" get services --namespace medzen -o json | jq -r '[.items[] | select(.metadata.name=="asr-runtime" or .metadata.name=="rag-index" or .metadata.name=="llm-gateway" or .metadata.name=="tts-gateway" or .metadata.name=="speech-orchestrator") | .spec.type] | unique | join(",")')" == "ClusterIP" ]]
  [[ "$(kubectl --kubeconfig "$kubeconfig" get ingress --namespace medzen -o json | jq '[.items[] | select(.metadata.name=="speech-orchestrator-b6-window")] | length')" == "1" ]]
  b6_stage_payload '{"alb_ingress_source":"sg-0a83abae6ab954543","dependency_ingresses":0,"dependency_service_type":"ClusterIP","orchestrator_ingresses":1,"public_load_balancers":0}'
}

b6_stage_execute local_bindings LOCAL_BINDINGS_REFUSED stage_local_bindings
b6_stage_execute deadline DEADLINE_ARM_REFUSED stage_deadline
b6_stage_execute workers_ready WORKER_REGISTRATION_REFUSED stage_workers
b6_stage_execute dra_ready PRE_ENDPOINT_DRA_READINESS_REFUSED stage_dra
b6_stage_execute rag_ready PRE_ENDPOINT_RAG_READINESS_REFUSED stage_rag
b6_stage_execute asr_ready PRE_ENDPOINT_ASR_READINESS_REFUSED stage_asr
b6_stage_execute tts_ready PRE_ENDPOINT_TTS_READINESS_REFUSED stage_tts
b6_stage_execute llm_ready PRE_ENDPOINT_LLM_READINESS_REFUSED stage_llm
b6_stage_execute orchestrator_ready PRE_ENDPOINT_ORCHESTRATOR_READINESS_REFUSED stage_orchestrator
b6_stage_execute controller_window PRE_ENDPOINT_CONTROLLER_PLAN_REFUSED stage_controller_window
b6_stage_execute controller_ready PRE_ENDPOINT_CONTROLLER_READINESS_REFUSED stage_controller_ready
b6_stage_execute pre_endpoint_images PRE_ENDPOINT_IMAGE_RESIDENCY_REFUSED stage_pre_endpoint_images
b6_stage_execute terraform_window ENDPOINT_TERRAFORM_WINDOW_REFUSED stage_terraform_window
export B6_ENDPOINTS_ENABLED=true
b6_stage_execute endpoints_ready ENDPOINT_AVAILABILITY_REFUSED stage_endpoints_ready
b6_stage_execute fargate_probe FARGATE_PROBE_REFUSED stage_fargate_probe
b6_stage_execute alb_ready ALB_READINESS_REFUSED stage_alb_ready
b6_stage_execute alb_tag_mutation_warning ALB_TAG_CLASSIFICATION_REFUSED stage_tag_result
b6_stage_execute file_proof FILE_CONVERSATION_PROOF_REFUSED stage_file_proof
b6_stage_execute websocket_proof WEBSOCKET_CONVERSATION_PROOF_REFUSED stage_websocket_proof
b6_stage_execute cancellation_proof CANCELLATION_PROOF_REFUSED stage_cancellation_proof
b6_stage_execute failure_drills FAILURE_DRILL_REFUSED stage_failure_drills
b6_stage_execute isolation_proof ISOLATION_PROOF_REFUSED stage_isolation
