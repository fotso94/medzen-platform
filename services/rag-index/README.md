# MedZen RAG index — B6.1 local slice

This service is a deterministic, read-only implementation of the canonical
`MEDZEN-SPEECH-CONTRACT-2026-001` internal retrieval boundary.

Current scope:

- local execution only;
- synthetic, explicitly non-clinical content only;
- checksum-bound manifest and alias loading;
- deterministic lexical ranking and citation metadata;
- health/readiness and fail-closed empty/tampered-index behavior; and
- alias switch and rollback tests.

It does **not** approve clinical content, write S3, create an AWS resource,
publish an image or authorize deployment. A clinical owner decision is required
before replacing the synthetic fixture. B6.6 deployment remains separately
packet-controlled.

Run the focused suite from the repository root:

```sh
.venv/bin/pytest -q tests/test_b6_contract_adoption.py tests/test_rag_index.py
```
