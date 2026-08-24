# LOCAL-CREDENTIAL LOCKDOWN RUNBOOK (Codex reviews #24-#25)

## Why the previous approach was theater
The first draft denied `sagemaker:CreateTrainingJob` unless the request
carried `medzen-tier=calibration`. The reviewer proved the obvious: the
CALLER supplies request tags, so a full-size job falsely tagged
"calibration" sailed through. Worse, the local identity inherits
`AdministratorAccess` and holds `IAMFullAccess` — it can create fresh
users, keys, roles and policy versions, so ANY policy attached to it is
self-removable. No file in this repository constrains this machine.

## The real design
Run everything below from a SEPARATE owner-controlled admin principal
(root or a dedicated break-glass admin the working machine has no keys
for). The working identity must end up unable to undo any of it.

1. **Strip admin**: detach `AdministratorAccess` and `IAMFullAccess`
   from the working identity (user/role this machine uses).
2. **Attach the permissions boundary** `medzen-local-boundary`
   (policy below). A boundary caps EVERY current and future permission,
   and only the admin principal can remove it.
3. **Replace broad service policies** with the scoped operational set
   the platform actually uses day-to-day (S3 medzen-speech, CloudWatch
   logs read, SageMaker Describe*/List*, ECR pull/push to the two
   repos, Cost Explorer read).

After this: local credentials cannot create ANY training job (all
launches — calibration and arm — go through workflows and their
scoped roles), cannot touch IAM, and cannot lift their own limits.
Calibration launches move to a non-protected workflow with a
calibration-scoped role (runtime-capped by policy conditions) — built
when this runbook is applied.

## medzen-local-boundary (permissions boundary policy)
```json
{
 "Version": "2012-10-17",
 "Statement": [
  {
   "Sid": "OperationalCeiling",
   "Effect": "Allow",
   "NotAction": [
    "iam:*",
    "organizations:*",
    "account:*",
    "sagemaker:CreateTrainingJob",
    "sagemaker:UpdateTrainingJob",
    "sagemaker:CreateEndpoint*",
    "sagemaker:CreateNotebookInstance",
    "ec2:RunInstances",
    "ec2:CreateFleet",
    "ec2:RequestSpotInstances"
   ],
   "Resource": "*"
  },
  {
   "Sid": "ReadOnlyIamIntrospection",
   "Effect": "Allow",
   "Action": ["iam:Get*", "iam:List*"],
   "Resource": "*"
  },
  {
   "Sid": "NoRemoteDebugEver",
   "Effect": "Deny",
   "Action": ["sagemaker:CreateTrainingJob", "sagemaker:UpdateTrainingJob"],
   "Resource": "*",
   "Condition": {"Bool": {"sagemaker:EnableRemoteDebug": "true"}}
  }
 ]
}
```

Codex review #24: `UpdateTrainingJob` joined the ceiling's NotAction (it can
toggle `EnableRemoteDebug` on a RUNNING job — shell access to the training
container), and the explicit `NoRemoteDebugEver` deny uses the SERVICE
condition key `sagemaker:EnableRemoteDebug` (request-body-derived, NOT a
caller-controlled tag — this one is not theater). The same deny now rides
`medzen-arm-launch-role.json` for the workflow caller.
(EC2 compute creation rides the existing reviewed EC2-stage packets —
if those remain local for now, remove the three ec2 lines and accept
the residual, stated; revisit at the next activation step.)

## Verification (owner, after applying)
```bash
aws sagemaker create-training-job --training-job-name probe-denied \
  --cli-input-json file://probe.json   # MUST fail AccessDenied
aws iam create-user --user-name probe  # MUST fail AccessDenied
aws iam delete-user-permissions-boundary --user-name <working-user> \
  # MUST fail AccessDenied
```
