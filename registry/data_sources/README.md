# Green-bucket data sources — cards & index

Campaign **green-bucket-aggregation-2026-08**. Machine-readable truth lives in
[`green_bucket_inventory.yaml`](green_bucket_inventory.yaml); this file is the
human-readable card set. Verified 2026-08-05 (HF account `fotso92`).

**Tiers** — `permissive` (CC0 / CC-BY-4.0, commercial OK) · `sharealike`
(CC-BY-SA-4.0, owner-legal-approved, stored/manifested separately) · `eval_only`
(never in a training manifest). Every ingested row carries `license_tier`.

| Source | Tier | In-scope langs | Purpose | Adapter | Access |
|---|---|---|---|---|---|
| Meta Omnilingual | permissive | akan, fula | train | `meta_omnilingual` | HF token (open) |
| WAXAL / Univ. Ghana | permissive | akan, ewe | train | `waxalnlp` (existing) | HF token (open) |
| WAXAL / Makerere + Umuganda | sharealike | acholi, luganda, lingala, amharic, oromo, shona, fula | train | `waxalnlp` (existing) | HF token (open) |
| Kallaama | permissive | wolof, fula | train | `kallaama` | direct download |
| Common Voice 17.0 | permissive | swahili, hausa, yoruba, igbo, luganda, amharic | train | `common_voice` | HF token (open) |
| FLEURS | eval_only | 13 langs | eval | `fleurs` | HF token (open) |
| AfriSpeech-200 | eval_only | accented EN (clinical) | eval | *(pending)* | HF, maybe gated |

---

## Meta Omnilingual ASR Corpus
- **Repo/pin** `facebook/omnilingual-asr-corpus @ 8648ba894637` · native parquet
- **Licence** CC-BY-4.0 → permissive · commercial OK, attribution required
- **Coverage** akan `fat_Latn` (~1,197 clips, ~1–3 h) · fula `fuf/fuh/fuv/fui/fuc/fuq/fue_Latn` (~12k clips, ~15–25 h). Row-level `dialect` = variety code.
- **Why small** underserved-language corpus; deliberately omits high-resource languages, so overlaps our scope only on Akan + Fula.
- **Access** open for `fotso92`, HF token only. **Smoke test required before bulk.**

## WAXAL / WaxalNLP
- **Repo/pin** `google/WaxalNLP @ e0a62aaebc61` · adapter already exists
- **Licence varies BY PROVIDER:**
  - Univ. Ghana (`aka_asr`, `ewe_asr`) → CC-BY-4.0 → **permissive**
  - Makerere (`ach_asr`, `lug_asr`) + Digital Umuganda (`ful/lin/amh/orm/sna_asr`) → CC-BY-SA-4.0 → **sharealike** (owner-legal-approved; isolated)
- **Hours** at_fetch — datasets-server can't index WAXAL (load-script); read Parquet footers at ingest start. Aggregate ~1,250 h / 19 langs.

## Kallaama
- **Source/pin** OpenSLR-151 / Zenodo-10892569 · **not on HF** (direct download)
- **Licence** CC-BY-4.0 → permissive
- **Coverage** wolof, fula(pulaar) — spontaneous agricultural radio speech, ~40–125 h total (at_fetch). Timestamped transcriptions → Phase-2 extract normalises to `<root>/<lang>/segments.tsv`.
- **Access** no account. `MEDZEN_KALLAAMA_DIR` points the adapter at the extracted tree.

## Common Voice 17.0
- **Repo/pin** `mozilla-foundation/common_voice_17_0 @ 11dc88355e89`
- **Licence** CC0 → permissive
- **Coverage** swahili, hausa, yoruba, igbo, luganda, amharic (per-language hours at_fetch). **No Cameroon Pidgin** (CV 25.0 / Data Collective — deferred).
- **Access** not gated, but load-script (needs `datasets` + `trust_remote_code`). Run bulk ingest on Linux (macOS/arm64 Arrow streaming bug). Possible one-time HF "accept" click — confirm at fetch.

## FLEURS
- **Repo/pin** `google/fleurs` main `70bb2e84b976`, read via parquet mirror `refs/convert/parquet @ 168de341b3db`
- **Licence** CC-BY-4.0, **reserved as eval_only** (truthful licence, held-out use)
- **Coverage** 13 in-scope langs @ ~12 h each (verified). Absent in-scope: akan, ewe, acholi, pidgin.
- **Access** HF token only. Ingest with `--force-eval` → lands under `eval/`.

## AfriSpeech-200 *(eval-only, adapter pending)*
- **Repo** `intronhealth/afrispeech-200` · CC-BY-NC-SA → **eval_only** (NC forbids commercial training)
- **Use** frozen **clinical** accented-English benchmark for the medical domain. ~200 h. Adapter deferred to when we wire the eval harness.

---

## Deferred
- **Cameroon Pidgin (Kamtok)** — only source is CV 25.0 on Mozilla Data Collective (signup failing server-side). Alt: Sheffield/Sussex spoken corpus (~240k words, academic licence — verify). Owner action.
