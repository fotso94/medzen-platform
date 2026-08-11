"""SOREVA adapter — CC-BY-4.0 (permissive tier), EVAL-ONLY by purpose.

SOREVA (Small Out-of-domain Resource for Various African languages,
OlameMend/soreva) is a Goethe-Institut collection: ~150 read clips per
language across 47 configs, ~403 MB total, test split only, male voices only.
It is an out-of-domain EVALUATION set — never training data. Ingest whole
configs to eval/<language>/asr/soreva-v1/ with MEDZEN_EVAL_ONLY=1.

The first real Cameroonian-language eval material in the project (Basaa,
Bamun, Medumba, Duala, Ewondo, Ghomálá', Cameroon Pidgin, ...). Owner
directive 2026-08-11.

Layout per config: data/<cfg>/test.tsv (wav\tverbatim\tnormalized\tgender,
no header) + data/<cfg>/audio/test.tar.gz. Read via hf_hub_download — no
`datasets` script loading needed.
"""
from __future__ import annotations

import csv
import hashlib
import io
import tarfile
from typing import Iterator

from . import green_common as gc
from .base import TARGET_SR, SourceSpec, build_record, usable

REPO = "OlameMend/soreva"
REVISION = "1897cf9927c1afc354efa6192943f6d4783bae93"   # pinned 2026-08-11
LICENSE_POLICY = "cc_by_4_0"                             # -> permissive tier

# our canonical language name -> SOREVA config. Names follow the existing
# registry where the language exists (hausa, ewe, pidgin, ...); new languages
# use lowercase ascii canonical names. Verified against the dataset's README
# and ISO 639-3 2026-08-11. NOT in the dataset despite the user's request:
# Bafut (bfd tagged, no data files), "Mka", "Nda".
CONFIGS = {
    # mainstream (second, out-of-domain eval alongside fleurs-v1)
    "hausa": "ha_ng", "yoruba": "yor_ng", "igbo": "ibo_ng",
    "lingala": "lin_cd", "swahili": "swa_ke", "wolof": "wol_sn",
    "ewe": "ewe_tg",
    # Cameroon + requested low-resource
    "bafia": "ksf_cm", "baka": "bkc_cm", "bakoko": "bkh_cm",
    "bamun": "bax_cm", "basaa": "bas_cm", "duala": "dua_cm",
    "ejagham": "etu_cm", "eton": "eto_cm", "ewondo": "ewo_cm",
    "fefe": "fmp_cm", "fulfulde": "fub_cm", "gbaya": "gya_cf",
    "ghomala": "bbj_cm", "isu": "isu_cm", "kera": "ker_td",
    "kom": "bkm_Kom", "kwasio": "kqs_cm", "lamso": "lns_cm",
    "maka": "mcp_cm", "malagasy": "mlg_cm", "medumba": "byv_cm",
    "mundang": "mua_cm", "ngiemboon": "nnh_cm", "ngombala": "nla_cm",
    "nomaande": "lem_cm", "nugunu": "yas_cm", "pidgin": "pcm_cm",
    "pulaar": "fuc_sn", "sepedi": "nso_za", "yambeta": "yat_cm",
    "yangben": "yav_cm", "yemba": "ybb_cm",
}


class SorevaAdapter:
    name = "soreva"

    def __init__(self, language: str, task: str | None = None,
                 revision: str = REVISION, version: str = "soreva-v1"):
        if language not in CONFIGS:
            raise ValueError(
                f"SOREVA has no in-scope config for '{language}'. "
                f"Available: {sorted(CONFIGS)}")
        if task not in (None, "asr"):
            raise ValueError("SOREVA is ASR/TTS-eval only; task must be asr")
        self.language = language
        self.task = "asr"
        self.cfg = CONFIGS[language]
        self.revision = revision
        self.version = version
        self.config = f"soreva_{self.cfg}"
        self.spec = SourceSpec(
            source_id="soreva",
            dataset_release=f"{REPO}@{revision}#{self.cfg}",
            license_policy=LICENSE_POLICY,
            allowed_use=["asr_eval", "tts_eval"],
            consent_id="dataset-level:soreva_goethe_cc_by_4_0",
        )
        self.tier = gc.tier_for(LICENSE_POLICY)    # -> permissive

    def items(self, limit: int | None = None) -> Iterator[dict]:
        import librosa
        import soundfile as sf
        from huggingface_hub import hf_hub_download

        tsv = hf_hub_download(REPO, f"data/{self.cfg}/test.tsv",
                              repo_type="dataset", revision=self.revision)
        tar = hf_hub_download(REPO, f"data/{self.cfg}/audio/test.tar.gz",
                              repo_type="dataset", revision=self.revision)

        rows: dict[str, tuple[str, str, str]] = {}
        with open(tsv, encoding="utf-8") as f:
            for cols in csv.reader(f, delimiter="\t"):
                if len(cols) >= 2:
                    rows[cols[0].rsplit("/", 1)[-1]] = (
                        cols[1].strip(),
                        (cols[2].strip() if len(cols) > 2 else ""),
                        (cols[3].strip() if len(cols) > 3 else ""))

        base = f"{self.language}/{self.task}/{self.config}"
        sd = gc.spill_dir()
        spill = __import__("pathlib").Path(sd.name)
        self._spill_dir = sd
        produced = 0
        spk = f"{self.cfg}_male_0"          # single male voice per config
        with tarfile.open(tar, "r:gz") as tf:
            for member in tf:
                if limit and produced >= limit:
                    return
                if not member.isfile():
                    continue
                fname = member.name.rsplit("/", 1)[-1]
                meta = rows.get(fname)
                if meta is None or not meta[0]:
                    continue
                raw = tf.extractfile(member).read()
                try:
                    arr, sr = sf.read(io.BytesIO(raw), dtype="float32",
                                      always_2d=False)
                except Exception:
                    continue
                if getattr(arr, "ndim", 1) > 1:
                    arr = arr.mean(axis=1)
                if sr != TARGET_SR:
                    arr = librosa.resample(arr, orig_sr=sr, target_sr=TARGET_SR)
                dur = len(arr) / TARGET_SR
                if not usable(dur, meta[0]):
                    continue
                buf = io.BytesIO()
                sf.write(buf, arr, TARGET_SR, format="WAV", subtype="PCM_16")
                wav = buf.getvalue()
                stem = (fname.rsplit(".", 1)[0]
                        + "_" + hashlib.sha256(wav).hexdigest()[:12])
                rp = spill / f"{stem}.raw"; wp = spill / f"{stem}.wav"
                rp.write_bytes(raw); wp.write_bytes(wav)
                rec = build_record(
                    audio_uri=f"s3://medzen-speech/eval/{base}/{self.version}/audio/{stem}.wav",
                    audio_sha256=hashlib.sha256(wav).hexdigest(),
                    duration_s=dur, sample_rate=TARGET_SR, channels=1,
                    text_verbatim=meta[0], language=self.language,
                    speaker_id=spk, session_id=self.cfg,
                    split="test", spec=self.spec,
                    split_strategy="speaker_disjoint",
                    gender=gc.norm_gender(meta[2] or "male"), domain="asr",
                    license_tier=self.tier, dialect=self.cfg,
                    raw_filepath=f"s3://medzen-speech/raw/{base}/{stem}.wav",
                    raw_checksum_sha256=hashlib.sha256(raw).hexdigest(),
                )
                yield {"record": rec, "raw_path": rp, "wav_path": wp,
                       "raw_ext": "wav", "stem": stem}
                produced += 1

    def rows(self, language: str | None = None,
             limit: int | None = None) -> Iterator[dict]:
        for item in self.items(limit=limit):
            yield item["record"]
