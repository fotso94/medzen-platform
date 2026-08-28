#!/usr/bin/env python3
"""Mint the NONPROD_REAL_PROVIDER_V2 registry snapshot and validate it
through the REAL RegistryRouter before any AWS write."""
import hashlib, json, sys
sys.path.insert(0, "services/speech-orchestrator")
from medzen_speech_orchestrator.registry import (
    canonical_json, DEPLOYED_ENDPOINTS, V2_DEPLOYED_CONTRACTS,
    V2_CLASSIFICATION, V2_CONTRACT_VERSION, RegistryRouter, Parameter)

TREE = "34ca18bab2f7c6f34e67c0598db416438f0bada15ab004f0b58e3dbafa3c0ca6"
ASR_VERSION = f"omniasr_ctc_1b:{TREE[:12]}"
REPORTED = "omniasr-nonprod:74c6e2e37b1696527f0d60a4d314da525012814a28ffde0b23d2908cea7934ad"
RAG_SHA = "02a266a9912323198e49015c991c24cce027fd308d774710dfb314c3e260d3a2"
LLM = "bedrock:eu.anthropic.claude-haiku-4-5-20251001-v1:0"

# alias -> (response_code, accepted_codes)
# ewe MUST use ["ee"]: its alias equals its ISO-3 code and the router
# refuses duplicate entries in the (alias, *codes) index.
LANGS = {
 "english":     ("en",  ["en"]),
 "ewe":         ("ewe", ["ee"]),
 "french":      ("fr",  ["fr"]),
 "kinyarwanda": ("kin", ["kin"]),
 "lingala":     ("lin", ["lin"]),
 "pidgin":      ("pcm", ["pcm"]),
 "swahili":     ("swa", ["swa"]),
}
# approved voices after the 2026-08-28 import from the ECS dev registry:
# kinyarwanda (owner-supplied) + french, pidgin, swahili (owner-provisioned,
# imported from /medzen/tts/dev/voices). english/ewe/lingala have no voice
# with a Fish reference_id, so they stay text-only.
# +english (owner-supplied voice 6d7b6ebb, added 2026-08-28). ewe and
# lingala remain text-only: no Fish voice with a reference_id exists.
FISH_LANGS = {"kinyarwanda", "french", "pidgin", "swahili", "english"}
TTS = {a: ({"backend": "http_fish_v2", "model_version": "fish:s2.1-pro-free"}
           if a in FISH_LANGS else
           {"backend": "http_text_only_v1", "model_version": None})
       for a in LANGS}

routes = {}
for alias, (code, codes) in LANGS.items():
    routes[alias] = {
        "schema_version": 2,
        "classification": V2_CLASSIFICATION,
        "contract_version": V2_CONTRACT_VERSION,
        "language": {"alias": alias, "response_code": code,
                     "accepted_codes": list(codes)},
        "asr": {"backend": "http_cluster_v1", "model_version": ASR_VERSION,
                "artifact_tree_sha256": TREE,
                "reported_registry_snapshot": REPORTED},
        "rag": {"alias": "current", "snapshot_sha256": RAG_SHA,
                "query_language": code},
        "llm": {"model_version": LLM, "policy_id": f"{alias}-medzen-v1"},
        "tts": TTS[alias],
        "dependencies": {n: {"endpoint": DEPLOYED_ENDPOINTS[n],
                             "contract_id": V2_DEPLOYED_CONTRACTS[n][0],
                             "contract_sha256": V2_DEPLOYED_CONTRACTS[n][1]}
                         for n in DEPLOYED_ENDPOINTS},
    }
index = {"schema_version": 1, "default_language": "english",
         "languages": [{"alias": a, "codes": list(c),
                        "route_parameter": f"routes/{a}"}
                       for a, (_, c) in LANGS.items()]}
material = {"schema_version": 1, "classification": V2_CLASSIFICATION,
            "index": index, "routes": routes}
snap = hashlib.sha256(canonical_json(material)).hexdigest()
root = f"/medzen/registry/nonprod/b6v2/{snap}"
values = {"index": index}
values.update({f"routes/{a}": routes[a] for a in LANGS})
manifest = {"schema_version": 1, "classification": V2_CLASSIFICATION,
            "snapshot_sha256": snap, "snapshot_material_sha256": snap,
            "parameter_value_sha256": {
                rel: hashlib.sha256(canonical_json(o)).hexdigest()
                for rel, o in values.items()}}
params = {f"{root}/_manifest": canonical_json(manifest).decode()}
for rel, o in values.items():
    params[f"{root}/{rel}"] = canonical_json(o).decode()

# ---- validate through the REAL router (no AWS) ----
class Store:
    def __init__(self, p): self.p = p
    def get_parameter(self, name):
        return Parameter(Name=name, Type="SecureString", Value=self.p[name], Version=1)
    def get_parameters_by_path(self, path):
        pre = path.rstrip("/") + "/"
        return tuple(self.get_parameter(n) for n in sorted(self.p) if n.startswith(pre))
r = RegistryRouter(Store(params), root, expected_classification=V2_CLASSIFICATION)
print("ROUTER ACCEPTED the snapshot")
print("  root:", root)
print("  params:", len(params), "| aliases:", len(LANGS))
print("  resolve('kin') ->", r.resolve("kin").alias, "| tts:", r.resolve("kin").tts_model_version)
print("  resolve('ewe') ->", r.resolve("ewe").alias)
print("  registry_snapshot label:", r.registry_snapshot[:34])
for n, v in params.items():
    assert len(v.encode()) <= 4096, (n, len(v))
print("  all values <= 4096B: OK")
json.dump({"root": root, "snapshot": snap, "parameters": params},
          open(f"{sys.argv[1]}/v2_snapshot.json", "w"), indent=1)
print("  written -> v2_snapshot.json")
