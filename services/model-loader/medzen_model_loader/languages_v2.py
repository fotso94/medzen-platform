"""B6v2 canonical serving-language identities (Codex round 5).

Round 4 shipped three DIFFERENT language keys with no translation:
the registry routes speak wire codes ("en", "kin"), the manifest and
loader speak aliases ("english", "kinyarwanda"), and the OmniASR
pipeline speaks omnilingual ids ("eng_Latn", "kin_Latn") — a real
request refused before inference ('en' is not served by this artifact).

This module is the ONE canonical map. It is embedded as a constant so
the loader container needs no repo checkout; two committed sources pin
it and a test refuses drift:
  - registry/languages/<alias>.yaml        -> iso_code (the wire code)
  - services/asr-eval-runtime/assets/language-conditioning-v1.json
    -> meta_llm (the omnilingual id the T6 gate actually conditioned on)
"""
from __future__ import annotations

# alias -> (wire iso code, omnilingual language id)
SERVING_LANGUAGES_V1: dict[str, tuple[str, str]] = {
    "english": ("en", "eng_Latn"),
    "ewe": ("ewe", "ewe_Latn"),
    "french": ("fr", "fra_Latn"),
    "kinyarwanda": ("kin", "kin_Latn"),
    "lingala": ("lin", "lin_Latn"),
    "pidgin": ("pcm", "pcm_Latn"),
    "swahili": ("swa", "swh_Latn"),
}


def canonical_language_ids() -> dict[str, str]:
    """The alias -> omnilingual id map a v2 manifest must declare."""
    return {alias: omni for alias, (_, omni) in SERVING_LANGUAGES_V1.items()}


def marker_language_ids() -> dict[str, str]:
    """The lookup table the serving marker carries: BOTH the alias and
    the wire iso code resolve to the omnilingual id, so the hint the
    orchestrator actually sends ("en", "kin") routes correctly."""
    table: dict[str, str] = {}
    for alias, (iso, omni) in SERVING_LANGUAGES_V1.items():
        table[alias] = omni
        table[iso] = omni
    return table
