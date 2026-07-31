"""B2 ingest-path tests: local shard reading, exact limits, clean exit,
near-dup clustering, and watchdog termination.

Network-touching tests are marked `net` and skipped by default:
    pytest tests -q                 # offline only
    pytest tests -q -m net          # include shard download (slow, ~8 min)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import yaml
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.adapters.waxalnlp import CONFIGS, REVISION, WaxalNLPAdapter  # noqa: E402
from pipeline.ingest import assign_splits  # noqa: E402

REG = ROOT / "registry" / "languages"


# --------------------------------------------------------------------------- #
# near-duplicate clustering (the acholi/tts rejection)
# --------------------------------------------------------------------------- #
def _rows(texts, strategy="text_disjoint"):
    return [{"text_normalized": t, "text_verbatim": t,
             "speaker_id": f"s{i % 3}", "split_strategy": strategy}
            for i, t in enumerate(texts)]


def test_near_dup_pair_never_straddles_a_text_disjoint_split():
    """Jaccard ~0.82 pair: different strings, so exact-text hashing put them on
    opposite sides and the validator rejected the corpus. Whole clusters must
    move together."""
    a = "take this medicine two times a day after food"
    b = "take this medicine two times a day after chop"
    rows = _rows([a, b] + [f"unrelated sentence number {i} about other things" for i in range(40)])
    assign_splits(rows)
    sa = next(r["split"] for r in rows if r["text_normalized"] == a)
    sb = next(r["split"] for r in rows if r["text_normalized"] == b)
    assert sa == sb, f"near-duplicates split apart: {sa} vs {sb}"


def test_clustering_still_produces_both_splits():
    """Clustering must not collapse everything into one split."""
    vocab = ["fever headache cough", "market money trade", "river boat fishing",
             "school teacher lesson", "farm maize harvest", "hospital nurse clinic",
             "rain season planting", "road transport journey", "child birth family",
             "medicine dosage tablet", "phone credit airtime", "football match score",
             "cooking oil pepper", "church prayer sunday", "police station report",
             "bank account savings"]
    rows = _rows([f"{v} number {i}" for i, v in enumerate(vocab)])
    assign_splits(rows)
    splits = {r["split"] for r in rows}
    assert splits == {"train", "test"}, f"expected both splits, got {splits}"


def test_identical_text_shares_a_split():
    same = "wetin dey happen for the house today"
    rows = _rows([same, same] + [f"other sentence {i} entirely" for i in range(30)])
    assign_splits(rows)
    got = {r["split"] for r in rows if r["text_normalized"] == same}
    assert len(got) == 1


def test_asr_splits_by_speaker_not_text():
    rows = [{"text_normalized": f"sentence {i}", "text_verbatim": f"sentence {i}",
             "speaker_id": f"spk{i % 4}", "split_strategy": "speaker_disjoint"}
            for i in range(40)]
    assign_splits(rows)
    train = {r["speaker_id"] for r in rows if r["split"] == "train"}
    test = {r["speaker_id"] for r in rows if r["split"] == "test"}
    assert train and test and not (train & test), "speakers must be disjoint"


# --------------------------------------------------------------------------- #
# adapter contract (offline)
# --------------------------------------------------------------------------- #
def test_revision_is_pinned_to_a_commit_sha():
    assert len(REVISION) == 40 and all(c in "0123456789abcdef" for c in REVISION), \
        "revision must be a full commit SHA, never a branch like @main"


def test_wolof_is_absent_so_ingest_fails_loudly():
    assert "wolof" not in CONFIGS
    with pytest.raises(ValueError, match="no data for 'wolof'"):
        WaxalNLPAdapter("wolof")


def test_unavailable_task_is_rejected():
    """pidgin is TTS-only in WaxalNLP; asking for ASR must fail, not silently
    fall back to a different task."""
    with pytest.raises(ValueError, match="no asr data"):
        WaxalNLPAdapter("pidgin", task="asr")


def test_shard_glob_matches_the_readme_layout():
    a = WaxalNLPAdapter("acholi", "tts")
    assert a.shard_glob == "data/TTS/ach/ach-train-*"
    b = WaxalNLPAdapter("akan", "asr")
    assert b.shard_glob == "data/ASR/aka/aka-train-*"


def test_licence_follows_the_provider_not_the_dataset():
    assert WaxalNLPAdapter("akan", "asr").spec.license_policy == "commercial_ok"
    assert WaxalNLPAdapter("acholi", "tts").spec.license_policy == "sharealike_review"


def test_adapter_creates_no_remote_iterator_at_construction():
    """Construction must be cheap and offline — no network, no Arrow handle."""
    t0 = time.monotonic()
    WaxalNLPAdapter("acholi", "tts")
    assert time.monotonic() - t0 < 1.0


# --------------------------------------------------------------------------- #
# watchdog: process-group termination
# --------------------------------------------------------------------------- #
def test_watchdog_terminates_the_whole_process_group():
    """kill on the parent alone orphans children. The driver signals the group,
    so a child spawned by the corpus process must die too."""
    script = (
        "import subprocess, sys, time, os\n"
        "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'])\n"
        "print(c.pid, flush=True)\n"
        "time.sleep(300)\n"
    )
    p = subprocess.Popen([sys.executable, "-c", script],
                         stdout=subprocess.PIPE, text=True, start_new_session=True)
    child_pid = int(p.stdout.readline().strip())
    pgid = os.getpgid(p.pid)

    os.killpg(pgid, 15)                      # TERM to the group
    # Generous, because this asserts that the grandchild DIES, not how fast.
    # Under emulation (amd64 image on arm64) process teardown is an order of
    # magnitude slower and a 10-second deadline failed there -- a timing
    # artifact reported as an orphan leak.
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            os.kill(child_pid, 0)
            time.sleep(0.2)
        except OSError:
            break
    else:
        os.killpg(pgid, 9)
        pytest.fail("grandchild survived group TERM — orphan leak")

    p.wait(timeout=10)
    with pytest.raises(OSError):
        os.kill(child_pid, 0)                # grandchild really is gone


def test_driver_uses_group_termination_not_bare_kill9():
    sh = (ROOT / "scripts" / "ingest_all.sh").read_text()
    assert 'kill -TERM "-$pgid"' in sh, "must TERM the process group"
    assert 'kill -KILL "-$pgid"' in sh, "must KILL the group as last resort"
    assert "kill -9 \"$pid\"" not in sh, "bare kill -9 on the parent leaves orphans"


# --------------------------------------------------------------------------- #
# schema: raw provenance is mandatory
# --------------------------------------------------------------------------- #
def test_raw_provenance_is_required_by_schema():
    d = json.loads((ROOT / "schemas" / "manifest.schema.json").read_text())
    for f in ("raw_filepath", "raw_checksum_sha256"):
        assert f in d["required"], f"{f} must be required — every corpus retains its source"


def test_verify_corpus_checks_raw_checksum():
    src = (ROOT / "scripts" / "verify_corpus.py").read_text()
    assert "RAW CHECKSUM MISMATCH" in src, "raw bytes must be verified, not just present"


def test_ingest_does_not_hard_exit():
    """os._exit() masks resource leaks. The local reader must exit normally."""
    src = (ROOT / "pipeline" / "ingest.py").read_text()
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    assert "os._exit(" not in code, "hard exit bypasses cleanup; fix the leak instead"


def test_adapter_projects_columns_to_bound_memory():
    """A full row group to_pylist() can be several GB."""
    src = (ROOT / "pipeline" / "adapters" / "waxalnlp.py").read_text()
    assert "columns=cols" in src, "read_row_group must project to needed columns"
    # strip comments/docstring: the module explains WHY streaming is banned
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    code = code.split('"""', 2)[-1]
    assert "streaming=True" not in code, "remote streaming reintroduces Arrow #45214"


# --------------------------------------------------------------------------- #
# network: real shard read, exact limits, clean exit
# --------------------------------------------------------------------------- #
@pytest.mark.net
@pytest.mark.parametrize("limit", [1, 5])
def test_local_shard_read_respects_exact_limit(limit):
    a = WaxalNLPAdapter("acholi", "tts")
    items = list(a.items(limit=limit))
    assert len(items) == limit, f"asked {limit}, got {len(items)}"
    for it in items:
        r = it["record"]
        assert r["sample_rate"] == 16000 and r["channels"] == 1
        assert 1.0 <= r["duration_s"] <= 30.0
        assert it["wav_path"].read_bytes()[:4] == b"RIFF", "curated audio must be real WAV"
        assert it["raw_path"].exists() and it["raw_path"].stat().st_size > 0
        assert r["raw_filepath"] and r["raw_checksum_sha256"]
        assert "None" not in r["speaker_id"], "speaker id must never be literal None"


@pytest.mark.net
def test_ingest_subprocess_exits_cleanly_and_promptly():
    """The 4h38m stall was a process that finished its work and then failed to
    exit. Completion must mean termination."""
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "HF_HUB_DISABLE_XET": "1"}
    p = subprocess.run(
        [sys.executable, "-m", "pipeline.ingest", "--source", "waxalnlp",
         "--language", "acholi", "--task", "tts", "--limit", "5", "--dry-run"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=1500)
    assert p.returncode == 0, p.stdout[-1500:] + p.stderr[-800:]
    assert "pass all A3 checks" in p.stdout


# --------------------------------------------------------------------------- #
# version threading + speaker fallback (regressions)
# --------------------------------------------------------------------------- #
def test_audio_uri_honours_the_requested_version():
    """--version v2 must not reference v1 audio."""
    a = WaxalNLPAdapter("acholi", "tts", version="v2")
    assert a.version == "v2"
    src = (ROOT / "pipeline" / "adapters" / "waxalnlp.py").read_text()
    assert "/{self.version}/audio/" in src, "version must be interpolated, not hard-coded"
    assert "/v1/audio/" not in src, "hard-coded v1 makes v2 manifests point at v1 audio"


def test_ingest_threads_version_into_the_adapter():
    src = (ROOT / "pipeline" / "ingest.py").read_text()
    assert "version=version" in src, "ingest --version must reach the adapter"


def test_speaker_id_never_becomes_the_string_none():
    """A literal 'None' speaker merges distinct speakers into one id and breaks
    speaker-disjoint splitting."""
    src = (ROOT / "pipeline" / "adapters" / "waxalnlp.py").read_text()
    assert '"none", "null"' in src, "must reject None-like speaker values"
    assert "_unknown_" in src, "must derive a stable fallback speaker id"


def test_audio_is_spilled_to_disk_not_held_in_ram():
    src = (ROOT / "pipeline" / "adapters" / "waxalnlp.py").read_text()
    assert "raw_path" in src and "wav_path" in src
    assert '"wav_bytes"' not in src, "retaining audio bytes drove peak RSS to ~1.9 GB"
    ing = (ROOT / "pipeline" / "ingest.py").read_text()
    assert 'it["wav_path"]' in ing, "uploads must stream from disk"


# --------------------------------------------------------------------------- #
# B3 — decode evidence must be paired, and provisional must block production
# --------------------------------------------------------------------------- #
def test_pidgin_decode_is_recorded_as_provisional():
    d = yaml.safe_load((REG / "pidgin.yaml").read_text())
    ds = d["asr"]["decode_strategy"]
    assert ds["mode"] == "en_token"
    assert ds["provisional"] is True, "44 read clips from 2 speakers is not a settled result"
    ev = ds["evidence"]
    lo, hi = ev["paired_ci95"]
    assert lo < 0 < hi, "if the paired CI excluded zero it would not be provisional"
    assert ev["distinguishable"] is False
    assert ev["confirm_on"], "a provisional choice must state what would settle it"


def test_production_cannot_rest_on_a_provisional_decode_strategy():
    d = json.loads((ROOT / "schemas" / "language.schema.json").read_text())
    prod = d["allOf"][0]["then"]["properties"]["asr"]["properties"]["decode_strategy"]
    assert prod["properties"]["provisional"] == {"const": False}


def test_runner_no_longer_claims_a_winner_from_an_unpaired_ranking():
    src = (ROOT / "scripts" / "run_baseline.py").read_text()
    assert "RANKING, not a result" in src
    assert "Record decode_strategy.mode=" not in src, "unpaired ranking is not evidence"


def test_paired_comparison_tool_exists_and_reports_an_interval():
    src = (ROOT / "scripts" / "compare_decode_paired.py").read_text()
    assert "paired_ci95" in src and "distinguishable" in src
    assert "utterance-aligned" in src, "must verify arms are paired before pairing them"


def test_chosen_by_run_is_a_stable_durable_reference():
    """A relative local path does not survive a machine change and nothing
    detects if the file is edited afterwards."""
    ds = yaml.safe_load((REG / "pidgin.yaml").read_text())["asr"]["decode_strategy"]
    ref = ds["chosen_by_run"]
    assert ref.startswith(("s3://", "mlflow://")), f"unstable reference: {ref}"
    assert not ref.startswith("file://"), "local paths are not citable evidence"
    sha = ds["evidence"]["artifact_sha256"]
    assert len(sha) == 64 and all(c in "0123456789abcdef" for c in sha)


def test_language_generation_is_idempotent_and_preserves_earned_state(tmp_path):
    """status, decode_strategy, tts approval and llm engine are results of
    experiments and reviews. Regeneration must never discard them.

    Runs against a COPY of the repository. The generator writes into
    registry/languages/, so pointing it at the real tree made this test mutate
    the source -- which fails outright on a read-only checkout and, worse,
    meant a green run had just rewritten the files it was checking."""
    import hashlib
    import shutil

    work = tmp_path / "repo"
    for sub in ("scripts", "registry"):
        shutil.copytree(ROOT / sub, work / sub)
    gen = work / "scripts" / "generate_languages.py"
    reg = work / "registry" / "languages"

    def fingerprint():
        h = hashlib.sha256()
        for f in sorted(reg.glob("*.yaml")):
            h.update(f.read_bytes())
        return h.hexdigest()

    before = fingerprint()
    for _ in range(2):
        r = subprocess.run([sys.executable, str(gen)], capture_output=True,
                           text=True, cwd=work)
        assert r.returncode == 0, r.stdout + r.stderr
        assert fingerprint() == before, "generation is not idempotent"

    ds = yaml.safe_load((reg / "pidgin.yaml").read_text())["asr"]["decode_strategy"]
    assert ds["mode"] == "en_token", "regeneration clobbered the experiment result"
    assert ds["provisional"] is True

    chk = subprocess.run([sys.executable, str(gen), "--check"], capture_output=True, text=True)
    assert chk.returncode == 0, chk.stdout + chk.stderr
