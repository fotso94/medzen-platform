# MedZen speech orchestrator — B6.3 local file mode

This slice implements the public multipart file endpoint with local-only
dependencies. It authenticates a synthetic client key, assigns request and
session identities, resolves a content-addressed registry snapshot, performs
the pre-LLM emergency check, calls the local synthetic ASR/RAG/LLM adapters and
returns a cited text-only response.

The registry fixture uses SSM-shaped parameter records beneath
`/medzen/registry/test/b6/<snapshot-sha256>/`. B6.5A can replace the local
parameter-store adapter without changing routing or validation.

This service must not use real audio, clinical content, AWS, Bedrock, SSM or
network providers in B6.3. Streaming remains B6.4 and TTS remains B6.5.

```text
multipart client
  -> bearer auth -> content-addressed registry route -> synthetic ASR
  -> mandatory emergency check -> synthetic RAG -> fake LLM
  -> cited text-only response
```

Design choices and trade-offs:

- The adopted speech contract is not edited; citation fields live in an
  additive B6.3 contract. This preserves the adopted hash while giving the
  end-to-end reply an explicit evidence binding.
- The local parameter store is intentionally stricter than a loose config
  file: canonical values, an exact parameter set and both leaf and snapshot
  hashes must agree. This adds validation code now but makes the later SSM
  swap small and fail-closed.
- ASR accepts one generated non-speech checksum only. This cannot demonstrate
  speech quality, but it prevents local contract tests from being mistaken for
  a model evaluation or processing real audio.

Revisit for later phases: B6.4 adds streaming/session persistence and bounded
queues; B6.5 adds TTS; B6.5A supplies the SSM adapter and immutable test
snapshot; B6.6 replaces in-process dependency adapters with cluster clients.
