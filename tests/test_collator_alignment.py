"""Behavioural tests for label alignment. These run the real collator.

The previous tests for this defect asserted on source strings and hard-coded
token ids. They proved a bug existed; they could not have proved a fix works,
and a green suite built from them would have meant nothing. These invoke
`collate()` on real batches, then hand the result to the same
`shift_tokens_right` HuggingFace uses internally, and assert on the tensors
that come out.

Offline by default via a faithful tokenizer double. The double is checked
against the real pinned tokenizer in `test_double_matches_the_real_tokenizer`,
which skips without AWS access -- so the double cannot quietly drift into
agreeing with a wrong implementation.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

torch = pytest.importorskip("torch")
from transformers.models.whisper.modeling_whisper import (  # noqa: E402
    shift_tokens_right)

from pipeline.label_length import SOT, decoder_start_id  # noqa: E402
from pipeline.train_asr import collate  # noqa: E402

# Real Whisper large-v3 ids.
SOT_ID = 50258          # <|startoftranscript|>
EOT_ID = 50257          # <|endoftext|>  -- and whisper's bos_token_id
EN, YO, TRANSCRIBE, NOTS = 50259, 50325, 50360, 50364
PAD = EOT_ID


class FakeTokenizer:
    """Faithful stand-in: same prefix layout, same bos/eos confusion."""

    bos_token_id = EOT_ID          # the trap, reproduced exactly
    eos_token_id = EOT_ID
    pad_token_id = PAD

    def __init__(self):
        self.lang = EN

    def convert_tokens_to_ids(self, tok):
        return {SOT: SOT_ID, "<|endoftext|>": EOT_ID}.get(tok)

    def set_prefix_tokens(self, language=None, task=None):
        self.lang = {"en": EN, "yo": YO}.get(language, EN)

    def encode(self, text):
        return [SOT_ID, self.lang, TRANSCRIBE, NOTS] + \
               [1000 + i for i, _ in enumerate(text.split())] + [EOT_ID]

    def __call__(self, text):
        # label_lengths() calls the tokenizer directly and reads .input_ids,
        # the same as the real one.
        from transformers import BatchEncoding
        return BatchEncoding({"input_ids": self.encode(text)})

    def pad(self, features, return_tensors=None):
        # BatchEncoding, not a dict: the collator reads `.attention_mask` as an
        # attribute, which is what the real tokenizer returns. A dict here would
        # make the double diverge from production at the first line of use.
        from transformers import BatchEncoding
        seqs = [f["input_ids"] for f in features]
        n = max(len(s) for s in seqs)
        ids = torch.tensor([s + [PAD] * (n - len(s)) for s in seqs])
        att = torch.tensor([[1] * len(s) + [0] * (n - len(s)) for s in seqs])
        return BatchEncoding({"input_ids": ids, "attention_mask": att})


class FakeProcessor:
    def __init__(self):
        self.tokenizer = FakeTokenizer()


def batch_of(*specs):
    """specs: (language, n_words). Returns collator-shaped batch items."""
    tk = FakeTokenizer()
    out = []
    for lang, n in specs:
        tk.set_prefix_tokens(language=lang, task="transcribe")
        out.append({"input_features": torch.zeros(2, 4),
                    "labels": tk.encode(" ".join(["w"] * n))})
    return out


def run_collate(batch, start=SOT_ID):
    return collate(FakeProcessor(), decoder_start_token_id=start)(batch)


# --------------------------------------------------------------------------- #
# the corrected behaviour
# --------------------------------------------------------------------------- #
def test_labels_begin_with_the_language_token_after_collation():
    """Exactly one SOT removed -- so the first target is the language token."""
    out = run_collate(batch_of(("en", 3)))
    assert out["labels"][0, 0].item() == EN
    assert SOT_ID not in out["labels"].tolist()[0], "no SOT should remain"


def test_language_task_and_notimestamps_survive_as_targets():
    """Only SOT is stripped. These three are legitimate Whisper targets, not
    prefix decoration -- an earlier draft of the RCA wrongly called them
    content the model should never have been trained to emit."""
    lab = run_collate(batch_of(("en", 3)))["labels"][0].tolist()
    assert lab[:3] == [EN, TRANSCRIBE, NOTS]


def test_eos_survives_collation():
    """EOS is what teaches the model to stop. Losing it is precisely the
    failure mode under investigation."""
    lab = run_collate(batch_of(("en", 3)))["labels"][0].tolist()
    real = [t for t in lab if t != -100]
    assert real[-1] == EOT_ID


def test_exactly_one_sot_in_the_decoder_inputs():
    """The actual defect: HF re-adds SOT, so an unstripped SOT produced two.
    This calls HuggingFace's own preparation rather than imitating it."""
    labels = run_collate(batch_of(("en", 4)))["labels"]
    dec = shift_tokens_right(labels, pad_token_id=PAD,
                             decoder_start_token_id=SOT_ID)
    row = dec[0].tolist()
    assert row[0] == SOT_ID
    assert row[1] != SOT_ID, "a second SOT is the bug that invalidated 23868bab"
    assert row.count(SOT_ID) == 1
    # and the model now predicts the language token from a single SOT
    assert labels[0, 0].item() == EN


def test_decoder_input_and_labels_stay_aligned():
    """dec[i] predicts labels[i]. With the bug every text target sat one
    position later than it does at inference."""
    labels = run_collate(batch_of(("en", 5)))["labels"]
    dec = shift_tokens_right(labels, PAD, SOT_ID)
    assert dec.shape == labels.shape
    assert dec[0, 1:].tolist() == labels[0, :-1].tolist()


def test_mixed_language_batch_keeps_each_row_its_own_language():
    out = run_collate(batch_of(("en", 3), ("yo", 6), ("en", 2)))
    assert [row[0] for row in out["labels"].tolist()] == [EN, YO, EN]


def test_padding_is_masked_not_trained_on():
    lab = run_collate(batch_of(("en", 2), ("en", 9)))["labels"]
    assert (lab[0] == -100).any(), "short row must be padded and masked"
    assert not (lab[1] == -100).any(), "longest row needs no padding"


# --------------------------------------------------------------------------- #
# fails closed
# --------------------------------------------------------------------------- #
def test_row_without_the_start_token_is_refused():
    """Guessing would reintroduce the silent mismatch that caused the failure."""
    batch = batch_of(("en", 3))
    batch[0]["labels"] = batch[0]["labels"][1:]        # SOT already missing
    with pytest.raises(ValueError, match="do not begin with"):
        run_collate(batch)


def test_inconsistent_batch_is_refused_not_partially_stripped():
    good, bad = batch_of(("en", 3), ("en", 4))
    bad["labels"] = bad["labels"][1:]
    with pytest.raises(ValueError, match="decoder_start_token_id"):
        run_collate([good, bad])


def test_wrong_decoder_start_id_is_refused():
    """Passing bos_token_id -- the original mistake -- must now fail loudly
    instead of silently stripping nothing."""
    with pytest.raises(ValueError, match="do not begin with"):
        run_collate(batch_of(("en", 3)), start=EOT_ID)


# --------------------------------------------------------------------------- #
# the id is resolved by identity, and cross-checked against the model
# --------------------------------------------------------------------------- #
def test_decoder_start_id_resolves_by_name_not_bos():
    tk = FakeTokenizer()
    assert decoder_start_id(tk) == SOT_ID
    assert tk.bos_token_id == EOT_ID != SOT_ID


def test_decoder_start_id_refuses_a_model_that_disagrees():
    class Cfg:
        decoder_start_token_id = 12345
    with pytest.raises(ValueError, match="mismatch"):
        decoder_start_id(FakeTokenizer(), Cfg())


def test_label_lengths_now_actually_subtracts_the_prefix():
    """Before the fix `effective` silently equalled `raw` for every row ever
    measured, because it too compared against bos_token_id."""
    from pipeline.label_length import label_lengths
    raw, eff = label_lengths(FakeTokenizer(), "one two three", "acholi")
    assert eff == raw - 1


# --------------------------------------------------------------------------- #
# the double must match reality
# --------------------------------------------------------------------------- #
# net as well as slow: it downloads the pinned tokenizer from S3.
@pytest.mark.slow
@pytest.mark.net
def test_double_matches_the_real_tokenizer():
    """Guards against the double drifting into agreement with a wrong impl."""
    boto3 = pytest.importorskip("boto3")
    botocore = pytest.importorskip("botocore")
    try:
        cli = boto3.Session(profile_name="medzen",
                            region_name="eu-central-1").client("s3")
        from scripts.audit_label_lengths import pinned_tokenizer
        real, _ = pinned_tokenizer(cli)
    except Exception as e:
        if isinstance(e, (botocore.exceptions.NoCredentialsError,
                          botocore.exceptions.ProfileNotFound,
                          botocore.exceptions.ClientError)):
            pytest.skip("no AWS access in this environment")
        raise

    assert real.convert_tokens_to_ids(SOT) == SOT_ID
    assert real.bos_token_id == EOT_ID
    assert real.eos_token_id == EOT_ID
    real.set_prefix_tokens(language="en", task="transcribe")
    ids = real("two words").input_ids
    assert ids[:4] == [SOT_ID, EN, TRANSCRIBE, NOTS]
    assert ids[-1] == EOT_ID
