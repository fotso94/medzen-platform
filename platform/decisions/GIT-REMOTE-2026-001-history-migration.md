# GIT-REMOTE-2026-001 — history migration authorization required

Status: `AWAITING_EXPLICIT_OWNER_APPROVAL`

## Why normal push cannot complete

GitHub rejected `master` because B1 commit `4037be6` added this generated
provider binary:

`infra/.terraform/providers/registry.terraform.io/hashicorp/aws/5.100.0/darwin_arm64/terraform-provider-aws_v5.100.0_x5`

Its Git blob is `7cdb6eb56e757ae73907892e99c771c1de6e1c1c`, with
`679,887,218` bytes. GitHub's per-file limit is 100 MB. The same historical
directory also contains local Terraform state. Removing these files in a new
commit protects the current tree but cannot remove their old Git objects.

Any normal full-history GitHub push therefore requires rewriting every
descendant commit ID. This includes the SHAs cited by B4/B5 evidence, so the
rewrite must not happen silently.

## Work completed without rewriting history

- Created private repository `https://github.com/fotso94/medzen-platform`.
- Configured it as local remote `origin`.
- Stopped tracking `.terraform/` and Terraform state in current commit
  `65d47ac`; local runtime files remain present and ignored.
- Pushed a parentless, byte-identical current-tree safety snapshot at
  `safety-snapshot-2026-08-03`, commit
  `faf35c561fe1438d080600c94da5282a32ec1ed7`.
- Verified the remote ref. The B5 engine, immutable BLOCKED report and MLflow
  attachment receipt are therefore off-device now.
- Kept the repository private. GitHub refused branch protection for a private
  repository on the current account plan; making it public is prohibited.

## Recommended migration — option A

If authorized, the migration will:

1. Create and verify a full pre-rewrite Git bundle and SHA-256 recovery record.
2. Record the old `master` head and all governance-referenced commits.
3. Rewrite local `master` to remove `infra/.terraform/**` from every historical
   commit while leaving all other paths and commit messages unchanged.
4. Verify that no blob at or above 100 MB remains and that no Terraform state
   remains anywhere in rewritten history.
5. Record an old-to-new commit mapping, including all B4/B5 cited SHAs.
6. Rerun the canonical tests and deterministic B5 refusal check.
7. Push sanitized `master` to the private repository, set it as the GitHub
   default branch and verify a fresh clone.
8. Retain the safety-snapshot branch. Branch protection remains unavailable
   until the GitHub account supports private-repository protections.

This changes local commit identities, but it is recoverable from the verified
pre-rewrite bundle and mapping record.

## Alternative — option B

Keep the local 139-commit graph unchanged and use the current GitHub safety
snapshot as the source backup. Store an encrypted exact Git bundle in a
separately owner-approved backup location. GitHub would not expose the original
history as normal commits.

## Explicit authorization phrase

To authorize the recommended migration, reply:

`Approve GIT-REMOTE-2026-001 option A and proceed with the recoverable history rewrite.`

Without that exact authorization, no history rewrite or force-push is
permitted.
