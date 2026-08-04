#!/usr/bin/env bash
set -euo pipefail

# Local/operator Terraform entry point. CI uses workload credentials and calls
# Terraform directly; this wrapper prevents a developer shell from silently
# using another account's default AWS credentials for the S3 backend.

EXPECTED_PROFILE="medzen"
EXPECTED_ACCOUNT="558069890522"
EXPECTED_CALLER="arn:aws:iam::558069890522:user/s.fotso"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ "${AWS_PROFILE:-}" != "$EXPECTED_PROFILE" ]]; then
  echo "REFUSING: set AWS_PROFILE=medzen explicitly for local Terraform." >&2
  exit 2
fi

actual_account="$(aws sts get-caller-identity --query Account --output text)"
actual_caller="$(aws sts get-caller-identity --query Arn --output text)"

if [[ "$actual_account" != "$EXPECTED_ACCOUNT" ]]; then
  echo "REFUSING: Terraform caller account is $actual_account, expected $EXPECTED_ACCOUNT." >&2
  exit 2
fi

if [[ "$actual_caller" != "$EXPECTED_CALLER" ]]; then
  echo "REFUSING: Terraform caller is $actual_caller, expected $EXPECTED_CALLER." >&2
  exit 2
fi

exec terraform -chdir="$REPO_ROOT/infra" "$@"
