# LIC-2026-002 — ShareAlike-on-weights legal review: CLEARED

Status: APPROVED (owner-attested legal sign-off)

## Decision

Training on CC-BY-SA (`sharealike_review`) licensed audio is CLEARED for
MedZen model training. The owner attests (2026-08-18, recorded in the
shared coordination file) that legal counsel reviewed and SIGNED the
ShareAlike-on-weights analysis contemplated by Base v5 §B9. The signed
document is retained PRIVATELY by the owner and is deliberately not
committed or uploaded; this record carries the owner's attestation as
the platform's authority, per the owner's explicit instruction.

## Effect (the T3 doctrine: a green review widens ONE reviewed list)

- `pipeline/licence_filter.py` moves `sharealike_review` from
  LEGAL_REVIEW_PENDING into the attribution-class trainable set (a
  ShareAlike obligation is an attribution-plus-share-alike obligation:
  flagged on every mix that uses it, mirrored into the model manifest).
- Unlocked immediately: amharic, luganda, oromo, shona training corpora
  (~130k rows) and fula's 32,919 previously-gated rows.
- acholi remains untrainable for a DIFFERENT reason: no clean
  evaluation set exists (v1 overlaps the training zone); it needs fresh
  disjoint eval data before any gate could judge a model.

## Obligations carried forward

Every training mix consuming sharealike rows records
`sharealike_obligation: true` in its provenance, and the exported model
manifest must carry the obligation notice — the licence follows the
weights.
