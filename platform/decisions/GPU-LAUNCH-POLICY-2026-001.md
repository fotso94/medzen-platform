# GPU-LAUNCH-POLICY-2026-001 (Codex reviews #16-#17, finding 4)

Every ephemeral GPU/CPU box launched for dev sweeps, sentinels, smokes or
calibration MUST use an ENCRYPTED root EBS volume bound to the MedZen KMS
key. The round-15 re-smoke used an unencrypted 80 GiB volume — a violation
of the project's KMS-at-rest posture (the volume was deleted on terminate,
but the launch was non-compliant).

## Requirement

The launch's root `Ebs` block MUST set:

- `Encrypted = true`
- `KmsKeyId = arn:aws:kms:eu-central-1:558069890522:key/9c336116-c648-4548-95c6-1b926478ae57`
- `DeleteOnTermination = true`
- `VolumeType = gp3`

## How launches happen (Codex review #17)

There is deliberately NO generic launch wrapper in this repo. A round-16
helper that took arbitrary user-data under the trainer instance profile
was a sealed-evaluation bypass and has been removed. Compute is created
ONLY through the governed, packet-gated EC2 stage adapter
(`pipeline/ec2_stage_adapter.py`) — whose own budget/account/idempotency
controls and the architecture-control tripwire in
`tests/test_arch_2026_001.py` gate every launch path. Sealed evaluation
additionally requires the spec-built evaluator
(`SEALED-EVALUATOR-SPEC-2026-001`), never an ad-hoc box.

Any future dev box (e.g. Arm-2 calibration) is launched through that
governed path with the encrypted `Ebs` block above; adding a new launch
capability anywhere else fails the architecture tripwire by construction.
