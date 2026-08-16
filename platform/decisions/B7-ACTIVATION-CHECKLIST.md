# B7 activation checklist

Everything in `.github/workflows/` ships dark: pipelines test, build and
scan with zero credentials today. Activation is one small reviewed packet
plus GitHub settings, all listed here so nothing activates by accident.

1. **OIDC + CI role packet (AWS)**: set `var.github_repo` to the real
   owner/repo; create the GitHub OIDC provider and a `medzen-ci-role`
   whose policy allows exactly: ECR push to the five service repos, EKS
   describe + the medzen-namespace deployment patch, terraform state
   read/write, and the non-production registry alias parameter — with the
   same explicit-Deny posture as the trainer role (no eval-evidence
   writes, no `/medzen/registry/serving/current`, no secrets, no Bedrock).
2. **Repository variable**: set `MEDZEN_CI_ROLE_ARN` — this single value
   turns the dark jobs on.
3. **`infrastructure` environment**: create it with required reviewers
   (the owner). It gates terraform apply AND the RAG alias flip.
4. **Branch protection on `main`** + CODEOWNERS with the real clinical
   owner's handle on `platform/testdata/rag-index/**` (and the future
   clinical content path), with "require review from code owners" on.
5. **Two explicit fail-closed placeholders to replace, each with its own
   reviewed change**:
   - model-pipeline `open-registry-pr`: the approved_version bump lands
     with B5 reactivation (promotion schema for the decode-approvals
     input of `scripts/generate_languages.py`);
   - content-rag-pipeline `alias-flip`: bind the exact non-production
     alias parameter under the B6.5A create-only discipline.
6. **Rollback drill** (B8): after activation, one real alias-restore
   drill before any production traffic, receipts committed.
