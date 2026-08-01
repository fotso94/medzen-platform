"""Language-scoped in-process validation for the corrected B4 campaign.

Base and candidate arms use the same verified weights, runtime, generation
contract, manifests, normalizers, and scoring function.  Reports contain
checksums and numeric measurements only; transcripts are never persisted.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from pipeline import orchestrate
from pipeline.generation import expected_prompt, require_short_form
from pipeline.languages import LANG_TOKEN


ROOT = Path(__file__).resolve().parent.parent
FROZEN = ROOT / "platform/evidence/VAL-2026-001-frozen-validation-sets.json"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def frozen_validation() -> tuple[dict, str]:
    raw = FROZEN.read_bytes()
    doc = json.loads(raw)
    if tuple(doc["sets"]) != orchestrate.ALL_VALIDATION_LANGUAGES:
        raise SystemExit(
            "REFUSING: VAL-2026-001 no longer contains the complete frozen "
            "language set in its recorded order")
    if doc.get("total_rows") != sum(s["rows"] for s in doc["sets"].values()):
        raise SystemExit("REFUSING: VAL-2026-001 row total is inconsistent")
    return doc, hashlib.sha256(raw).hexdigest()


def adapter_sha256(adapter_dir: Path) -> str:
    path = Path(adapter_dir) / "adapter_model.safetensors"
    if not path.is_file():
        raise SystemExit(
            f"REFUSING: adapter has no adapter_model.safetensors at {path}")
    return sha256_file(path)


class ValidationRuntime:
    """Cache frozen inputs, then score base or one saved LoRA artifact."""

    def __init__(self, cli: Any, descriptor: dict, cache: Path,
                 device: str | None = None,
                 languages: tuple[str, ...] | None = None,
                 manifest_set: dict[str, dict] | None = None,
                 validation_record_sha256: str | None = None):
        import torch
        from scripts.evaluate_candidate import require_cuda

        self.cli = cli
        self.descriptor = descriptor
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.device = device or (
            "cuda" if torch.cuda.is_available()
            else "mps" if torch.backends.mps.is_available() else "cpu")
        require_cuda(self.device)
        authorised = tuple(descriptor.get(
            "validation_languages", orchestrate.VALIDATION_LANGUAGES))
        self.languages = tuple(languages or authorised)
        custom_holdout = manifest_set is not None
        if (not self.languages or len(set(self.languages)) != len(self.languages)
                or any(language not in orchestrate.ALL_VALIDATION_LANGUAGES
                       for language in self.languages)):
            raise SystemExit(
                "REFUSING: validation language subset is empty, duplicated, "
                "or outside VAL-2026-001")
        if self.languages != authorised and not (
                custom_holdout and descriptor.get("stage") == "artifactize"
                and self.languages == ("lingala",)):
            raise SystemExit(
                f"REFUSING: runtime validation languages {self.languages} "
                f"differ from descriptor-authorised {authorised}")
        if custom_holdout:
            if set(manifest_set) != {"lingala"}:
                raise SystemExit(
                    "REFUSING: the post-selection holdout must contain only Lingala")
            info = manifest_set["lingala"]
            if (info.get("key") != descriptor.get("holdout_manifest_key")
                    or info.get("manifest_sha256") !=
                    descriptor.get("holdout_manifest_sha256")):
                raise SystemExit(
                    "REFUSING: holdout manifest differs from the descriptor")
            if validation_record_sha256 != descriptor.get(
                    "holdout_evidence_sha256"):
                raise SystemExit(
                    "REFUSING: holdout evidence hash differs from the descriptor")
            self.frozen = {"sets": manifest_set,
                           "total_rows": sum(i["rows"] for i in manifest_set.values())}
            self.frozen_sha = validation_record_sha256
        else:
            self.frozen, self.frozen_sha = frozen_validation()
        if (not custom_holdout
                and self.frozen_sha != descriptor["validation_manifest_sha256"]):
            raise SystemExit(
                f"REFUSING: VAL-2026-001 hashes {self.frozen_sha[:16]}, "
                "descriptor authorises "
                f"{descriptor['validation_manifest_sha256'][:16]}")
        self._loaded: dict[str, tuple[list[dict], list[tuple[Any, int]]]] = {}
        self.base_dir = (
            Path(os.environ.get("MEDZEN_MODEL_DIR", "/tmp/medzen-base"))
            / "whisper-large-v3"
            / "06f233fe06e710322aca913c1bc4249a0d71fce1"
        )
        self.processor = None
        self.base_manifest = None
        self.base_manifest_sha = None

    def ensure_prepared(self) -> None:
        """Populate the verified model/processor/input cache exactly once.

        Adapter smoke checks can run before the full base evaluation.  They
        must not assume that a previous evaluation happened to populate this
        cache as a side effect.
        """
        if not self._loaded:
            self.prepare()
        if self.processor is None or not self._loaded:
            raise SystemExit(
                "REFUSING: validation inputs were not prepared for the "
                "saved-adapter smoke test")

    def prepare(self) -> None:
        from scripts.evaluate_candidate import ensure_base, load_audio, load_eval_pinned

        self.base_manifest, self.base_manifest_sha = ensure_base(
            self.cli, self.base_dir)
        if self.base_manifest_sha != self.descriptor["base_manifest_sha256"]:
            raise SystemExit(
                f"REFUSING: base manifest hashes "
                f"{self.base_manifest_sha[:16]}, descriptor authorises "
                f"{self.descriptor['base_manifest_sha256'][:16]}")

        from transformers import WhisperProcessor
        self.processor = WhisperProcessor.from_pretrained(str(self.base_dir))
        for language in self.languages:
            info = self.frozen["sets"][language]
            parts = info["key"].split("/")
            task, version = parts[-3], parts[-2]
            rows, got = load_eval_pinned(
                self.cli, language, task, version,
                info["manifest_sha256"])
            if len(rows) != info["rows"]:
                raise SystemExit(
                    f"REFUSING: {language} has {len(rows)} rows, frozen record "
                    f"declares {info['rows']}")
            require_short_form(rows)
            audios = [
                load_audio(self.cli, row, self.cache / "audio")
                for row in rows
            ]
            self._loaded[language] = (rows, audios)

    def _fresh_base(self):
        import torch
        from transformers import WhisperForConditionalGeneration

        dtype = torch.float16 if self.device == "cuda" else torch.float32
        return WhisperForConditionalGeneration.from_pretrained(
            str(self.base_dir), dtype=dtype).to(self.device).eval()

    def _score(self, model) -> dict:
        from scripts.evaluate_candidate import score_arm

        per_language = {}
        for language in self.languages:
            rows, audios = self._loaded[language]
            token = LANG_TOKEN[language]
            prompt = expected_prompt(self.processor, token)
            per_language[language] = score_arm(
                model, self.processor, rows, audios, language,
                self.device, token, prompt)
        return per_language

    def summary(self, per_language: dict) -> dict:
        return {
            "wer": {l: per_language[l]["wer"]
                    for l in self.languages},
            "cer": {l: per_language[l]["cer"]
                    for l in self.languages},
            "eos_rate": {l: per_language[l]["eos_rate"]
                         for l in self.languages},
            "cap_hit_rate": {l: per_language[l]["cap_hit_rate"]
                             for l in self.languages},
            "generated_tokens_median": {
                l: per_language[l]["generated_tokens"]["median"]
                for l in self.languages},
            "generated_tokens_max": {
                l: per_language[l]["generated_tokens"]["max"]
                for l in self.languages},
        }

    def _record(self, arm: str, per_language: dict,
                adapter_sha: str | None = None) -> dict:
        import torch
        import transformers
        import peft

        return {
            "record": "B4-FROZEN-VALIDATION",
            "recorded_utc": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "purpose": "training_system_validation",
            "promotable": False,
            "arm": arm,
            "stage_descriptor_sha256":
                __import__("pipeline.stage_descriptor",
                           fromlist=["descriptor_hash"])
                .descriptor_hash(self.descriptor),
            "code_git_sha": self.descriptor["git_sha"],
            "image_digest": self.descriptor["image_digest"],
            "code_tar_sha256": self.descriptor["bundle_tar_sha256"],
            "language_scope_sha256":
                self.descriptor["language_scope_sha256"],
            "training_languages": self.descriptor["training_languages"],
            "validation_languages": list(self.languages),
            "base_manifest_sha256": self.base_manifest_sha,
            "validation_record_sha256": self.frozen_sha,
            "validation_manifests": {
                l: self.frozen["sets"][l]["manifest_sha256"]
                for l in self.languages
            },
            "adapter_sha256": adapter_sha,
            "device": self.device,
            "torch": torch.__version__,
            "transformers": transformers.__version__,
            "peft": peft.__version__,
            "per_language": per_language,
            "summary": self.summary(per_language),
            "content_policy": (
                "no transcript is printed or stored; per-utterance rows carry "
                "audio checksums and numeric measurements only"),
        }

    def evaluate_base(self, out: Path) -> dict:
        from pipeline import orchestrate
        from scripts.evaluate_candidate import preflight_contract

        self.ensure_prepared()
        model = self._fresh_base()
        first_language = self.languages[0]
        _, audios = self._loaded[first_language]
        prompt = expected_prompt(self.processor, LANG_TOKEN[first_language])
        preflight = preflight_contract(
            model, self.processor, audios[0][0], audios[0][1],
            self.device, LANG_TOKEN[first_language], prompt)
        per_language = self._score(model)
        record = self._record("base", per_language)
        record["generation_contract_preflight"] = preflight
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(
            (json.dumps(record, indent=2, sort_keys=True) + "\n").encode())

        normalization = {
            l: per_language[l]["normalization_version"]
            for l in self.languages
        }
        manifests = {
            l: self.frozen["sets"][l]["manifest_sha256"]
            for l in self.languages
        }
        return {
            **self.summary(per_language),
            "artifact_path": str(out),
            "artifact_sha256": sha256_file(out),
            "base_arm_key": orchestrate.base_arm_key(
                self.descriptor["image_digest"],
                self.descriptor["generation_config_fingerprint"],
                self.descriptor["evaluator_sha256"],
                manifests, normalization),
        }

    def evaluate_adapter(self, adapter_dir: Path, out: Path,
                         expected_adapter_sha256: str | None = None) -> dict:
        from peft import PeftModel

        self.ensure_prepared()
        got = adapter_sha256(adapter_dir)
        if expected_adapter_sha256 is not None and got != expected_adapter_sha256:
            raise SystemExit(
                f"REFUSING: saved adapter hashes {got[:16]}, expected "
                f"{expected_adapter_sha256[:16]}")
        base = self._fresh_base()
        model = PeftModel.from_pretrained(
            base, str(adapter_dir), is_trainable=False).to(self.device).eval()
        per_language = self._score(model)
        record = self._record("candidate", per_language, adapter_sha=got)
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(
            (json.dumps(record, indent=2, sort_keys=True) + "\n").encode())
        return {
            **self.summary(per_language),
            "adapter_sha256": got,
            "artifact_path": str(out),
            "artifact_sha256": sha256_file(out),
        }
