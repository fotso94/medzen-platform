"""Immutable model and decode identities for the bounded pilot."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Candidate:
    name: str
    family: str
    model_sha256: str
    unconditioned: bool
    conditioned: bool


CANDIDATES = {
    "whisper-large-v3": Candidate(
        name="whisper-large-v3",
        family="whisper_ct2",
        model_sha256="5adf77568813513bc3697a1501ba354c04c7b93ea374fc5407cf4f6402f7431e",
        unconditioned=True,
        conditioned=True,
    ),
    "omniASR_CTC_1B_v2": Candidate(
        name="omniASR_CTC_1B_v2",
        family="meta_ctc",
        model_sha256="354f981756aa8f41591ea363e45b9c4eba1ec5144c2273af82e747efbb08919c",
        unconditioned=True,
        conditioned=False,
    ),
    "omniASR_LLM_1B_v2": Candidate(
        name="omniASR_LLM_1B_v2",
        family="meta_llm",
        model_sha256="cceb4d9ebac3d168a6af6b26c62ce11bafc562b38976c6bfa87e7d60422c6da5",
        unconditioned=True,
        conditioned=True,
    ),
}

SOURCE_IDENTITY = {
    "omnilingual_source_commit": "145a12a668aace6c1d0d290128c1225571fc1955",
    "omnilingual_internal_version": "0.1.0",
    "omnilingual_release_tag": "0.2.0",
    "tokenizer_sha256": "8aa11a1092142ef472537476ef6e76541123e2f0d789b79f3ebd119008240b1e",
    "whisper_revision": "06f233fe06e710322aca913c1bc4249a0d71fce1",
}
