# MedZen serving-registry publication boundary — v1

## Purpose

The serving registry is published as an immutable, content-addressed snapshot
before a small serving pointer is changed. A snapshot write cannot make a model
live. Activation is a separate operation requiring a signed PASS gate report
and recorded manual approval.

## Data flow

1. Generated language records and their approval bindings are canonicalized.
2. Each language is stored as a KMS-encrypted SecureString under
   `/medzen/registry/snapshots/<snapshot-sha256>/languages/<alias>`.
3. A content-addressed manifest stores every language-value hash and binds the
   gate report, signed manifest, approval, source registry, generated tree and
   Git commit.
4. Every parameter is created with overwrite disabled and read back with
   decryption. A different value at an existing snapshot path refuses the run.
5. Only after the snapshot is complete may a separate approved operation update
   `/medzen/registry/serving/current`.
6. The previous pointer value and the new SSM parameter version are retained as
   rollback evidence. Parameter deletion is denied.

## Permission boundary

- `medzen-registry-publisher-role` is the only role granted
  `ssm:PutParameter` for `/medzen/registry/*`.
- The role is explicitly denied writes outside that prefix and denied all
  Parameter Store deletion.
- Orchestrator, LLM gateway and TTS gateway remain read-only.
- Trainer, builder, ASR runtime and model-loader receive no registry-write
  capability.
- SecureStrings reuse `alias/medzen-data`. KMS cryptographic operations are
  constrained to the SSM service and the exact registry parameter ARN prefix.
- The configured owner principal may assume the dedicated role but does not
  receive direct Parameter Store write permissions.

## Fail-closed rules

- Missing or malformed hashes, commit, signature verification, manual approval
  or PASS outcome refuse production snapshot construction.
- Empty snapshots, invalid language aliases, values above the standard
  Parameter Store 4 KiB limit and immutable collisions refuse publication.
- Snapshot publication never changes the serving pointer.
- Activation checks that the pointer still equals the owner-approved previous
  value immediately before writing and verifies readback immediately after.
- The current B5 BLOCKED artifact cannot satisfy this contract.

## Rollback

Use the recorded previous pointer value to create a new SSM version of
`/medzen/registry/serving/current`, then read it back and record the returned
version. Immutable snapshots are never rewritten or deleted.

## B6A separation

Zero-shot Whisper v0 is a platform test, not an approved language model. Its
temporary configuration must use a separately authorized non-production
namespace and must never update `/medzen/registry/serving/current` or any
language `approved_version`.

## Trade-off and revisit trigger

SSM Parameter Store does not provide compare-and-swap writes. Versioned
snapshots, a last-value check, one manually approved publisher and readback make
the current single-publisher workflow fail closed enough for this stage. If
publication becomes concurrent or automated from more than one pipeline,
introduce a DynamoDB conditional lock/transaction before allowing activation.
