# GPU-LAUNCH-POLICY-2026-001 (Codex review #16 finding 4)

Every ephemeral GPU/CPU box launched for dev sweeps, sentinels or smokes
MUST use an ENCRYPTED root EBS volume bound to the MedZen KMS key. The
round-15 re-smoke used an unencrypted 80 GiB volume — a violation of the
project's KMS-at-rest posture (the volume was deleted on terminate, but
the launch was non-compliant).

MANDATORY `run-instances` block-device-mapping:

```
--block-device-mappings 'DeviceName=/dev/xvda,Ebs={VolumeSize=<N>,VolumeType=gp3,DeleteOnTermination=true,Encrypted=true,KmsKeyId=arn:aws:kms:eu-central-1:558069890522:key/9c336116-c648-4548-95c6-1b926478ae57}'
```

Use `scripts/sweeps/launch-gpu-box.sh` (below) which encodes this. Any
launch without `Encrypted=true` is out of policy.
