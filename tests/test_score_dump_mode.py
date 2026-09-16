"""Default-off per-frame probability dump in the protected evaluator
(decoder diagnostic, 2026-09-15).

Three things must hold before this option can be trusted:

 (i)   with the variable unset the evaluator is what it was — the per-row
       decode region and the receipts construction/write region are
       byte-identical to the pre-change file, and the ONLY new statements in
       main() that are not behind a dump guard are two assignments;
 (ii)  an unrecognised MEDZEN_SCORE_DUMP_MODE refuses rather than falling back
       (including 'full', which is deliberately not implemented: dev alone
       would be 5.64 GB, so it needs its own decision, not a typo);
 (iii) greedy decoding recomputed from the dump ALONE reproduces the
       transcript the scorer produced, including frames whose argmax lies
       OUTSIDE the kept set — the case a kept-set-only dump cannot reproduce.

The recompute tests drive the REAL frozen decoder
(pipeline.omniasr_calibrate._ctc_greedy_text) through a numpy-backed torch
double, so they run on CI runners and engineering hosts that carry no torch;
the same assertions run again against real torch wherever it is installed.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipeline.omniasr_score import (DUMP_DEST_ROOTS, DUMP_MODES,  # noqa: E402
                                    EvaluatorRefusal, _DumpWriter, _open_dump,
                                    dump_frame_dtype, encode_frames,
                                    greedy_from_dump, parse_kept_set,
                                    reconstruct_dense, resolve_dump_dest,
                                    resolve_dump_mode, resolve_dump_topk)

KEY = "MEDZEN_SCORE_DUMP_MODE"
SRC = (ROOT / "pipeline/omniasr_score.py").read_text()

# ---- (i) the default path is the pre-change evaluator ----------------------
# Regions captured VERBATIM from the file this option was added to (whole-file
# sha256 7d2ad3faed36b6e24753454c7f9b22173bd5533b32021f8f706fddb50dbafc49).
# The dump only ever APPENDS statements after these regions; one byte of drift
# inside either of them changes what the frozen decode contract does or what
# the attested receipts bytes are, and fails here.
GOLDEN_DECODE_REGION_SHA256 = (
    "d1cc2ca13381d5c255457720634296f0ec48cd8243c9fbf67746e44551acab44")
GOLDEN_RECEIPTS_REGION_SHA256 = (
    "ad6f9e1bf5a152e9e8b514994068b7f250edec7f7f4303652eb2980d41f6d17e")
DECODE_START = "    for row in rows:\n"
DECODE_END = '                         "hyp_normalized": hyp})'
RECEIPTS_START = "    receipts = {\n"
RECEIPTS_END = "                     sort_keys=True))"
# the only statements in main() that mention the dump and are NOT behind a
# guard; both are inert when the variable is unset
ALLOWED_UNGUARDED = {"dump_mode = resolve_dump_mode(os.environ)", "dump = None"}

# ---- synthetic decoding fixtures ------------------------------------------
VOCAB_SIZE = 40
BLANK = 1
KEPT = [1, 5, 6, 7]                      # blank + three in-set symbols
TOPK = 3
# frame-by-frame argmax plan: ids 33 (frames 1 and 7) are OUTSIDE the kept set
ARGMAX_PLAN = [5, 33, 1, 6, 6, 1, 7, 33]


def _render(ids) -> str:
    """Stand-in for tokenizer.create_decoder(skip_special_tokens=True); every
    id renders to a DISTINCT string so an id-level difference cannot hide."""
    return "".join(f"[{int(i)}]" for i in ids.tolist())


def _region(start: str, end: str) -> str:
    i = SRC.index(start)
    return SRC[i:SRC.index(end, i) + len(end)]


def _logits():
    np = pytest.importorskip("numpy")
    rng = np.random.default_rng(11)
    wide = rng.uniform(-9.0, -1.0, size=(len(ARGMAX_PLAN),
                                         VOCAB_SIZE)).astype("float32")
    for frame, token in enumerate(ARGMAX_PLAN):
        wide[frame, token] = 5.0 + frame * 0.25      # a strictly unique max
    return wide


@pytest.fixture
def fake_torch(monkeypatch):
    """Install a numpy-backed double for the three torch operations
    _ctc_greedy_text performs, so the FROZEN decoder itself runs here."""
    np = pytest.importorskip("numpy")

    class _T:
        def __init__(self, array):
            self.a = np.asarray(array)

        def __getitem__(self, key):
            return _T(self.a[key])

        def argmax(self, dim=None):
            return _T(self.a.argmax(axis=dim))

        def tolist(self):
            return self.a.tolist()

    module = types.ModuleType("torch")
    module.as_tensor = lambda x, dtype=None: _T(x.a if isinstance(x, _T) else x)
    module.int64 = "int64"
    monkeypatch.setitem(sys.modules, "torch", module)
    return module


def _frozen_text(wide, valid=None):
    """What the scorer itself would emit for these logits."""
    from pipeline.omniasr_calibrate import _ctc_greedy_text
    return _ctc_greedy_text(wide, _render, BLANK,
                            valid_frames=len(wide) if valid is None else valid)


class _FakeLogits:
    """The model's [T, vocab] tensor, restricted to exactly the operations
    _DumpWriter.write_row performs on it."""

    def __init__(self, array, dtype="torch.bfloat16"):
        import numpy as np
        self._a = np.asarray(array)
        self._dtype = dtype

    @property
    def dtype(self):
        return self._dtype

    def __getitem__(self, key):
        return _FakeLogits(self._a[key], self._dtype)

    def argmax(self, dim=None):
        return _FakeLogits(self._a.argmax(axis=dim), self._dtype)

    def float(self):
        return self

    def detach(self):
        return self

    def cpu(self):
        return self

    def numpy(self):
        return self._a


class _FakeS3:
    def __init__(self, body: bytes):
        self._body = body

    def get_object(self, Bucket, Key, VersionId):        # noqa: N803
        return {"Body": types.SimpleNamespace(read=lambda: self._body)}


def _artifact(**overrides) -> bytes:
    doc = {"record": "CM4-DECODER-KEPT-SET-2026-001", "blank_token_id": BLANK,
           "vocab_size": VOCAB_SIZE, "token_ids": list(KEPT)}
    doc.update(overrides)
    return json.dumps(doc, sort_keys=True).encode()


# ---------------------------------------------------------------------------
# (i) the default path
# ---------------------------------------------------------------------------
def test_default_is_off():
    assert resolve_dump_mode({}) == "off"


def test_blank_value_is_the_default():
    assert resolve_dump_mode({KEY: "   "}) == "off"


def test_the_decode_region_is_byte_identical_to_the_pre_change_file():
    region = _region(DECODE_START, DECODE_END)
    assert hashlib.sha256(region.encode()).hexdigest() == \
        GOLDEN_DECODE_REGION_SHA256
    assert "_ctc_greedy_text(logits[0], decoder, blank_idx," in region


def test_the_receipts_region_is_byte_identical_to_the_pre_change_file():
    region = _region(RECEIPTS_START, RECEIPTS_END)
    assert hashlib.sha256(region.encode()).hexdigest() == \
        GOLDEN_RECEIPTS_REGION_SHA256
    # nothing new leaks into receipts.json ('json.dumps' is not a dump
    # reference, so it is removed before looking for one)
    assert "dump" not in region.replace("json.dumps", "")


def test_only_two_dump_statements_in_main_are_unguarded():
    main = next(node for node in ast.parse(SRC).body
                if isinstance(node, ast.FunctionDef) and node.name == "main")
    found: list[tuple[str, bool]] = []

    def names(node) -> bool:
        # a reference to the dump objects themselves — json.dumps is a call on
        # `json` and must not count
        return any(isinstance(child, ast.Name)
                   and child.id in {"dump", "dump_mode"}
                   for child in ast.walk(node))

    def walk(statements, guarded):
        for node in statements:
            if isinstance(node, ast.If):
                walk(node.body, guarded or names(node.test))
                walk(node.orelse, guarded)
            elif isinstance(node, (ast.For, ast.While, ast.With, ast.Try)):
                walk(node.body, guarded)
                walk(getattr(node, "orelse", []), guarded)
                walk(getattr(node, "finalbody", []), guarded)
                for handler in getattr(node, "handlers", []):
                    walk(handler.body, guarded)
            elif names(node):
                found.append((ast.unparse(node), guarded))

    walk(main.body, False)
    assert {text for text, guarded in found if not guarded} == ALLOWED_UNGUARDED
    assert [text for text, guarded in found if guarded], \
        "the guarded dump calls disappeared from main()"


def test_the_evaluator_still_adds_no_decode_logic_of_its_own():
    # the recompute path calls the frozen function; it does not reimplement it
    assert "from pipeline.omniasr_calibrate import _ctc_greedy_text" in SRC
    assert "def _ctc_greedy_text" not in SRC


# ---------------------------------------------------------------------------
# (ii) refusal, never a silent fallback
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("value", ["hybrid", "HYBRID", " Hybrid "])
def test_hybrid_must_be_explicit(value):
    assert resolve_dump_mode({KEY: value}) == "hybrid"


@pytest.mark.parametrize("value", ["full", "on", "1", "true", "yes", "hybrid1",
                                   "off,hybrid", "topk", "dump"])
def test_unknown_values_refuse_rather_than_fall_back(value):
    with pytest.raises(EvaluatorRefusal, match=KEY):
        resolve_dump_mode({KEY: value})


def test_only_two_modes_exist_and_full_is_not_one_of_them():
    assert DUMP_MODES == ("off", "hybrid")


def test_topk_default_and_bounds():
    assert resolve_dump_topk({}) == 16
    assert resolve_dump_topk({"MEDZEN_SCORE_DUMP_TOPK": " 8 "}) == 8
    for bad in ("0", "65", "-1", "eight", "8.5"):
        with pytest.raises(EvaluatorRefusal, match="MEDZEN_SCORE_DUMP_TOPK"):
            resolve_dump_topk({"MEDZEN_SCORE_DUMP_TOPK": bad})


def test_dest_defaults_to_an_existing_output_directory():
    assert DUMP_DEST_ROOTS == ("/opt/ml/checkpoints", "/opt/ml/model")
    assert resolve_dump_dest({}) == Path("/opt/ml/checkpoints")
    assert resolve_dump_dest({"MEDZEN_SCORE_DUMP_DEST":
                              "/opt/ml/checkpoints/dump"}) == \
        Path("/opt/ml/checkpoints/dump")


@pytest.mark.parametrize("value", ["/tmp/dump", "/opt/ml/input/data",
                                   "s3://medzen-speech/research/x", "dump"])
def test_dest_outside_the_jobs_output_directories_refuses(value):
    with pytest.raises(EvaluatorRefusal, match="MEDZEN_SCORE_DUMP_DEST"):
        resolve_dump_dest({"MEDZEN_SCORE_DUMP_DEST": value})


# ---------------------------------------------------------------------------
# the kept set is a byte-pinned artifact, never recomputed or assumed
# ---------------------------------------------------------------------------
def test_kept_set_parses_and_keeps_the_declared_order():
    kept, doc = parse_kept_set(_artifact(token_ids=[7, 1, 5]), blank_idx=BLANK)
    assert kept == [7, 1, 5]
    assert doc["record"] == "CM4-DECODER-KEPT-SET-2026-001"


@pytest.mark.parametrize("payload,match", [
    (_artifact(token_ids=[]), "non-empty"),
    (_artifact(token_ids=[5, 5, 1]), "duplicates"),
    (_artifact(token_ids=[1, "5"]), "not an integer"),
    (json.dumps({"token_ids": [1, 5]}).encode(), "blank_token_id"),
    (_artifact(blank_token_id=0), "pad_idx"),
    (_artifact(token_ids=[5, 6, 7]), "omit the blank"),
    (b"not json at all", "not JSON"),
])
def test_kept_set_refusals(payload, match):
    with pytest.raises(EvaluatorRefusal, match=match):
        parse_kept_set(payload, blank_idx=BLANK)


# ---------------------------------------------------------------------------
# the record layout
# ---------------------------------------------------------------------------
def test_frame_record_is_the_designed_282_bytes():
    pytest.importorskip("numpy")
    # 105 kept symbols (104 training-target tokens + blank) and top-16:
    # 105*2 + 16*2 + 16*2 + 4 + 2 + 2
    assert dump_frame_dtype(105, 16).itemsize == 282
    assert dump_frame_dtype(len(KEPT), TOPK).itemsize == \
        len(KEPT) * 2 + TOPK * 4 + 8


def test_encoder_refuses_values_fp16_cannot_hold_losslessly():
    np = pytest.importorskip("numpy")
    wide = _logits()
    wide[0, 0] = 70000.0
    with pytest.raises(EvaluatorRefusal, match="fp16"):
        encode_frames(wide, wide.argmax(axis=-1), kept_ids=KEPT, topk=TOPK)
    wide[0, 0] = np.inf
    with pytest.raises(EvaluatorRefusal, match="non-finite"):
        encode_frames(wide, wide.argmax(axis=-1), kept_ids=KEPT, topk=TOPK)


# ---------------------------------------------------------------------------
# (iii) greedy recomputed from the dump alone
# ---------------------------------------------------------------------------
def _assert_recompute_matches(np):
    wide = _logits()
    expected = _frozen_text(wide)
    assert expected == "[5][33][6][7][33]", expected
    record, peak = encode_frames(wide, wide.argmax(axis=-1),
                                 kept_ids=KEPT, topk=TOPK)
    text, divergence = greedy_from_dump(record, kept_ids=KEPT,
                                        vocab_size=VOCAB_SIZE,
                                        blank_idx=BLANK, decoder=_render)
    assert text == expected
    assert divergence == 0
    assert peak == pytest.approx(float(np.abs(wide).max()), abs=1e-3)
    # the frames whose argmax is OUTSIDE the kept set are the ones that make
    # the argmax column load-bearing: a kept-set-only dump decodes differently
    outside = [frame for frame, token in enumerate(ARGMAX_PLAN)
               if token not in KEPT]
    assert outside == [1, 7]
    kept_only = np.full((len(wide), VOCAB_SIZE), -np.inf, dtype="float32")
    kept_only[:, KEPT] = record["kept"].astype("float32")
    assert _frozen_text(kept_only) != expected
    # and the stored values are exact full-vocabulary log-probabilities
    probability = float(np.exp(record["topk_logits"][0][0].astype("float64")
                               - record["logsumexp"][0]))
    reference = float(np.exp(wide[0].astype("float64")
                             - np.log(np.exp(wide[0].astype("float64")).sum()))
                      .max())
    assert probability == pytest.approx(reference, rel=1e-3)


def test_greedy_recomputed_from_the_dump_reproduces_the_scorer(fake_torch):
    np = pytest.importorskip("numpy")
    _assert_recompute_matches(np)


def test_greedy_recompute_under_real_torch():
    pytest.importorskip("torch")
    np = pytest.importorskip("numpy")
    _assert_recompute_matches(np)


def test_reconstructed_matrix_carries_the_argmax_even_when_out_of_set():
    np = pytest.importorskip("numpy")
    wide = _logits()
    record, _ = encode_frames(wide, wide.argmax(axis=-1), kept_ids=KEPT,
                              topk=TOPK)
    dense = reconstruct_dense(record, kept_ids=KEPT, vocab_size=VOCAB_SIZE)
    assert dense.shape == (len(ARGMAX_PLAN), VOCAB_SIZE)
    assert dense.argmax(axis=-1).tolist() == ARGMAX_PLAN
    for frame, token in enumerate(ARGMAX_PLAN):
        assert dense[frame, token] == pytest.approx(wide[frame, token],
                                                    abs=1e-2)
    assert np.isinf(dense[2, 17])        # nothing invented where nothing kept


# ---------------------------------------------------------------------------
# the writer: every row proves itself against the receipts it accompanies
# ---------------------------------------------------------------------------
def _writer(tmp_path, header=None):
    return _DumpWriter(tmp_path / "dump", kept_ids=KEPT, topk=TOPK,
                       blank_idx=BLANK,
                       header=dict(header or {"manifest_sha256": "m" * 64}))


def test_writer_round_trip_and_header(tmp_path, fake_torch):
    np = pytest.importorskip("numpy")
    wide = _logits()
    expected = _frozen_text(wide)
    writer = _writer(tmp_path)
    entry = writer.write_row("a" * 64, _FakeLogits(wide), len(wide), expected,
                             _render, samples=16000, sample_rate=16000)
    status = writer.close()
    header = json.loads((tmp_path / "dump" /
                         "score-dump-v1.header.json").read_bytes())
    assert entry["valid_frames"] == len(wide)
    assert header["vocab_size"] == VOCAB_SIZE
    assert header["blank_index"] == BLANK
    assert header["logits_dtype"] == "torch.bfloat16"
    assert header["manifest_sha256"] == "m" * 64
    assert header["frame_record_bytes"] == dump_frame_dtype(len(KEPT),
                                                            TOPK).itemsize
    assert header["frame_rate_fps"]["declared_estimate"] == 49.9
    assert header["frame_rate_fps"]["measured_mean"] == pytest.approx(8.0)
    assert header["rows"][0]["hyp_sha256"] == \
        hashlib.sha256(expected.encode()).hexdigest()
    assert status["frames"] == len(wide)
    # the bytes on disk are exactly the records, and they still decode right
    raw = (tmp_path / "dump" / "score-dump-v1.frames.bin").read_bytes()
    assert len(raw) == status["bytes"] == header["frame_record_bytes"] * len(wide)
    replay = np.frombuffer(raw, dtype=dump_frame_dtype(len(KEPT), TOPK))
    text, _ = greedy_from_dump(replay, kept_ids=KEPT, vocab_size=VOCAB_SIZE,
                               blank_idx=BLANK, decoder=_render)
    assert text == expected


def test_writer_refuses_a_row_that_does_not_reproduce_its_hypothesis(
        tmp_path, fake_torch):
    pytest.importorskip("numpy")
    wide = _logits()
    writer = _writer(tmp_path)
    with pytest.raises(EvaluatorRefusal, match="does not reproduce"):
        writer.write_row("b" * 64, _FakeLogits(wide), len(wide),
                         "[9][9][9]", _render, samples=16000,
                         sample_rate=16000)


def test_writer_refuses_when_the_valid_frame_count_is_unknown(tmp_path,
                                                              fake_torch):
    pytest.importorskip("numpy")
    writer = _writer(tmp_path)
    with pytest.raises(EvaluatorRefusal, match="valid frame count"):
        writer.write_row("c" * 64, _FakeLogits(_logits()), None, "x", _render,
                         samples=16000, sample_rate=16000)


def test_writer_refuses_a_kept_id_outside_the_observed_vocabulary(tmp_path,
                                                                  fake_torch):
    pytest.importorskip("numpy")
    writer = _DumpWriter(tmp_path / "dump", kept_ids=[BLANK, VOCAB_SIZE + 3],
                         topk=TOPK, blank_idx=BLANK, header={})
    with pytest.raises(EvaluatorRefusal, match="outside the observed"):
        writer.write_row("d" * 64, _FakeLogits(_logits()), 4, "x", _render,
                         samples=16000, sample_rate=16000)


# ---------------------------------------------------------------------------
# the pinned artifact reaches the header by sha, not by hardcoding
# ---------------------------------------------------------------------------
def _dump_env(monkeypatch, tmp_path, sha):
    import pipeline.omniasr_score as score
    monkeypatch.setattr(score, "DUMP_DEST_ROOTS", (str(tmp_path),))
    monkeypatch.setenv("MEDZEN_SCORE_DUMP_MODE", "hybrid")
    monkeypatch.setenv("MEDZEN_SCORE_DUMP_TOPK", str(TOPK))
    monkeypatch.setenv("MEDZEN_SCORE_DUMP_DEST", str(tmp_path / "out"))
    monkeypatch.setenv("MEDZEN_SCORE_DUMP_SYMBOLS_S3_URI",
                       "s3://medzen-speech/research/arm2-decoder/kept.json")
    monkeypatch.setenv("MEDZEN_SCORE_DUMP_SYMBOLS_VERSION_ID", "VID123")
    monkeypatch.setenv("MEDZEN_SCORE_DUMP_SYMBOLS_SHA256", sha)


def _tokenizer(pad_idx=BLANK):
    return types.SimpleNamespace(
        vocab_info=types.SimpleNamespace(pad_idx=pad_idx))


def test_open_dump_records_the_artifact_sha_in_the_header(tmp_path, monkeypatch,
                                                          fake_torch):
    pytest.importorskip("numpy")
    artifact = _artifact()
    sha = hashlib.sha256(artifact).hexdigest()
    _dump_env(monkeypatch, tmp_path, sha)
    writer = _open_dump(_FakeS3(artifact), os.environ, scratch=tmp_path,
                        blank_idx=BLANK, tokenizer=_tokenizer(),
                        manifest_sha="f" * 64, job_name="medzen-b5-x",
                        arm="icmp15-base", model_sha="9" * 64,
                        image_digest="sha256:" + "e" * 64,
                        input_convention="normalized", manifest_rows=845)
    wide = _logits()
    writer.write_row("a" * 64, _FakeLogits(wide), len(wide), _frozen_text(wide),
                     _render, samples=16000, sample_rate=16000)
    writer.close()
    header = json.loads((tmp_path / "out" /
                         "score-dump-v1.header.json").read_bytes())
    assert header["kept_set"]["sha256"] == sha        # pinned, not hardcoded
    assert header["kept_set"]["token_ids"] == KEPT
    assert header["kept_set"]["declared_blank_token_id"] == BLANK
    assert header["blank_index"] == BLANK
    assert header["tokenizer_pad_idx_raw"] == BLANK
    assert header["manifest_sha256"] == "f" * 64
    assert header["manifest_rows"] == 845
    assert header["logits_are_normalized"] is False
    assert header["topk"] == TOPK
    assert header["mode"] == "hybrid"


def test_open_dump_refuses_a_substituted_artifact(tmp_path, monkeypatch):
    _dump_env(monkeypatch, tmp_path, "0" * 64)
    with pytest.raises(EvaluatorRefusal, match="refusing a substituted input"):
        _open_dump(_FakeS3(_artifact()), os.environ, scratch=tmp_path,
                   blank_idx=BLANK, tokenizer=_tokenizer(),
                   manifest_sha="f" * 64, job_name="j", arm="a",
                   model_sha="9" * 64, image_digest="d",
                   input_convention="normalized", manifest_rows=1)


def test_open_dump_refuses_a_tokenizer_without_a_declared_blank(tmp_path,
                                                                monkeypatch):
    # blank_idx 0 is what `int(pad_idx or 0)` yields when pad_idx is absent —
    # a dump run must refuse that fallback rather than record a guessed blank
    artifact = _artifact(blank_token_id=0, token_ids=[0, 5, 6, 7])
    _dump_env(monkeypatch, tmp_path, hashlib.sha256(artifact).hexdigest())
    with pytest.raises(EvaluatorRefusal, match="pad_idx is absent"):
        _open_dump(_FakeS3(artifact), os.environ, scratch=tmp_path,
                   blank_idx=0, tokenizer=_tokenizer(pad_idx=None),
                   manifest_sha="f" * 64, job_name="j", arm="a",
                   model_sha="9" * 64, image_digest="d",
                   input_convention="normalized", manifest_rows=1)
