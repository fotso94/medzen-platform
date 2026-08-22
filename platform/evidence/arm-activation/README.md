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

Activation order (Codex review #26: the workflows and trust target
master, so MERGE COMES FIRST):
1. Codex passes this packet -> merge the reviewed branch to master.
2. terraform plan -out=arm.tfplan -var arm_launch_enabled=true
   -var github_repo=fotso94/medzen-platform  (precondition REFUSES any
   other repo value); verify the saved plan JSON shows exactly the two
   arm resources; terraform apply arm.tfplan.
3. Set repo variable MEDZEN_ARM_LAUNCH_ROLE_ARN to the new role ARN.
4. Owner creates environment arm-launch-approval (self as required
   reviewer; deployment branches: master only).
5. Run ALL THREE canaries from master: arm-launch-canary (expect
   CANARY_ASSUMED_ROLE_OK), arm-launch-canary-unauthorized (missing
   claim -> refused), arm-launch-canary-wrongref (real-but-wrong
   job_workflow_ref -> refused).
6. Record the APPROVED review per the Codex verdict; regenerate the
   intent against the APPROVED bytes; dispatch arm-launch; owner
   clicks Approve -> $70-capped launch.
