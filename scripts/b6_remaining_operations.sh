#!/usr/bin/env bash
# Canonical operation dispatcher for prospective packet 2026-034.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
cd "$repo_root"
export PYTHONPATH="$repo_root${PYTHONPATH:+:$PYTHONPATH}"
if [[ $# -ne 8 ]]; then
  echo "usage: $0 STAGE KUBECONFIG AUTHORIZATION PACKET_SHA256 RECEIPTS_DIR TOKEN_FILE ATTEMPT PAYLOAD_PATH" >&2
  exit 2
fi
stage="$1"
kubeconfig="$2"
authorization="$3"
packet_sha256="$4"
receipts_dir="$5"
token_file="$6"
attempt="$7"
payload_path="$8"
manifest="platform/k8s/b6-6/remaining-proofs-window.yaml"
wav="platform/testdata/b6a-003c-b-synthetic.wav"
proof_audio_sha256="$(.venv/bin/python -c 'from scripts.b6_6_proof_audio_binding import PROOF_AUDIO_SHA256; print(PROOF_AUDIO_SHA256)')"
alb_hostname_file="/private/tmp/b6-034-attempt-${attempt}-alb-hostname"

write_payload() {
  jq -c . <<<"$1" >"$payload_path"
}

stage0_refuse() {
  local reason_code="$1"
  local failed_assertion="$2"
  local stage_exit_code="$3"
  local detail="$4"
  local safe_error_text
  safe_error_text="$(LC_ALL=C printf '%s' "$detail" | LC_ALL=C tr '\r\n\t' '   ' | LC_ALL=C tr -cd ' -~' | cut -c1-512)"
  [[ -n "$safe_error_text" ]] || safe_error_text="$reason_code"
  write_payload "$(jq -nc \
    --arg reason_code "$reason_code" \
    --arg failed_assertion "$failed_assertion" \
    --argjson stage_exit_code "$stage_exit_code" \
    --arg safe_error_text "$safe_error_text" \
    '{status:"REFUSED",reason_code:$reason_code,failed_assertion:$failed_assertion,stage_exit_code:$stage_exit_code,safe_error_text:$safe_error_text,pre_model_and_audio:true}')"
  return "$stage_exit_code"
}

terraform_plan_receipt() {
  terraform -chdir=infra show -json "$1" | jq -c '{
    adds: ([.resource_changes[]? | select(.change.actions == ["create"])] | length),
    changes: ([.resource_changes[]? | select(.change.actions == ["update"] or .change.actions == ["delete","create"] or .change.actions == ["create","delete"])] | length),
    destroys: ([.resource_changes[]? | select(.change.actions == ["delete"])] | length),
    resource_names: ([.resource_changes[]? | select(.change.actions != ["no-op"] and .change.actions != ["read"]) | .address] | sort)
  }'
}

[[ "${AWS_PROFILE:-}" == "medzen" ]] || { echo "REFUSING: AWS_PROFILE=medzen is required" >&2; exit 2; }
[[ -f "$kubeconfig" && -f "$authorization" ]] || { echo "REFUSING: required execution input is absent" >&2; exit 2; }
[[ "$token_file" == "/private/tmp/medzen-b6-6-client-token" ]] || { echo "REFUSING: exact synthetic token path is required" >&2; exit 2; }
[[ "$attempt" == "1" || "$attempt" == "2" ]] || { echo "REFUSING: packet 2026-034 permits only attempts 1 or 2" >&2; exit 2; }
[[ "$proof_audio_sha256" =~ ^[0-9a-f]{64}$ ]] || { echo "REFUSING: proof-audio binding is malformed" >&2; exit 2; }

stage_stage0() {
  local alignment_output alignment_payload binding_output credential_output credential_payload command_status observed readback_output readback_payload
  set +e
  binding_output="$(.venv/bin/python - "$authorization" "$packet_sha256" "$repo_root" 2>&1 <<'PY'
import sys
from pathlib import Path
from scripts.b6_remaining_bindings import validate
validate(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]))
PY
  )"
  command_status=$?
  set -e
  if [[ "$command_status" != "0" ]]; then
    stage0_refuse STAGE0_BINDING_REFUSED SOURCE_BINDINGS_AND_AUTHORIZATION_VALID 31 "$binding_output"
    return $?
  fi

  set +e
  alignment_output="$(.venv/bin/python scripts/b6_6_registry_rag_alignment.py audit 2>&1)"
  command_status=$?
  set -e
  if [[ "$command_status" != "0" ]] || ! alignment_payload="$(jq -ce 'select(.status == "PASS_ALIGNED_RAG_PROOF_PATH")' <<<"$alignment_output" 2>/dev/null)"; then
    observed="$(jq -r '.reason_code // "registry and RAG proof alignment output malformed"' <<<"$alignment_output" 2>/dev/null || printf '%s' "$alignment_output")"
    stage0_refuse STAGE0_RAG_ALIGNMENT_REFUSED PROOF_INPUT_REGISTRY_AND_RAG_IDENTITIES_ALIGN 42 "$observed"
    return $?
  fi

  if [[ -e "$token_file" || -e "$alb_hostname_file" ]]; then
    stage0_refuse STAGE0_LOCAL_PATH_REFUSED LOCAL_EPHEMERAL_PATHS_ABSENT 32 "preexisting local token or ALB hostname path"
    return $?
  fi

  set +e
  readback_output="$(.venv/bin/python scripts/b6_6_registry_readback.py --profile medzen 2>&1)"
  command_status=$?
  set -e
  if [[ "$command_status" != "0" ]] || ! readback_payload="$(jq -ce 'select(.status == "PASS_REUSE_IDENTICAL_COMPLETE")' <<<"$readback_output" 2>/dev/null)"; then
    observed="$(jq -r '.reason_code // "registry read-back output malformed"' <<<"$readback_output" 2>/dev/null || printf '%s' "$readback_output")"
    stage0_refuse STAGE0_REGISTRY_READBACK_REFUSED CONTENT_ADDRESSED_TEST_REGISTRY_IS_EXACT_AND_PRODUCTION_ABSENT 43 "$observed"
    return $?
  fi

  set +e
  credential_output="$(.venv/bin/python scripts/b6_6_credential.py --token-file "$token_file" --profile medzen 2>&1)"
  command_status=$?
  set -e
  if [[ "$command_status" != "0" ]] || ! credential_payload="$(jq -ce 'select(.status == "PASS")' <<<"$credential_output" 2>/dev/null)"; then
    observed="$(jq -r '.safe_error_text // .reason_code // "credential rotation output malformed"' <<<"$credential_output" 2>/dev/null || printf '%s' "$credential_output")"
    stage0_refuse STAGE0_CREDENTIAL_ROTATION_REFUSED FRESH_SYNTHETIC_CREDENTIAL_ROTATED 33 "$observed"
    return $?
  fi

  set +e
  observed="$(aws sts get-caller-identity --profile medzen --query Account --output text 2>&1)"
  command_status=$?
  set -e
  if [[ "$command_status" != "0" || "$observed" != "558069890522" ]]; then
    stage0_refuse STAGE0_AWS_IDENTITY_REFUSED AWS_ACCOUNT_IS_558069890522 34 "$observed"
    return $?
  fi

  for nodegroup in cpu gpu; do
    set +e
    observed="$(aws eks describe-nodegroup --cluster-name medzen-speech --nodegroup-name "$nodegroup" --region eu-central-1 --profile medzen --query 'nodegroup.scalingConfig.desiredSize' --output text 2>&1)"
    command_status=$?
    set -e
    if [[ "$command_status" != "0" || "$observed" != "0" ]]; then
      stage0_refuse STAGE0_WORKER_CAPACITY_ZERO_REFUSED WORKER_DESIRED_CAPACITY_IS_ZERO 44 "$nodegroup:$observed"
      return $?
    fi
  done

  set +e
  observed="$(aws ssm get-parameters-by-path --path /medzen/registry/test/b6/d4f9696d288e0ea6c1d139f496e00eaf097b77ea8b3a4f5a26a6470286adfe81 --recursive --with-decryption --region eu-central-1 --profile medzen --query 'length(Parameters)' --output text 2>&1)"
  command_status=$?
  set -e
  if [[ "$command_status" != "0" || "$observed" != "3" ]]; then
    stage0_refuse STAGE0_TEST_REGISTRY_REFUSED TEST_REGISTRY_PARAMETER_COUNT_IS_THREE 35 "$observed"
    return $?
  fi

  set +e
  observed="$(aws ssm get-parameters-by-path --path /medzen/registry/serving --recursive --with-decryption --region eu-central-1 --profile medzen --query 'length(Parameters[?Name==`/medzen/registry/serving/current`])' --output text 2>&1)"
  command_status=$?
  set -e
  if [[ "$command_status" != "0" || "$observed" != "0" ]]; then
    stage0_refuse STAGE0_PRODUCTION_POINTER_REFUSED PRODUCTION_SERVING_POINTER_IS_ABSENT 36 "$observed"
    return $?
  fi

  set +e
  observed="$(kubectl --kubeconfig "$kubeconfig" get nodes -l 'workload in (cpu,gpu)' --request-timeout=15s -o json 2>&1)"
  command_status=$?
  set -e
  if [[ "$command_status" != "0" ]]; then
    stage0_refuse STAGE0_WORKLOAD_NODE_ZERO_REFUSED WORKLOAD_NODE_COUNT_IS_ZERO 37 "$observed"
    return $?
  fi
  observed="$(jq -r '.items | length' <<<"$observed" 2>/dev/null || printf 'malformed node response')"
  if [[ "$observed" != "0" ]]; then
    stage0_refuse STAGE0_WORKLOAD_NODE_ZERO_REFUSED WORKLOAD_NODE_COUNT_IS_ZERO 37 "$observed"
    return $?
  fi

  set +e
  observed="$(kubectl --kubeconfig "$kubeconfig" get pods -n medzen -l medzen.io/classification=synthetic-integration-only --request-timeout=15s -o json 2>&1)"
  command_status=$?
  set -e
  if [[ "$command_status" != "0" ]]; then
    stage0_refuse STAGE0_SYNTHETIC_POD_ZERO_REFUSED SYNTHETIC_POD_COUNT_IS_ZERO 38 "$observed"
    return $?
  fi
  observed="$(jq -r '.items | length' <<<"$observed" 2>/dev/null || printf 'malformed pod response')"
  if [[ "$observed" != "0" ]]; then
    stage0_refuse STAGE0_SYNTHETIC_POD_ZERO_REFUSED SYNTHETIC_POD_COUNT_IS_ZERO 38 "$observed"
    return $?
  fi

  set +e
  observed="$(kubectl --kubeconfig "$kubeconfig" get deployments -n medzen -l medzen.io/classification=synthetic-integration-only --request-timeout=15s -o json 2>&1)"
  command_status=$?
  set -e
  if [[ "$command_status" != "0" ]]; then
    stage0_refuse STAGE0_SYNTHETIC_DEPLOYMENT_ZERO_REFUSED SYNTHETIC_DEPLOYMENT_COUNT_IS_ZERO 46 "$observed"
    return $?
  fi
  observed="$(jq -r '.items | length' <<<"$observed" 2>/dev/null || printf 'malformed deployment response')"
  if [[ "$observed" != "0" ]]; then
    stage0_refuse STAGE0_SYNTHETIC_DEPLOYMENT_ZERO_REFUSED SYNTHETIC_DEPLOYMENT_COUNT_IS_ZERO 46 "$observed"
    return $?
  fi

  set +e
  observed="$(kubectl --kubeconfig "$kubeconfig" get deployment/aws-load-balancer-controller --namespace kube-system --ignore-not-found --request-timeout=15s -o name 2>&1)"
  command_status=$?
  set -e
  if [[ "$command_status" != "0" || -n "$observed" ]]; then
    stage0_refuse STAGE0_CONTROLLER_ABSENCE_REFUSED WINDOW_CONTROLLER_IS_ABSENT 39 "$observed"
    return $?
  fi

  set +e
  observed="$(kubectl --kubeconfig "$kubeconfig" get daemonset/dra-driver-nvidia-gpu-kubelet-plugin --namespace nvidia-dra-driver --ignore-not-found --request-timeout=15s -o name 2>&1)"
  command_status=$?
  set -e
  if [[ "$command_status" != "0" || -n "$observed" ]]; then
    stage0_refuse STAGE0_DRA_ABSENCE_REFUSED WINDOW_DRA_IS_ABSENT 40 "$observed"
    return $?
  fi

  set +e
  observed="$(.venv/bin/python scripts/b6_6_probe_endpoints.py absent --profile medzen 2>&1)"
  command_status=$?
  set -e
  if [[ "$command_status" != "0" ]]; then
    stage0_refuse STAGE0_ENDPOINT_ABSENCE_REFUSED WINDOW_ENDPOINTS_ARE_ABSENT 41 "$observed"
    return $?
  fi

  set +e
  observed="$(aws elbv2 describe-load-balancers --names medzen-b6-window --region eu-central-1 --profile medzen 2>&1)"
  command_status=$?
  set -e
  if [[ "$command_status" == "0" ]] || ! grep -q 'LoadBalancerNotFound' <<<"$observed"; then
    stage0_refuse STAGE0_ALB_ABSENCE_REFUSED WINDOW_ALB_IS_ABSENT 45 "$observed"
    return $?
  fi
  write_payload "$(jq -nc --argjson alignment "$alignment_payload" --argjson readback "$readback_payload" --argjson credential "$credential_payload" --arg attempt "$attempt" '{authorization_record:"HASH_BOUND_OWNER_APPROVED",cost_registry:"COST-REGISTRY-2026-005",attempt:($attempt|tonumber),source_bindings_verified:true,registry_rag_alignment:$alignment,registry_readback:$readback,credential:$credential,preserved_file_proof:{status:"PASS",sha256:"808d160e391998e3f534d8776342e58337ebb4a200ffaab58fcc43e586c60c89",rerun:false}}')"
}

stage_deadline() {
  payload="$(.venv/bin/python scripts/b6_6_deadline.py arm)"
  write_payload "$payload"
}

stage_workers() {
  aws eks update-nodegroup-config --cluster-name medzen-speech --nodegroup-name cpu --scaling-config minSize=0,maxSize=4,desiredSize=2 --region eu-central-1 --profile medzen >/dev/null
  aws eks update-nodegroup-config --cluster-name medzen-speech --nodegroup-name gpu --scaling-config minSize=0,maxSize=1,desiredSize=1 --region eu-central-1 --profile medzen >/dev/null
  aws eks wait nodegroup-active --cluster-name medzen-speech --nodegroup-name cpu --region eu-central-1 --profile medzen
  aws eks wait nodegroup-active --cluster-name medzen-speech --nodegroup-name gpu --region eu-central-1 --profile medzen
  payload="$(.venv/bin/python scripts/b6_6_wait_workers.py --kubeconfig "$kubeconfig" --wait-seconds 1200)"
  write_payload "$payload"
}

stage_dra() {
  kubectl --kubeconfig "$kubeconfig" apply -f platform/k8s/b6a/nvidia-dra-003c-b.locked.yaml
  kubectl --kubeconfig "$kubeconfig" rollout status daemonset/dra-driver-nvidia-gpu-kubelet-plugin --namespace nvidia-dra-driver --timeout=15m
  stable="$(.venv/bin/python scripts/b6_6_k8s_stability.py daemonset --kubeconfig "$kubeconfig" --namespace nvidia-dra-driver --name dra-driver-nvidia-gpu-kubelet-plugin --expected-image 558069890522.dkr.ecr.eu-central-1.amazonaws.com/medzen-nvidia-dra@sha256:7fb313758a20a04e80a53d5f6d1efe8e6fe936bc845001c21204c1c361d59246 --wait-seconds 900)"
  write_payload "$(jq -c '. + {daemonset_ready:true,digest_pinned:true,gpu_nodes:1,before_private_endpoints:true}' <<<"$stable")"
}

stage_rag() {
  .venv/bin/python scripts/b6_remaining_manifest_slice.py pre-endpoint | kubectl --kubeconfig "$kubeconfig" apply -f -
  kubectl --kubeconfig "$kubeconfig" scale deployment/rag-index --namespace medzen --replicas=1
  kubectl --kubeconfig "$kubeconfig" rollout status deployment/rag-index --namespace medzen --timeout=10m
  stable="$(.venv/bin/python scripts/b6_6_k8s_stability.py deployment --kubeconfig "$kubeconfig" --namespace medzen --name rag-index --replicas 1 --expected-image 558069890522.dkr.ecr.eu-central-1.amazonaws.com/medzen-rag-index@sha256:fe4663812f88bd35d520fee3e80450981347c970f2a561eb8163b14183b7194c --wait-seconds 600)"
  write_payload "$(jq -c '. + {ready_replicas:1,provider:"embedded_synthetic",service_type:"ClusterIP",before_private_endpoints:true}' <<<"$stable")"
}

stage_asr() {
  kubectl --kubeconfig "$kubeconfig" scale deployment/asr-runtime --namespace medzen --replicas=1
  kubectl --kubeconfig "$kubeconfig" rollout status deployment/asr-runtime --namespace medzen --timeout=30m
  stable="$(.venv/bin/python scripts/b6_6_k8s_stability.py deployment --kubeconfig "$kubeconfig" --namespace medzen --name asr-runtime --replicas 1 --expected-image 558069890522.dkr.ecr.eu-central-1.amazonaws.com/medzen-model-loader@sha256:cb794f2169dc65f391a0c9ec789997ce19a31b38d9087f263fba0863ba0414a5 --expected-image 558069890522.dkr.ecr.eu-central-1.amazonaws.com/medzen-asr-runtime@sha256:434ac9e757b56949324e9e480490042fcbf35285f27bdee713bd771b502f4087 --wait-seconds 1800)"
  write_payload "$(jq -c '. + {artifact_tree_sha256:"5adf77568813513bc3697a1501ba354c04c7b93ea374fc5407cf4f6402f7431e",model:"zero-shot-whisper-large-v3",ready_replicas:1,serving_label:"v0",before_private_endpoints:true}' <<<"$stable")"
}

stage_tts() {
  kubectl --kubeconfig "$kubeconfig" scale deployment/tts-gateway --namespace medzen --replicas=1
  kubectl --kubeconfig "$kubeconfig" rollout status deployment/tts-gateway --namespace medzen --timeout=10m
  stable="$(.venv/bin/python scripts/b6_6_k8s_stability.py deployment --kubeconfig "$kubeconfig" --namespace medzen --name tts-gateway --replicas 1 --expected-image 558069890522.dkr.ecr.eu-central-1.amazonaws.com/medzen-speech-tts-gateway@sha256:88e83b97a03c593505435981c554d5d0f3045c4acb4a7224148d58e3af96087d --wait-seconds 600)"
  write_payload "$(jq -c '. + {fish_calls:0,provider:"text_only",ready_replicas:1,service_type:"ClusterIP",before_private_endpoints:true}' <<<"$stable")"
}

stage_llm() {
  kubectl --kubeconfig "$kubeconfig" scale deployment/llm-gateway --namespace medzen --replicas=1
  kubectl --kubeconfig "$kubeconfig" rollout status deployment/llm-gateway --namespace medzen --timeout=10m
  stable="$(.venv/bin/python scripts/b6_6_k8s_stability.py deployment --kubeconfig "$kubeconfig" --namespace medzen --name llm-gateway --replicas 1 --expected-image 558069890522.dkr.ecr.eu-central-1.amazonaws.com/medzen-llm-gateway@sha256:88026dd9708073dcd3622e7dd68e7a70aff98cddd43129c53c017d571f533f5a --wait-seconds 600)"
  write_payload "$(jq -c '. + {bedrock_calls:0,provider:"fake",ready_replicas:1,service_type:"ClusterIP",before_private_endpoints:true}' <<<"$stable")"
}

stage_orchestrator() {
  kubectl --kubeconfig "$kubeconfig" scale deployment/speech-orchestrator --namespace medzen --replicas=1
  kubectl --kubeconfig "$kubeconfig" rollout status deployment/speech-orchestrator --namespace medzen --timeout=10m
  stable="$(.venv/bin/python scripts/b6_6_k8s_stability.py deployment --kubeconfig "$kubeconfig" --namespace medzen --name speech-orchestrator --replicas 1 --expected-image 558069890522.dkr.ecr.eu-central-1.amazonaws.com/medzen-orchestrator@sha256:475ff8520e7ff78a52208a1bebe1de78c2a257de112424a837d0f5e1a73d2dc3 --wait-seconds 600)"
  expected='["sha256:434ac9e757b56949324e9e480490042fcbf35285f27bdee713bd771b502f4087","sha256:475ff8520e7ff78a52208a1bebe1de78c2a257de112424a837d0f5e1a73d2dc3","sha256:88026dd9708073dcd3622e7dd68e7a70aff98cddd43129c53c017d571f533f5a","sha256:88e83b97a03c593505435981c554d5d0f3045c4acb4a7224148d58e3af96087d","sha256:cb794f2169dc65f391a0c9ec789997ce19a31b38d9087f263fba0863ba0414a5","sha256:fe4663812f88bd35d520fee3e80450981347c970f2a561eb8163b14183b7194c"]'
  images="$(.venv/bin/python scripts/b6_6_k8s_stability.py pod-images --kubeconfig "$kubeconfig" --namespace medzen --selector medzen.io/classification=synthetic-integration-only --expected-digest sha256:434ac9e757b56949324e9e480490042fcbf35285f27bdee713bd771b502f4087 --expected-digest sha256:475ff8520e7ff78a52208a1bebe1de78c2a257de112424a837d0f5e1a73d2dc3 --expected-digest sha256:88026dd9708073dcd3622e7dd68e7a70aff98cddd43129c53c017d571f533f5a --expected-digest sha256:88e83b97a03c593505435981c554d5d0f3045c4acb4a7224148d58e3af96087d --expected-digest sha256:cb794f2169dc65f391a0c9ec789997ce19a31b38d9087f263fba0863ba0414a5 --expected-digest sha256:fe4663812f88bd35d520fee3e80450981347c970f2a561eb8163b14183b7194c --wait-seconds 600)"
  [[ "$(jq -c '.resident_child_digests' <<<"$images")" == "$expected" ]]
  write_payload "$(jq -nc --argjson stable "$stable" --argjson images "$images" '$stable + $images + {authentication_loaded:true,mode:"deployed_http_ssm",ready_replicas:1,registry_snapshot:"d4f9696d288e0ea6c1d139f496e00eaf097b77ea8b3a4f5a26a6470286adfe81",workload_child_digests_verified:6,before_private_endpoints:true}')"
}

stage_controller_window() {
  plan="/private/tmp/b6-034-controller-$PPID.tfplan"
  scripts/terraform_medzen.sh plan -input=false -out="$plan" \
    -var=account_id=558069890522 \
    -var=registry_publisher_principal_arn=arn:aws:iam::558069890522:user/s.fotso \
    -var=enable_b6_load_balancer_controller=true \
    -var=enable_b6_integration_window=false \
    -var=enable_b6_probe_qualification=false \
    -target=helm_release.b6_load_balancer_controller
  .venv/bin/python scripts/check_b6_6_window_plan.py controller "$plan"
  plan_receipt="$(terraform_plan_receipt "$plan")"
  jq -e '.adds == 1 and .changes == 0 and .destroys == 0 and .resource_names == ["helm_release.b6_load_balancer_controller[0]"]' <<<"$plan_receipt" >/dev/null
  scripts/terraform_medzen.sh apply -input=false -auto-approve "$plan"
  write_payload "$(jq -c '. + {before_private_endpoints:true}' <<<"$plan_receipt")"
}

stage_controller_ready() {
  kubectl --kubeconfig "$kubeconfig" rollout status deployment/aws-load-balancer-controller --namespace kube-system --timeout=10m
  stable="$(.venv/bin/python scripts/b6_6_k8s_stability.py deployment --kubeconfig "$kubeconfig" --namespace kube-system --name aws-load-balancer-controller --replicas 1 --expected-image 558069890522.dkr.ecr.eu-central-1.amazonaws.com/medzen-aws-load-balancer-controller@sha256:c2ebdeae779c796e3d071d7a0d3a4ebdbb31e4e8d53e3e5372ee0ab0c4f3f08f --wait-seconds 600)"
  write_payload "$(jq -c '. + {controller_replicas_ready:1,digest_pinned:true,namespace_watch:"medzen",before_private_endpoints:true}' <<<"$stable")"
}

stage_pre_endpoint_images() {
  .venv/bin/python scripts/b6_6_probe_endpoints.py absent --profile medzen >/dev/null
  payload="$(.venv/bin/python scripts/b6_remaining_pre_endpoint_images.py pre --kubeconfig "$kubeconfig" --wait-seconds 600)"
  write_payload "$payload"
}

stage_terraform_window() {
  plan="/private/tmp/b6-034-endpoints-$PPID.tfplan"
  targets=(
    -target=helm_release.b6_load_balancer_controller
    -target=aws_security_group.b6_probe_endpoints
    -target=aws_vpc_security_group_ingress_rule.b6_alb_from_backend
    -target=aws_vpc_security_group_ingress_rule.b6_nodes_from_alb
    -target=aws_vpc_security_group_ingress_rule.b6_probe_to_endpoints
    -target=aws_vpc_security_group_egress_rule.b6_probe_to_ecr_endpoints
    -target=aws_vpc_security_group_egress_rule.b6_probe_to_s3
    -target=aws_vpc_endpoint.b6_probe_ecr_api
    -target=aws_vpc_endpoint.b6_probe_ecr_dkr
    -target=aws_vpc_endpoint.b6_probe_s3
    -target=aws_iam_role.b6_probe_execution
    -target=aws_iam_role_policy.b6_probe_execution
    -target=aws_ecs_cluster.b6_probe
    -target=aws_ecs_task_definition.b6_probe
  )
  scripts/terraform_medzen.sh plan -input=false -out="$plan" \
    -var=account_id=558069890522 \
    -var=registry_publisher_principal_arn=arn:aws:iam::558069890522:user/s.fotso \
    -var=enable_b6_load_balancer_controller=true \
    -var=enable_b6_integration_window=true \
    -var=enable_b6_probe_qualification=false "${targets[@]}"
  .venv/bin/python scripts/check_b6_6_window_plan.py endpoints "$plan"
  plan_receipt="$(terraform_plan_receipt "$plan")"
  expected_resources='["aws_ecs_cluster.b6_probe[0]","aws_ecs_task_definition.b6_probe[0]","aws_iam_role.b6_probe_execution[0]","aws_iam_role_policy.b6_probe_execution[0]","aws_security_group.b6_probe_endpoints[0]","aws_vpc_endpoint.b6_probe_ecr_api[0]","aws_vpc_endpoint.b6_probe_ecr_dkr[0]","aws_vpc_endpoint.b6_probe_s3[0]","aws_vpc_security_group_egress_rule.b6_probe_to_ecr_endpoints[0]","aws_vpc_security_group_egress_rule.b6_probe_to_s3[0]","aws_vpc_security_group_ingress_rule.b6_alb_from_backend[0]","aws_vpc_security_group_ingress_rule.b6_nodes_from_alb[0]","aws_vpc_security_group_ingress_rule.b6_probe_to_endpoints[0]"]'
  jq -e --argjson expected "$expected_resources" '.adds == 13 and .changes == 0 and .destroys == 0 and .resource_names == $expected' <<<"$plan_receipt" >/dev/null
  scripts/terraform_medzen.sh apply -input=false -auto-approve "$plan"
  write_payload "$(jq -c '. + {controller_changes:0,endpoint_security_groups_created:1,fargate_maximum_tasks:1,iam_roles_created:1,security_group_ingress_rules_created:3,security_group_egress_rules_created:2,vpc_endpoints_created:3}' <<<"$plan_receipt")"
}

stage_endpoints_ready() {
  payload="$(.venv/bin/python scripts/b6_6_probe_endpoints.py available --profile medzen --wait-seconds 900)"
  write_payload "$payload"
}

stage_alb_ready() {
  .venv/bin/python scripts/b6_remaining_manifest_slice.py ingress | kubectl --kubeconfig "$kubeconfig" apply -f -
  deadline=$((SECONDS + 900))
  hostname=""
  while [[ -z "$hostname" ]]; do
    hostname="$(kubectl --kubeconfig "$kubeconfig" get ingress/speech-orchestrator-b6-window --namespace medzen -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || true)"
    (( SECONDS < deadline )) || { echo "REFUSING: internal ALB hostname was not assigned" >&2; return 2; }
    [[ -n "$hostname" ]] || sleep 15
  done
  printf '%s\n' "$hostname" >"$alb_hostname_file"
  ready_status=0
  payload="$(.venv/bin/python scripts/b6_6_lbc_runtime.py wait-ready --profile medzen --wait-seconds 900)" || ready_status=$?
  write_payload "$payload"
  [[ "$ready_status" == "0" ]] || return "$ready_status"
}

stage_fargate_probe() {
  hostname="$(tr -d '\n' <"$alb_hostname_file")"
  [[ -n "$hostname" ]]
  probe_status=0
  payload="$(.venv/bin/python scripts/b6_6_fargate_probe.py --target-url "http://$hostname/readyz" --profile medzen --wait-seconds 600)" || probe_status=$?
  write_payload "$payload"
  [[ "$probe_status" == "0" ]] || return "$probe_status"
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
  payload="$(jq -c --arg status "$receipt_status" '. + {receipt_status:$status}' <<<"$payload")"
  write_payload "$payload"
}

run_local_probe() {
  mode="$1"
  kubectl --kubeconfig "$kubeconfig" port-forward --namespace medzen service/speech-orchestrator 18080:8080 >/dev/null 2>&1 &
  pid=$!
  trap 'kill "$pid" >/dev/null 2>&1 || true' RETURN
  ready=0
  stable_ready=0
  for _ in {1..60}; do
    if curl --fail --silent --max-time 2 http://127.0.0.1:18080/readyz >/dev/null 2>&1; then
      stable_ready=$((stable_ready + 1))
      if [[ "$stable_ready" == "2" ]]; then
        ready=1
        break
      fi
    else
      stable_ready=0
    fi
    sleep 1
  done
  if ! kill -0 "$pid" >/dev/null 2>&1; then
    jq -nc '{status:"REFUSED",reason_code:"SYNTHETIC_PROOF_ASSERTION_REFUSED",failed_assertion:"LOCAL_PORT_FORWARD_PROCESS_ALIVE",probe_exit_code:100,http_status:null,sanitized_response_body:"",response_body_truncated:false,response_body_sha256:"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",safe_error_text:"local port-forward process exited",synthetic_only:true,phi_present:false}'
    return 100
  fi
  if [[ "$ready" != "1" ]]; then
    jq -nc '{status:"REFUSED",reason_code:"SYNTHETIC_PROOF_ASSERTION_REFUSED",failed_assertion:"LOCAL_PORT_FORWARD_READYZ",probe_exit_code:101,http_status:null,sanitized_response_body:"",response_body_truncated:false,response_body_sha256:"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",safe_error_text:"local port-forward readyz did not pass within 60 seconds",synthetic_only:true,phi_present:false}'
    return 101
  fi
  MEDZEN_B6_PROOF_AUDIO_SHA256="$proof_audio_sha256" \
    .venv/bin/python scripts/b6_6_probe.py "$mode" --base-url http://127.0.0.1:18080 --token-file "$token_file" --wav "$wav"
}

run_probe_stage() {
  local mode="$1"
  local payload probe_status
  set +e
  payload="$(run_local_probe "$mode")"
  probe_status=$?
  set -e
  if ! jq -e 'type == "object" and (.status == "PASS" or .status == "REFUSED")' <<<"$payload" >/dev/null 2>&1; then
    payload="$(jq -nc '{status:"REFUSED",reason_code:"SYNTHETIC_PROOF_ASSERTION_REFUSED",failed_assertion:"PROBE_DIAGNOSTIC_JSON_VALID",probe_exit_code:102,http_status:null,sanitized_response_body:"",response_body_truncated:false,response_body_sha256:"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",safe_error_text:"probe diagnostic output is malformed",synthetic_only:true,phi_present:false}')"
    probe_status=102
  fi
  write_payload "$payload"
  [[ "$probe_status" == "0" ]] || return "$probe_status"
}

stage_websocket_proof() { run_probe_stage websocket; }
stage_cancellation_proof() { run_probe_stage cancellation; }

stage_failure_drills() {
  set +e
  controls="$(run_local_probe refusals)"
  probe_status=$?
  set -e
  if [[ "$probe_status" != "0" ]]; then
    write_payload "$controls"
    return "$probe_status"
  fi
  kubectl --kubeconfig "$kubeconfig" patch service/rag-index --namespace medzen --type merge \
    -p '{"spec":{"selector":{"medzen.io/failure-drill":"unavailable"}}}' >/dev/null
  unavailable_stable="$(.venv/bin/python scripts/b6_6_k8s_stability.py endpoints --kubeconfig "$kubeconfig" --namespace medzen --name rag-index --count 0 --wait-seconds 120)"
  set +e
  dependency="$(run_local_probe dependency-refusal)"
  probe_status=$?
  set -e
  if [[ "$probe_status" != "0" ]]; then
    write_payload "$dependency"
    return "$probe_status"
  fi
  kubectl --kubeconfig "$kubeconfig" patch service/rag-index --namespace medzen --type merge \
    -p '{"spec":{"selector":{"app.kubernetes.io/name":"rag-index","medzen.io/classification":"synthetic-integration-only","medzen.io/failure-drill":null}}}' >/dev/null
  restored_stable="$(.venv/bin/python scripts/b6_6_k8s_stability.py endpoints --kubeconfig "$kubeconfig" --namespace medzen --name rag-index --count 1 --wait-seconds 120)"
  payload="$(jq -nc --argjson controls "$controls" --argjson dependency "$dependency" --argjson unavailable "$unavailable_stable" --argjson restored "$restored_stable" '{status:"PASS",controlled_refusals:$controls,rag_unavailable:$dependency,rag_unavailable_stability:$unavailable,rag_restored_stability:$restored,rag_pod_recreated:false,local_provider_drills:"PREPROVEN_BY_PINNED_SUITES_NO_REAL_PROVIDER_CALLS"}')"
  write_payload "$payload"
}

stage_isolation() {
  stable="$(.venv/bin/python scripts/b6_6_k8s_stability.py isolation --kubeconfig "$kubeconfig" --wait-seconds 120)"
  write_payload "$(jq -c '. + {alb_ingress_source:"sg-0a83abae6ab954543",public_load_balancers:0}' <<<"$stable")"
}

case "$stage" in
  stage0) stage_stage0 ;;
  deadline) stage_deadline ;;
  workers_ready) stage_workers ;;
  dra_ready) stage_dra ;;
  rag_ready) stage_rag ;;
  asr_ready) stage_asr ;;
  tts_ready) stage_tts ;;
  llm_ready) stage_llm ;;
  orchestrator_ready) stage_orchestrator ;;
  controller_window) stage_controller_window ;;
  controller_ready) stage_controller_ready ;;
  pre_endpoint_images) stage_pre_endpoint_images ;;
  terraform_window) stage_terraform_window ;;
  endpoints_ready) stage_endpoints_ready ;;
  alb_ready) stage_alb_ready ;;
  fargate_probe) stage_fargate_probe ;;
  alb_tag_mutation_warning) stage_tag_result ;;
  websocket_proof) stage_websocket_proof ;;
  cancellation_proof) stage_cancellation_proof ;;
  failure_drills) stage_failure_drills ;;
  isolation_proof) stage_isolation ;;
  cleanup|cleanup-recovery)
    bash scripts/b6_remaining_cleanup.sh "$kubeconfig" "$authorization" "$packet_sha256" "$receipts_dir" "$token_file" "$attempt" "$payload_path"
    ;;
  *) echo "REFUSING: unknown B6.6 operation stage" >&2; exit 2 ;;
esac
