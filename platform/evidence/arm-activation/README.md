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

COST LANGUAGE, precisely (Codex review #27): $70 is the PROCESS-
enforced ceiling (launcher refusal + the byte-exact reviewed request at
1 instance / $64 calculated max). The AWS-HARD limits are: instance
type ml.g6.xlarge only, runtime <= 144,000s, the exact job name, and
the campaign KMS key. IAM cannot constrain InstanceCount; the account
quota itself allows two ml.g6.xlarge — the single-instance bound lives
in the reviewed request, not in IAM.

Activation order (Codex review #27: GitHub AUTO-CREATES a referenced
missing environment WITHOUT protection rules — so the protected
environment must exist BEFORE anything can reference it):
1. Codex passes this packet.
2. Owner (or implementer via owner session) creates environment
   arm-launch-approval FIRST: required reviewer = fotso94, deployment
   branches = master only. Verify it shows the protection rules.
3. Merge the reviewed branch to master.
4. terraform plan -out=arm.tfplan -var arm_launch_enabled=true
   -var github_repo=fotso94/medzen-platform (immutable id form is a
   defaulted variable, precondition-checked); verify the saved plan
   JSON = exactly the two arm creates; terraform apply arm.tfplan.
5. Set repo variable MEDZEN_ARM_LAUNCH_ROLE_ARN to the role ARN.
6. Run ALL THREE canaries from master: arm-launch-canary (must print
   CANARY_ASSUMED_ROLE_OK with the exact role ARN asserted),
   arm-launch-canary-unauthorized and arm-launch-canary-wrongref (each
   must print its explicit-AccessDenied OK line; a missing variable
   FAILS rather than skips).
7. Record the APPROVED review per the Codex verdict; regenerate the
   intent against the APPROVED bytes; dispatch arm-launch; owner
   clicks Approve -> launch (process ceiling $70, calculated max $64).
