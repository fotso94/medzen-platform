"""Adversarial tests for the Arm-2 nomination scorer v2 (Codex second-review
2026-08-25 finding 5): every input authenticated through the frozen scoring
packet; edit distances RECOMPUTED from hypotheses; finalist DERIVED from the
stage-1 decision."""
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import arm2_nomination_scorer as scorer          # noqa: E402


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def ident(surface: str, i: int) -> str:
    return hashlib.sha256(f"{surface}:{i}".encode()).hexdigest()


REF_WORDS = 10          # every synthetic reference is 10 words


def make_ref(surface: str, i: int) -> str:
    return " ".join(f"{surface}w{i}t{k}" for k in range(REF_WORDS))


def hyp_with_wer(reference: str, wer: float) -> str:
    """Corrupt round(wer*REF_WORDS) words -> exact word-level edit distance."""
    words = reference.split()
    bad = int(round(wer * len(words)))
    for k in range(bad):
        words[k] = words[k] + "_x"
    return " ".join(words)


def build_world(tmp_path: Path):
    """A miniature committed world: split, references, clusters, candidate
    packets, scoring packet — all sha-pinned under tmp_path as root."""
    (tmp_path / "platform/manifests/arm2-scoring").mkdir(parents=True)
    (tmp_path / "platform/decisions").mkdir(parents=True)
    split = {lang: [ident(lang, i) for i in range(6)]
             for lang in scorer.NOMINATION_LANGUAGES}
    split_doc = {"manifest": {"split": split}}
    sp = tmp_path / "platform/manifests/split.json"
    sp.write_bytes(json.dumps(split_doc).encode())
    refs = {}
    clusters = {}
    for lang in scorer.NOMINATION_LANGUAGES:
        rows = []
        for i, identity in enumerate(split[lang]):
            refs[identity] = make_ref(lang, i)
            clusters[identity] = f"spk:{lang}-{i // 2}"
            rows.append({"audio_checksum_sha256": identity,
                         "text_normalized": refs[identity],
                         "speaker_id": f"{lang}-{i // 2}"})
        (tmp_path / f"platform/manifests/arm2-scoring/references-{lang}.jsonl"
         ).write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    for surface in scorer.VETO_SURFACES:
        rows = []
        for i in range(4):
            identity = ident("veto:" + surface, i)
            refs[identity] = make_ref(surface, i)
            rows.append({"audio_checksum_sha256": identity,
                         "text_normalized": refs[identity]})
        (tmp_path / f"platform/manifests/arm2-scoring/references-{surface}.jsonl"
         ).write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    (tmp_path / "platform/manifests/arm2-scoring/clusters.json").write_text(
        json.dumps({"clusters": clusters}))
    alphas = {"H1": 0.25, "H2": 1.0, "H3": 0.5, "H4": 0.5}
    cand_pins = {}
    for c, a in alphas.items():
        body = {"job_id": f"x-{c.lower()}", "distillation": {"kd_alpha": a}}
        rel = f"platform/manifests/{c}.json"
        (tmp_path / rel).write_bytes(json.dumps(body).encode())
        raw = (tmp_path / rel).read_bytes()
        canonical = sha(json.dumps(json.loads(raw), sort_keys=True,
                                   separators=(",", ":")).encode())
        cand_pins[c] = {"path": rel, "sha256": sha(raw),
                        "canonical_sha256": canonical}
    packet = {
        "record": "B5-UNIVERSAL-ARM2-NOMINATION-SCORING-PACKET-2026-001",
        "split": {"path": "platform/manifests/split.json",
                  "sha256": sha(sp.read_bytes())},
        "references": {s: {"s3_uri": f"s3://x/references-{s}.jsonl",
                            "s3_version_id": "V1",
                            "sha256": sha((tmp_path / f"platform/manifests/arm2-scoring/references-{s}.jsonl").read_bytes())}
                       for s in (*scorer.NOMINATION_LANGUAGES,
                                 *scorer.VETO_SURFACES)},
        "clusters": {"path": "platform/manifests/arm2-scoring/clusters.json",
                     "sha256": sha((tmp_path / "platform/manifests/arm2-scoring/clusters.json").read_bytes())},
        "candidate_packets": cand_pins,
        "bootstrap": {"B": 10000, "master_seed": 7},
        "evaluator": {"job_name_pattern":
                      r"medzen-b5-b5-universal-arm2-score-[a-z0-9-]+"},
    }
    pp = tmp_path / "platform/decisions/scoring-packet.json"
    pp.write_bytes(json.dumps(packet).encode())
    cfg = scorer.load_scoring_packet(
        tmp_path, pp,
        references_dir=tmp_path / "platform/manifests/arm2-scoring")
    return tmp_path, pp, cfg, split, refs


def hyps_for(cfg, refs, wer_by_surface: dict[str, float]) -> dict[str, str]:
    out = {}
    for lang in scorer.NOMINATION_LANGUAGES:
        for identity in cfg["split"][lang]:
            out[identity] = hyp_with_wer(refs[identity], wer_by_surface[lang])
    for surface in scorer.VETO_SURFACES:
        for identity in cfg["references"][surface]:
            out[identity] = hyp_with_wer(refs[identity],
                                         wer_by_surface[surface])
    return out


def flat(w_nom: float, w_veto: float) -> dict[str, float]:
    d = {lang: w_nom for lang in scorer.NOMINATION_LANGUAGES}
    d.update({s: w_veto for s in scorer.VETO_SURFACES})
    return d


BASELINE = flat(0.10, 0.10)


def run_stage1(tmp_path, cand_wers: dict[str, dict[str, float]]):
    _, _, cfg, split, refs = build_world(tmp_path)
    hyps = {a: hyps_for(cfg, refs, BASELINE) for a in scorer.COMPARATORS}
    for c in scorer.CANDIDATES:
        hyps[c] = hyps_for(cfg, refs, cand_wers[c])
    return scorer.score_stage(stage=1, cfg=cfg, hyps_by_arm=hyps)


# ------------------------------------------------------------------ metric
def test_word_edits_is_exact_levenshtein():
    assert scorer.word_edits("a b c", "a b c") == (0, 3)
    assert scorer.word_edits("a b c", "a x c") == (1, 3)
    assert scorer.word_edits("a b c", "a c") == (1, 3)
    assert scorer.word_edits("a b c", "a b c d") == (1, 3)
    assert scorer.word_edits("", "x y") == (2, 0)


# --------------------------------------------------------------- decisions
def test_clear_winner_qualifies_and_wins(tmp_path):
    wers = {c: BASELINE for c in scorer.CANDIDATES}
    wers["H1"] = flat(0.0, 0.10)
    d = run_stage1(tmp_path, wers)
    assert d["per_candidate"]["H1"]["qualifies"]
    assert d["outcome"] == "PROVISIONAL_FINALIST"
    assert d["provisional_finalist"] == "H1"


def test_no_qualifier_is_terminal(tmp_path):
    d = run_stage1(tmp_path, {c: BASELINE for c in scorer.CANDIDATES})
    assert d["outcome"] == "NO_RECIPE_QUALIFIES"


def test_veto_disqualifies_a_positive_winner(tmp_path):
    good = flat(0.0, 0.10)
    good["lingala"] = 0.20            # +10pp regression vs base -> veto
    d = run_stage1(tmp_path, {"H1": BASELINE, "H2": BASELINE,
                              "H3": good, "H4": BASELINE})
    pc = d["per_candidate"]["H3"]
    assert pc["holm_all_rejected"] is True
    assert pc["vetoes"]["lingala"]["vetoed"] is True
    assert pc["qualifies"] is False
    assert d["outcome"] == "NO_RECIPE_QUALIFIES"


def test_tie_break_alpha_then_lexicographic(tmp_path):
    win = flat(0.0, 0.10)
    d = run_stage1(tmp_path, {"H1": BASELINE, "H2": BASELINE,
                              "H3": win, "H4": win})
    assert set(d["qualifiers"]) == {"H3", "H4"}
    assert d["provisional_finalist"] == "H3"


# --------------------------------------------------------------- refusals
RECEIPT_PROV = {"job_name": "medzen-b5-b5-universal-arm2-score-h1-2026-001",
                "workflow_run_ref": "https://github.com/x/actions/runs/1",
                "attestation_ref": "https://github.com/x/attestations/1"}
EVAL = {"job_name_pattern": r"medzen-b5-b5-universal-arm2-score-[a-z0-9-]+"}


def test_caller_supplied_edit_counts_refuse(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps({**RECEIPT_PROV, "model_sha256": "m" * 64,
        "packet_canonical_sha256": "p" * 64,
        "rows": [{"audio_checksum_sha256": "a" * 64,
                  "hyp_normalized": "x", "edit_distance": 0}]}))
    with pytest.raises(SystemExit, match="caller-supplied edit counts"):
        scorer.load_receipts(p, arm="H1", expected_model_sha="m" * 64,
                             evaluator=EVAL)


def test_wrong_model_sha_refuses(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps({**RECEIPT_PROV, "model_sha256": "m" * 64,
        "packet_canonical_sha256": "p" * 64,
        "rows": [{"audio_checksum_sha256": "a" * 64, "hyp_normalized": "x"}]}))
    with pytest.raises(SystemExit, match="unproven model"):
        scorer.load_receipts(p, arm="H1", expected_model_sha="n" * 64,
                             evaluator=EVAL)


def test_missing_evaluator_provenance_refuses(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"job_name": "medzen-b5-b5-universal-arm2-score-x",
        "model_sha256": "m" * 64, "packet_canonical_sha256": "p" * 64,
        "rows": [{"audio_checksum_sha256": "a" * 64, "hyp_normalized": "x"}]}))
    with pytest.raises(SystemExit, match="workflow_run_ref"):
        scorer.load_receipts(p, arm="H1", expected_model_sha="m" * 64,
                             evaluator=EVAL)


def test_wrong_evaluator_job_name_refuses(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps({**RECEIPT_PROV,
        "job_name": "medzen-b5-someones-laptop",
        "model_sha256": "m" * 64, "packet_canonical_sha256": "p" * 64,
        "rows": [{"audio_checksum_sha256": "a" * 64, "hyp_normalized": "x"}]}))
    with pytest.raises(SystemExit, match="protected-evaluator pattern"):
        scorer.load_receipts(p, arm="H1", expected_model_sha="m" * 64,
                             evaluator=EVAL)


def test_arm_completion_receipt_gates_model_identity():
    good = {"export": {"model_sha256": "ab" * 32},
            "terminal_status": "Completed",
            "authoritative_verification": {"authoritative": True,
                "verdict": "PASS", "failures": [], "mode": "live"}}
    assert scorer.load_arm_completion_receipt(
        json.dumps(good).encode(), arm="H1") == "ab" * 32
    bad = json.loads(json.dumps(good))
    bad["authoritative_verification"]["verdict"] = "FAIL"
    with pytest.raises(SystemExit, match="unambiguous live PASS"):
        scorer.load_arm_completion_receipt(json.dumps(bad).encode(), arm="H1")
    bad2 = json.loads(json.dumps(good))
    del bad2["export"]["model_sha256"]
    with pytest.raises(SystemExit, match="export.model_sha256"):
        scorer.load_arm_completion_receipt(json.dumps(bad2).encode(), arm="H1")


def test_missing_rows_refuse(tmp_path):
    _, _, cfg, split, refs = build_world(tmp_path)
    hyps = {a: hyps_for(cfg, refs, BASELINE)
            for a in (*scorer.COMPARATORS, *scorer.CANDIDATES)}
    del hyps["H1"][split["english"][0]]
    with pytest.raises(SystemExit, match="missing 1 row"):
        scorer.score_stage(stage=1, cfg=cfg, hyps_by_arm=hyps)


def test_pin_mismatch_refuses(tmp_path):
    root, pp, cfg, _, _ = build_world(tmp_path)
    # tamper one fetched reference file after packet authoring
    target = root / "platform/manifests/arm2-scoring/references-english.jsonl"
    target.write_text(target.read_text() + "\n")
    with pytest.raises(SystemExit, match="substituted input"):
        scorer.load_scoring_packet(
            root, pp,
            references_dir=root / "platform/manifests/arm2-scoring")


def test_stage2_requires_derived_finalist(tmp_path):
    with pytest.raises(SystemExit, match="DERIVED from the Stage-1"):
        scorer.main(["stage2", "--scoring-packet", "x",
                     "--scoring-packet-sha256", "0" * 64,
                     "--references-dir", str(tmp_path),
                     "--arm-receipts", "base=y",
                     "--receipts", "base=z", "--out", "o"])


def test_stage2_reversal_and_confirmation(tmp_path):
    root, _, cfg, _, refs = build_world(tmp_path)
    base = {a: hyps_for(cfg, refs, BASELINE) for a in scorer.COMPARATORS}
    # reversal: finalist no longer better
    d = scorer.score_stage(stage=2, cfg=cfg,
                           hyps_by_arm={**base,
                                        "H1": hyps_for(cfg, refs, BASELINE)},
                           finalist="H1")
    assert d["outcome"] == "NO_RECIPE_QUALIFIES"
    # confirmation
    d = scorer.score_stage(stage=2, cfg=cfg,
                           hyps_by_arm={**base,
                                        "H1": hyps_for(cfg, refs,
                                                       flat(0.0, 0.10))},
                           finalist="H1")
    assert d["outcome"] == "FINAL_NOMINATION"


# ----------------------------------------------------- real committed pins
def test_real_scoring_packet_shape_and_pins():
    packet = json.loads(
        (ROOT / scorer.SCORING_PACKET_PATH).read_bytes())
    # item 6: reference transcripts are NOT in the repo — every pin is an
    # exact S3 object (uri + VersionId) + sha256
    for s in (*scorer.NOMINATION_LANGUAGES, *scorer.VETO_SURFACES):
        pin = packet["references"][s]
        assert pin["s3_uri"].startswith(
            "s3://medzen-speech/curated/_arm2_scoring/"), s
        assert pin["s3_version_id"], s
        assert len(pin["sha256"]) == 64, s
        assert not (ROOT / "platform/manifests/arm2-scoring" /
                    f"references-{s}.jsonl").exists(), (
            s, "transcripts must not be committed (item 6)")
    # item 3: the evaluator identity surface is frozen
    ev = packet["evaluator"]
    assert ev["job_name_pattern"].startswith("medzen-b5-b5-universal-arm2-score-")
    # clusters (no transcripts) stay committed; frozen structure holds
    clusters = json.loads((ROOT / packet["clusters"]["path"]).read_bytes())
    split = json.loads((ROOT / packet["split"]["path"]).read_bytes())[
        "manifest"]["split"]
    for lang, want in (("english", 451), ("french", 470),
                       ("pidgin", 25), ("swahili", 325)):
        got = len({clusters["clusters"][i] for i in split[lang]})
        assert got == want, (lang, got)
    # candidate alphas pinned by canonical sha
    for c in scorer.CANDIDATES:
        pin = packet["candidate_packets"][c]
        raw = (ROOT / pin["path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == pin["sha256"], c
    assert packet["bootstrap"]["B"] >= scorer.MIN_B


# ------------------------------------------------------------- determinism
def test_determinism_same_world_identical_decision(tmp_path):
    wers = {c: BASELINE for c in scorer.CANDIDATES}
    wers["H1"] = flat(0.0, 0.10)
    a = run_stage1(tmp_path / "a", wers)
    b = run_stage1(tmp_path / "b", wers)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_bootstrap_is_paired(tmp_path):
    rows = [(1, 10)] * 6
    cl = [[0, 1], [2, 3], [4, 5]]
    plan = scorer.resample_plan(len(cl), 500, 42)
    out = scorer.bootstrap_wers({"a": rows, "b": list(rows)}, cl, plan)
    assert out["a"] == out["b"]
