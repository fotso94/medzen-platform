"""Language-to-Whisper-token mapping. Neutral by design.

This lives apart from the trainer so that anything needing the mapping -- the
trainer, the label-length checker, audits, tests -- can import it without
importing the trainer, and without the trainer importing them back.

Whisper has a fixed set of language tokens. Six of our fourteen languages map
exactly. Seven have no Whisper token at all and train under the closest usable
one, which is a modelling compromise, not a detail: a wrong choice here is
indistinguishable from a training bug, and is the first thing to check if the
loss fails to descend. Pidgin's mapping is provisional from the B3 decode
experiment, decided on 44 clips with an interval that did not exclude zero.
"""
from __future__ import annotations

# exact: the language has its own Whisper token
EXACT = {
    "amharic": "am",
    "hausa": "ha",
    "lingala": "ln",
    "shona": "sn",
    "swahili": "sw",
    "yoruba": "yo",
}

# approximate: no Whisper token exists; trained under a regional neighbour
APPROXIMATE = {
    "acholi": "sw",
    "luganda": "sw",
    "fula": "sw",
    "oromo": "sw",
    "akan": "yo",
    "ewe": "yo",
    "igbo": "yo",
}

# provisional: chosen by experiment, not settled (B3, n=44, CI included zero)
PROVISIONAL = {
    "pidgin": "en",
}

LANG_TOKEN: dict[str, str] = {**EXACT, **APPROXIMATE, **PROVISIONAL}

assert len(LANG_TOKEN) == len(EXACT) + len(APPROXIMATE) + len(PROVISIONAL), \
    "a language appears in more than one category"
