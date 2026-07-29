"""B2.4 — per-language text normalisation, versioned.

Two forms travel with every record (A3):
  text_verbatim    as spoken/transcribed. NEVER normalised into English.
  text_normalized  canonical form used for WER scoring, search and dedup.

`normalization_version` is stored per row because changing a normaliser
invalidates every cached score computed under the old one. Bump the version
when you change behaviour; never edit a version in place.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Callable

_PUNCT = re.compile(r"[^\w\s'̀-ͯ-]", flags=re.UNICODE)
_WS = re.compile(r"\s+")


def _base(text: str) -> str:
    """Shared floor: NFC, lowercase, strip punctuation, collapse whitespace."""
    t = unicodedata.normalize("NFC", text or "").lower()
    t = _PUNCT.sub(" ", t)
    return _WS.sub(" ", t).strip()


def strip_tones(text: str) -> str:
    """Remove combining diacritics. For tonal languages, report WER both with
    and without: it separates recognition errors from tone-marking errors."""
    d = unicodedata.normalize("NFD", text)
    return unicodedata.normalize("NFC", "".join(c for c in d if not unicodedata.combining(c)))


# --------------------------------------------------------------------------- #
# Pidgin: no standard orthography, so a variant table maps spellings to one
# canonical form. SEED ONLY — a native speaker must extend this. Scoring
# against an incomplete table inflates WER with spelling disagreement, which
# is exactly the failure A3 warns about.
# --------------------------------------------------------------------------- #
# Only UNAMBIGUOUS spelling variants belong here. A mapping that is wrong
# half the time corrupts WER worse than no mapping at all.
#
# DELIBERATELY EXCLUDED, needs a native speaker to disambiguate in context:
#   de   -> dey (continuous marker) OR the (article)
#   wan  -> want (verb)             OR one (numeral)
#   shop -> chop (eat)              OR shop (store)
#   sef  -> self (emphatic)         OR safe
# Until context-sensitive rules exist, these stay unnormalised in both forms.
PIDGIN_VARIANTS: dict[str, str] = {
    "dei": "dey", "dae": "dey", "dey": "dey",
    "weting": "wetin", "wettin": "wetin", "wetin": "wetin",
    "nah": "na", "naa": "na",
    "nor": "no", "noo": "no",
    "abek": "abeg", "abeq": "abeg",
    "pickin": "pikin", "picin": "pikin", "pikkin": "pikin",
    "komot": "comot", "kommot": "comot",
    "dohn": "don",
    "mek": "make", "meik": "make",
    "unu": "una", "unnu": "una",
    "dhem": "dem",
    "wahalla": "wahala", "wahla": "wahala",
    "savvy": "sabi",
    "sotai": "sotey", "sotay": "sotey",
}


def _pidgin(text: str) -> str:
    toks = _base(text).split()
    return " ".join(PIDGIN_VARIANTS.get(t, t) for t in toks)


def _tonal(text: str) -> str:
    """Keep diacritics (they are phonemic) but guarantee NFC composition —
    the same mark composed two ways otherwise scores as a substitution."""
    return _base(text)


@dataclass(frozen=True)
class Normalizer:
    version: str
    fn: Callable[[str], str]
    tonal: bool = False

    def __call__(self, text: str) -> str:
        return self.fn(text)


REGISTRY: dict[str, Normalizer] = {
    "pidgin":  Normalizer("pidgin-norm-v1", _pidgin),
    "english": Normalizer("generic-norm-v1", _base),
    "french":  Normalizer("generic-norm-v1", _base),
    "swahili": Normalizer("generic-norm-v1", _base),
    "hausa":   Normalizer("generic-norm-v1", _base),
    "amharic": Normalizer("generic-norm-v1", _base),
    "oromo":   Normalizer("generic-norm-v1", _base),
    "lingala": Normalizer("generic-norm-v1", _base),
    "shona":   Normalizer("generic-norm-v1", _base),
    "wolof":   Normalizer("generic-norm-v1", _base),
    "fula":    Normalizer("generic-norm-v1", _base),
    "luganda": Normalizer("generic-norm-v1", _base),
    "acholi":  Normalizer("generic-norm-v1", _base),
    # tonal with phonemic diacritics
    "igbo":    Normalizer("tonal-norm-v1", _tonal, tonal=True),
    "yoruba":  Normalizer("tonal-norm-v1", _tonal, tonal=True),
    "akan":    Normalizer("tonal-norm-v1", _tonal, tonal=True),
    "ewe":     Normalizer("tonal-norm-v1", _tonal, tonal=True),
}

DEFAULT = Normalizer("generic-norm-v1", _base)


def for_language(alias: str) -> Normalizer:
    return REGISTRY.get(alias, DEFAULT)
