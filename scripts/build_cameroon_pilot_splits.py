#!/usr/bin/env python3
"""Deterministic speaker- AND prompt-disjoint splits for the Cameroon pilot.

Committed for reproducibility (independent review, 2026-09-03).

    test  = TEST_SPK  x TEST_SENT    disjoint from train on BOTH axes
    dev   = TRAIN_SPK x DEV_SENT     prompt-disjoint from train; speakers overlap
    train = TRAIN_SPK x TRAIN_SENT
    everything in a cross cell is DISCARDED (that is the cost of the guarantee)

ROOT-CAUSE FIX: the first version asserted prompt-disjointness on Common Voice
`sentence_id`. One Ngombale prompt exists under TWO sentence_ids with identical
text, so an identical prompt sat on both sides and the assertion passed. Every
assertion here is on NORMALISED TEXT (the platform's own scorer normaliser),
with sentence_id checked only as a secondary signal.

Determinism: seeded, no wall-clock, no Math.random equivalent. Re-running on the
same inputs reproduces byte-identical splits and the same summary hashes.

  python scripts/build_cameroon_pilot_splits.py --root <qualified-data-root>
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/asr-eval-runtime"))
from medzen_asr_eval.metrics import normalize_text  # noqa: E402

SEED = 20260903
TARGET_TEST_HOURS = 0.90
TARGET_DEV_HOURS = 0.50
MIN_TEST_SPEAKERS = 3
LOCALES = ("nnh", "nla", "yav", "gya")


def stable_hash(value: str) -> int:
    return int(hashlib.sha256(value.encode("utf-8")).hexdigest()[:12], 16)


def audited(rows: list[dict], audit: dict) -> list[dict]:
    """Drop near-silent, genuinely clipped (>=6 consecutive saturated samples)
    and edge-truncated clips. mp3 decoder overshoot (peak just over 1.0) is
    normal and is NOT clipping."""
    keep = []
    for row in rows:
        if (row.get("rms") or 1.0) < 1e-4:
            continue
        marks = audit.get(row["path"])
        if marks is not None:
            if marks["max_sat_run"] >= 6:
                continue
            if marks["tail_ratio"] > 0.5 or marks["head_ratio"] > 0.5:
                continue
        keep.append(row)
    return keep


def build(root: Path, locale: str,
          partition_key: str = "text") -> tuple[dict[str, list[dict]], dict]:
    """partition_key="text" is CORRECT and the default. "sentence_id" reproduces
    the as-ingested cp1 splits byte-for-byte, including their one defect (a
    single Ngombale prompt under two sentence_ids), which is corrected in
    eval/ngombala/asr/cvcm1/manifest.r2.jsonl rather than by re-ingesting."""
    rows = audited(
        json.loads((root / f"rows_{locale}.json").read_text()),
        json.loads((root / f"audit_{locale}.json").read_text()))
    by_speaker: dict[str, list[dict]] = collections.defaultdict(list)
    for row in rows:
        by_speaker[row["client_id"]].append(row)
    hours = {k: sum(r["real_s"] for r in v) / 3600 for k, v in by_speaker.items()}
    total = sum(hours.values())

    order = sorted(hours, key=lambda s: stable_hash(s + locale))
    test_speakers: list[str] = []
    accumulated = 0.0
    for speaker in order:
        if len(test_speakers) >= MIN_TEST_SPEAKERS and accumulated >= 0.25 * total:
            break
        if len(test_speakers) >= max(MIN_TEST_SPEAKERS, len(order) // 3):
            break
        test_speakers.append(speaker)
        accumulated += hours[speaker]
    test_speakers_set = set(test_speakers)
    train_speakers = set(hours) - test_speakers_set
    test_hours = sum(hours[s] for s in test_speakers_set)
    train_hours = total - test_hours

    # Partition on NORMALISED TEXT, so two sentence_ids carrying one prompt
    # cannot straddle the split.
    def prompt_of(row: dict) -> str:
        return (row["sentence_id"] if partition_key == "sentence_id"
                else normalize_text(row["sentence"]))

    texts = sorted({prompt_of(r) for r in rows})
    rng = random.Random(SEED + stable_hash(locale) % 1000)
    shuffled = list(texts)
    rng.shuffle(shuffled)
    test_fraction = min(0.45, TARGET_TEST_HOURS / test_hours) if test_hours else 0.0
    dev_fraction = min(0.25, TARGET_DEV_HOURS / train_hours) if train_hours else 0.0
    n_test = max(1, round(test_fraction * len(texts)))
    n_dev = max(1, round(dev_fraction * len(texts)))
    test_texts = set(shuffled[:n_test])
    dev_texts = set(shuffled[n_test:n_test + n_dev])
    train_texts = set(shuffled[n_test + n_dev:])

    splits: dict[str, list[dict]] = {"train": [], "dev": [], "test": [], "discard": []}
    for row in rows:
        speaker = row["client_id"]
        text = prompt_of(row)
        if speaker in train_speakers and text in train_texts:
            splits["train"].append(row)
        elif speaker in train_speakers and text in dev_texts:
            splits["dev"].append(row)
        elif speaker in test_speakers_set and text in test_texts:
            splits["test"].append(row)
        else:
            splits["discard"].append(row)

    def texts_of(name: str) -> set[str]:
        # assertions ALWAYS run on normalised text, whatever the partition key,
        # so the sentence_id mode still REPORTS the defect it reproduces
        return {normalize_text(r["sentence"]) for r in splits[name]}

    def speakers_of(name: str) -> set[str]:
        return {r["client_id"] for r in splits[name]}

    # every assertion on NORMALISED TEXT
    leak = texts_of("train") & texts_of("test")
    if leak and partition_key == "sentence_id":
        summary_leak = sorted(leak)
    else:
        assert not leak, f"{locale}: prompt leak train/test"
        summary_leak = []
    assert not (texts_of("train") & texts_of("dev")), f"{locale}: prompt leak train/dev"
    assert not (texts_of("dev") & texts_of("test")), f"{locale}: prompt leak dev/test"
    assert not (speakers_of("train") & speakers_of("test")), f"{locale}: speaker leak train/test"

    def describe(name: str) -> dict:
        items = splits[name]
        return {"clips": len(items),
                "hours": round(sum(r["real_s"] for r in items) / 3600, 4),
                "speakers": len(speakers_of(name)) if name != "discard" else None,
                "prompts": len(texts_of(name)) if name != "discard" else None}

    summary = {"locale": locale, "seed": SEED, "partition_key": partition_key,
               "known_prompt_leak": summary_leak,
               "total_clips": len(rows), "total_hours": round(total, 4),
               "test_speakers": sorted(test_speakers_set),
               "splits": {k: describe(k) for k in ("train", "dev", "test", "discard")}}
    return splits, summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--partition-key", choices=("text", "sentence_id"),
                        default="text")
    args = parser.parse_args()
    report = {}
    for locale in LOCALES:
        splits, summary = build(args.root, locale, args.partition_key)
        for name in ("train", "dev", "test"):
            payload = ("\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True)
                                 for r in splits[name]) + "\n").encode("utf-8")
            summary["splits"][name]["sha256"] = hashlib.sha256(payload).hexdigest()
            if args.write:
                (args.root / f"asplit_{locale}_{name}.jsonl").write_bytes(payload)
        report[locale] = summary
        s = summary["splits"]
        print(f"{locale}: train {s['train']['clips']}/{s['train']['hours']}h "
              f"({s['train']['speakers']}spk,{s['train']['prompts']}prompts) | "
              f"dev {s['dev']['clips']}/{s['dev']['hours']}h | "
              f"test {s['test']['clips']}/{s['test']['hours']}h "
              f"({s['test']['speakers']}spk,{s['test']['prompts']}prompts) | "
              f"discard {s['discard']['clips']}")
        for name in ("train", "dev", "test"):
            print(f"    {name:5s} sha256 {s[name]['sha256']}")
    leaks = {k: v["known_prompt_leak"] for k, v in report.items() if v["known_prompt_leak"]}
    if leaks:
        print(f"\nREPRODUCED KNOWN DEFECT (partition_key=sentence_id): {leaks}")
    else:
        print("\nALL PROMPT AND SPEAKER ASSERTIONS PASSED "
              "(assertions are on normalised text)")
    print(json.dumps({"summary_sha256": hashlib.sha256(
        json.dumps(report, sort_keys=True).encode()).hexdigest()}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
