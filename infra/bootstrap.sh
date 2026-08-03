#!/usr/bin/env bash
# Run ONCE, after approval. Creates the Terraform state bucket + lock table,
# then tells you to enable the S3 backend. Nothing else.
set -euo pipefail
PROFILE=medzen; REGION=eu-central-1; ACCT=558069890522
BUCKET="medzen-speech-tfstate-${ACCT}"; TABLE="medzen-speech-tflock"

aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" --profile "$PROFILE" \
  --create-bucket-configuration LocationConstraint="$REGION" 2>/dev/null \
  && echo "created $BUCKET" || echo "$BUCKET exists"
aws s3api put-bucket-versioning --bucket "$BUCKET" --profile "$PROFILE" \
  --versioning-configuration Status=Enabled
aws s3api put-public-access-block --bucket "$BUCKET" --profile "$PROFILE" \
  --public-access-block-configuration \
  "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"

aws dynamodb create-table --table-name "$TABLE" --profile "$PROFILE" --region "$REGION" \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST >/dev/null 2>&1 \
  && echo "created $TABLE" || echo "$TABLE exists"

echo; echo "Now uncomment the backend \"s3\" block in providers.tf and run:"
echo "  terraform init -migrate-state"
