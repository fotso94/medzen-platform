# ARM-LAUNCH ACTIVATION EVIDENCE (owner fast-path packet)
Owner directive: dev environment, speed first; key rotation, public-repo
cleanup and the local credential boundary are owner-accepted risks.

- terraform-plan.txt — `Plan: 2 to add, 0 to change, 0 to destroy`
  (arm role + its policy ONLY; legacy B7 CI resources now behind their
  own b7_ci_enabled flag, default false; the EXISTING OIDC provider is
  a data source, never re-created)
- access-analyzer-role-policy.json — ZERO findings. Conditions:
  mandatory RequestTag medzen-tier=arm (no IfExists), ForAllValues
  InstanceTypes=ml.g6.xlarge / VpcSubnets / VpcSecurityGroupIds,
  OutputKmsKey = the campaign key ARN, MaxRuntimeInSeconds <= 144000.
- trust-policy.json + access-analyzer-trust-policy.json — validated as
  AWS::IAM::AssumeRolePolicyDocument: ZERO errors. Remaining items and
  dispositions: SUGGESTION CONFIRM_AUDIENCE_CLAIM_TYPE (aud is the
  exact standard value sts.amazonaws.com); WARNING
  SPECIFIC_GITHUB_REPO_AND_BRANCH_RECOMMENDED (branch specificity is
  supplied by job_workflow_ref@refs/heads/master; the sub uses the
  environment form required for environment-protected jobs).
- Workflows: arm-launch.yml (protected caller, no creds, no inputs) ->
  arm-launch-exec.yml (REUSABLE credential-bearer; the job_workflow_ref
  target; mode input used only in `if:`) with arm-launch-canary.yml
  (positive STS probe through the exact production chain, non-mutating)
  and arm-launch-canary-unauthorized.yml (same environment, wrong
  workflow -> assumption MUST fail; the job passes only if creds fail).
  actionlint: all four PASS.

Activation order (after Codex pass): terraform apply -var
arm_launch_enabled=true -var github_repo=fotso94/medzen-platform ->
set MEDZEN_ARM_LAUNCH_ROLE_ARN repo variable -> owner creates
environment arm-launch-approval (self as required reviewer, master-only
branches) -> run BOTH canaries (expect positive OK + negative refused)
-> merge to master -> flip review to APPROVED per Codex verdict ->
dispatch arm-launch -> owner clicks Approve -> $70-capped launch.
