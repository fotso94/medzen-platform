#!/bin/bash
# Reusable ENCRYPTED-EBS GPU launcher (GPU-LAUNCH-POLICY-2026-001).
# Usage: launch-gpu-box.sh <name> <userdata-file> [volume_gb]
set -euo pipefail
NAME="$1"; UD="$2"; VOL="${3:-80}"
KMS=arn:aws:kms:eu-central-1:558069890522:key/9c336116-c648-4548-95c6-1b926478ae57
aws ec2 run-instances --region eu-central-1 \
  --image-id ami-0e307bca04fbd2d80 --instance-type g6.xlarge \
  --iam-instance-profile Name=medzen-trainer-profile \
  --subnet-id subnet-01fb2fc3f56bce55e \
  --security-group-ids sg-0fee72d218ac002a7 sg-01adf16d45f0a5820 \
  --associate-public-ip-address \
  --metadata-options HttpTokens=required,HttpPutResponseHopLimit=2 \
  --instance-initiated-shutdown-behavior terminate \
  --block-device-mappings "DeviceName=/dev/xvda,Ebs={VolumeSize=${VOL},VolumeType=gp3,DeleteOnTermination=true,Encrypted=true,KmsKeyId=${KMS}}" \
  --user-data "file://${UD}" \
  --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=${NAME}},{Key=project,Value=medzen-b5}]" \
  --query 'Instances[0].InstanceId' --output text
