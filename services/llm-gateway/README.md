# MedZen LLM gateway — B6.2 local/mocked slice

This service implements the internal `MEDZEN-LLM-CONTRACT-2026-001` boundary
with a deterministic fake Bedrock provider.

It loads each language's generated registry reference and resolves it through
`registry/llm-policies/v1.yaml`. Responses require RAG citations, bind their
exact identities and content hashes, and fail closed when the provider changes
or invents a citation. Provider timeouts and failures feed the shared circuit
breaker; an open breaker prevents another provider invocation and makes
readiness fail.

There is deliberately no boto3 dependency or real Bedrock adapter. A real
invocation requires a separate versioned AWS packet with request, input-token,
output-token and cost caps.

Focused local suite:

```sh
.venv/bin/pytest -q tests/test_llm_contract.py tests/test_llm_gateway.py
```
