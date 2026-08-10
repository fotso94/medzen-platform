#!/usr/bin/env bash
# Structural stage wrapper: every attempted stage persists PASS or REFUSED.

b6_stage_payload() {
  [[ -n "${B6_STAGE_PAYLOAD_FILE:-}" ]] || return 2
  jq -ec 'if type == "object" then . else error("payload must be an object") end' \
    <<<"$1" >"$B6_STAGE_PAYLOAD_FILE"
  if [[ $# -eq 2 ]]; then
    [[ "$2" == "PASS" || "$2" == "WARNING_NON_FATAL" ]] || return 2
    printf '%s\n' "$2" >"$B6_STAGE_STATUS_FILE"
  fi
}

b6_stage_execute() {
  if [[ $# -lt 3 ]]; then
    echo "REFUSING: b6_stage_execute requires STAGE REASON_CODE COMMAND" >&2
    return 2
  fi
  local stage="$1"
  local default_reason="$2"
  shift 2
  local caller_errexit=false
  [[ $- == *e* ]] && caller_errexit=true
  local payload_file
  payload_file="$(mktemp -t medzen-b6-stage-payload.XXXXXX)"
  local status_file
  status_file="$(mktemp -t medzen-b6-stage-status.XXXXXX)"
  : >"$payload_file"
  printf 'PASS\n' >"$status_file"

  set +e
  (
    set -eEuo pipefail
    export B6_STAGE_PAYLOAD_FILE="$payload_file"
    export B6_STAGE_STATUS_FILE="$status_file"
    "$@"
  )
  local command_status=$?
  if [[ "$caller_errexit" == "true" ]]; then set -e; else set +e; fi

  local receipt_status="PASS"
  local payload
  if [[ $command_status -eq 0 ]] && jq -e 'type == "object"' "$payload_file" >/dev/null 2>&1; then
    payload="$(jq -c . "$payload_file")"
    receipt_status="$(tr -d '\n' <"$status_file")"
    [[ "$receipt_status" == "PASS" || "$receipt_status" == "WARNING_NON_FATAL" ]] || {
      command_status=2
      receipt_status="REFUSED"
      payload='{"reason_code":"STAGE_RECEIPT_STATUS_INVALID","command_exit_code":2,"post_endpoint_image_diagnosis":{"status":"NOT_CHECKED","reason_code":"STATUS_INVALID"}}'
    }
  else
    if [[ $command_status -eq 0 ]]; then command_status=2; fi
    receipt_status="REFUSED"
    local reason_code="$default_reason"
    local post_endpoint_diagnosis='{"status":"NOT_CHECKED","reason_code":"ENDPOINTS_NOT_ENABLED"}'
    if [[ "${B6_ENDPOINTS_ENABLED:-false}" == "true" && -n "${B6_KUBECONFIG:-}" ]]; then
      set +e
      post_endpoint_diagnosis="$(.venv/bin/python scripts/b6_6_pre_endpoint_images.py post-failure --kubeconfig "$B6_KUBECONFIG")"
      local diagnosis_status=$?
      if [[ "$caller_errexit" == "true" ]]; then set -e; else set +e; fi
      if [[ $diagnosis_status -eq 3 ]]; then
        reason_code="POST_ENDPOINT_NEW_KUBERNETES_IMAGE_PULL_FATAL"
      fi
    fi
    if ! jq -e 'type == "object"' <<<"$post_endpoint_diagnosis" >/dev/null 2>&1; then
      post_endpoint_diagnosis='{"status":"NOT_CHECKED","reason_code":"POST_ENDPOINT_DIAGNOSIS_MALFORMED"}'
    fi
    set +e
    payload="$(jq -nc \
      --arg reason "$reason_code" \
      --argjson command_exit_code "$command_status" \
      --argjson diagnosis "$post_endpoint_diagnosis" \
      '{reason_code:$reason,command_exit_code:$command_exit_code,post_endpoint_image_diagnosis:$diagnosis}')"
    local payload_status=$?
    if [[ "$caller_errexit" == "true" ]]; then set -e; else set +e; fi
    if [[ $payload_status -ne 0 ]]; then
      payload='{"reason_code":"STRUCTURAL_RECEIPT_PAYLOAD_REFUSED","command_exit_code":2,"post_endpoint_image_diagnosis":{"status":"NOT_CHECKED","reason_code":"PAYLOAD_ENCODING_REFUSED"}}'
      command_status=2
    fi
  fi
  /bin/rm -f -- "$payload_file" "$status_file"

  .venv/bin/python scripts/b6_6_receipt_v2.py \
    "$stage" "$receipt_status" --receipts-dir "$B6_STAGE_RECEIPTS_DIR" \
    --payload "$payload"
  if [[ "$receipt_status" != "PASS" && "$receipt_status" != "WARNING_NON_FATAL" ]]; then
    if [[ "$caller_errexit" == "true" ]]; then set -e; else set +e; fi
    return "$command_status"
  fi
  if [[ "$caller_errexit" == "true" ]]; then set -e; else set +e; fi
}
