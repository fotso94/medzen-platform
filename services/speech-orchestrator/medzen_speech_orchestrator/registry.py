from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ALIAS_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
CODE_RE = re.compile(r"^[a-z]{2,3}$")
ROOT_RE = re.compile(r"^/medzen/registry/test/b6/([0-9a-f]{64})$")
CLASSIFICATION = "B6_3_LOCAL_SYNTHETIC_ONLY"
CONTRACT_VERSION = "medzen.speech.v1"


class RegistryRefusal(RuntimeError):
    """The registry snapshot is missing, ambiguous or hash-invalid."""


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


@dataclass(frozen=True)
class Parameter:
    Name: str
    Type: str
    Value: str
    Version: int


class ParameterStore(Protocol):
    def get_parameter(self, name: str) -> Parameter: ...
    def get_parameters_by_path(self, path: str) -> tuple[Parameter, ...]: ...


class LocalParameterStore:
    """Implements the two SSM reads used by routing over an SSM-shaped fixture."""

    def __init__(self, fixture: Path):
        try:
            value: Any = json.loads(fixture.read_bytes())
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RegistryRefusal("local registry fixture is missing or malformed") from exc
        if not isinstance(value, dict) or set(value) != {"schema_version", "parameters"}:
            raise RegistryRefusal("local registry fixture fields are incomplete or unknown")
        if value["schema_version"] != 1 or not isinstance(value["parameters"], list):
            raise RegistryRefusal("local registry fixture schema is unsupported")
        parameters: dict[str, Parameter] = {}
        for raw in value["parameters"]:
            if not isinstance(raw, dict) or set(raw) != {"Name", "Type", "Value", "Version"}:
                raise RegistryRefusal("local registry parameter is malformed")
            name = raw["Name"]
            if (
                not isinstance(name, str)
                or not name.startswith("/medzen/registry/test/b6/")
                or name in parameters
                or raw["Type"] != "SecureString"
                or not isinstance(raw["Value"], str)
                or isinstance(raw["Version"], bool)
                or not isinstance(raw["Version"], int)
                or raw["Version"] < 1
            ):
                raise RegistryRefusal("local registry parameter identity is invalid")
            try:
                decoded = json.loads(raw["Value"])
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise RegistryRefusal("local registry parameter value is malformed") from exc
            if raw["Value"].encode("utf-8") != canonical_json(decoded):
                raise RegistryRefusal("local registry parameter value is not canonical JSON")
            parameters[name] = Parameter(**raw)
        if not parameters:
            raise RegistryRefusal("local registry fixture is empty")
        self._parameters = parameters

    def get_parameter(self, name: str) -> Parameter:
        try:
            return self._parameters[name]
        except KeyError as exc:
            raise RegistryRefusal("required registry parameter is missing") from exc

    def get_parameters_by_path(self, path: str) -> tuple[Parameter, ...]:
        prefix = path.rstrip("/") + "/"
        return tuple(
            self._parameters[name]
            for name in sorted(self._parameters)
            if name.startswith(prefix)
        )


@dataclass(frozen=True)
class RegistryRoute:
    alias: str
    response_code: str
    accepted_codes: tuple[str, ...]
    asr_backend: str
    asr_model_version: str
    asr_fixture_sha256: str
    rag_alias: str
    rag_snapshot_sha256: str
    rag_query_language: str
    llm_model_version: str
    llm_policy_id: str
    tts_backend: str
    tts_model_version: None
    registry_snapshot: str

    @property
    def model_versions(self) -> dict[str, str | None]:
        return {
            "asr": self.asr_model_version,
            "registry_snapshot": self.registry_snapshot,
            "llm": None,
            "rag": None,
            "tts": self.tts_model_version,
        }


def _object(parameter: Parameter) -> dict[str, Any]:
    try:
        value = json.loads(parameter.Value)
    except json.JSONDecodeError as exc:
        raise RegistryRefusal("registry parameter value is malformed") from exc
    if not isinstance(value, dict):
        raise RegistryRefusal("registry parameter value must be an object")
    return value


def _exact(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise RegistryRefusal(f"{label} fields are incomplete or unknown")
    return value


class RegistryRouter:
    def __init__(self, store: ParameterStore, root: str):
        match = ROOT_RE.fullmatch(root.rstrip("/"))
        if match is None:
            raise RegistryRefusal("registry root is not a versioned B6 test snapshot")
        self.root = root.rstrip("/")
        self.snapshot_sha256 = match.group(1)
        self.registry_snapshot = f"b6-test:{self.snapshot_sha256}"
        self._routes_by_alias, self._aliases_by_code, self.default_language = self._load(store)

    def _load(
        self, store: ParameterStore
    ) -> tuple[dict[str, RegistryRoute], dict[str, str], str]:
        all_parameters = store.get_parameters_by_path(self.root)
        if not all_parameters:
            raise RegistryRefusal("registry snapshot path is empty")
        actual = {parameter.Name: parameter for parameter in all_parameters}
        if len(actual) != len(all_parameters):
            raise RegistryRefusal("registry snapshot contains duplicate parameters")
        manifest_name = f"{self.root}/_manifest"
        manifest = _exact(
            _object(store.get_parameter(manifest_name)),
            {
                "schema_version", "classification", "snapshot_sha256",
                "snapshot_material_sha256", "parameter_value_sha256"
            },
            "registry manifest",
        )
        if (
            manifest["schema_version"] != 1
            or manifest["classification"] != CLASSIFICATION
            or manifest["snapshot_sha256"] != self.snapshot_sha256
            or manifest["snapshot_material_sha256"] != self.snapshot_sha256
        ):
            raise RegistryRefusal("registry manifest identity is invalid")
        hashes = manifest["parameter_value_sha256"]
        if not isinstance(hashes, dict) or not hashes:
            raise RegistryRefusal("registry manifest parameter hashes are missing")
        expected_names = {manifest_name}
        decoded: dict[str, dict[str, Any]] = {}
        for relative, expected_hash in hashes.items():
            if (
                not isinstance(relative, str)
                or relative.startswith("/")
                or ".." in relative.split("/")
                or not isinstance(expected_hash, str)
                or SHA256_RE.fullmatch(expected_hash) is None
            ):
                raise RegistryRefusal("registry manifest contains an invalid parameter binding")
            name = f"{self.root}/{relative}"
            expected_names.add(name)
            parameter = store.get_parameter(name)
            if hashlib.sha256(parameter.Value.encode("utf-8")).hexdigest() != expected_hash:
                raise RegistryRefusal("registry parameter value hash mismatch")
            decoded[relative] = _object(parameter)
        if set(actual) != expected_names:
            raise RegistryRefusal("registry snapshot contains missing or unexpected parameters")
        index = _exact(
            decoded.get("index"),
            {"schema_version", "default_language", "languages"},
            "registry index",
        )
        if index["schema_version"] != 1 or not isinstance(index["languages"], list):
            raise RegistryRefusal("registry index schema is unsupported")
        routes: dict[str, RegistryRoute] = {}
        raw_routes: dict[str, dict[str, Any]] = {}
        code_index: dict[str, str] = {}
        for entry in index["languages"]:
            _exact(entry, {"alias", "codes", "route_parameter"}, "registry language")
            alias = entry["alias"]
            codes = entry["codes"]
            relative = entry["route_parameter"]
            if (
                not isinstance(alias, str)
                or ALIAS_RE.fullmatch(alias) is None
                or alias in routes
                or not isinstance(codes, list)
                or not codes
                or not all(isinstance(code, str) and CODE_RE.fullmatch(code) for code in codes)
                or len(set(codes)) != len(codes)
                or relative != f"routes/{alias}"
                or relative not in decoded
            ):
                raise RegistryRefusal("registry language route identity is invalid")
            raw_route = decoded[relative]
            raw_routes[alias] = raw_route
            route = self._route(alias, tuple(codes), raw_route)
            routes[alias] = route
            for code in (alias, *codes):
                if code in code_index:
                    raise RegistryRefusal("registry language code is ambiguous")
                code_index[code] = alias
        default = index["default_language"]
        if not isinstance(default, str) or default not in routes:
            raise RegistryRefusal("registry default language is missing")
        material = {
            "schema_version": 1,
            "classification": CLASSIFICATION,
            "index": index,
            "routes": raw_routes,
        }
        if hashlib.sha256(canonical_json(material)).hexdigest() != self.snapshot_sha256:
            raise RegistryRefusal("registry snapshot content hash mismatch")
        return routes, code_index, default

    def _route(
        self, alias: str, codes: tuple[str, ...], value: Any
    ) -> RegistryRoute:
        route = _exact(
            value,
            {
                "schema_version", "classification", "language", "contract_version",
                "asr", "rag", "llm", "tts"
            },
            "registry route",
        )
        language = _exact(
            route["language"], {"alias", "response_code", "accepted_codes"},
            "registry route language"
        )
        asr = _exact(
            route["asr"], {"backend", "model_version", "fixture_sha256"},
            "registry ASR route"
        )
        rag = _exact(
            route["rag"], {"alias", "snapshot_sha256", "query_language"},
            "registry RAG route"
        )
        llm = _exact(
            route["llm"], {"model_version", "policy_id"}, "registry LLM route"
        )
        tts = _exact(
            route["tts"], {"backend", "model_version"}, "registry TTS route"
        )
        if (
            route["schema_version"] != 1
            or route["classification"] != CLASSIFICATION
            or route["contract_version"] != CONTRACT_VERSION
            or language["alias"] != alias
            or language["accepted_codes"] != list(codes)
            or not isinstance(language["response_code"], str)
            or CODE_RE.fullmatch(language["response_code"]) is None
            or asr["backend"] != "local_synthetic_fixture"
            or not isinstance(asr["model_version"], str)
            or not asr["model_version"]
            or not isinstance(asr["fixture_sha256"], str)
            or SHA256_RE.fullmatch(asr["fixture_sha256"]) is None
            or rag["alias"] != "current"
            or not isinstance(rag["snapshot_sha256"], str)
            or SHA256_RE.fullmatch(rag["snapshot_sha256"]) is None
            or not isinstance(rag["query_language"], str)
            or CODE_RE.fullmatch(rag["query_language"]) is None
            or llm["model_version"] != "fake-bedrock-local-v1"
            or llm["policy_id"] != f"{alias}-medzen-v1"
            or tts != {"backend": "text_only", "model_version": None}
        ):
            raise RegistryRefusal("registry route is unsafe or inconsistent")
        return RegistryRoute(
            alias=alias,
            response_code=language["response_code"],
            accepted_codes=codes,
            asr_backend=asr["backend"],
            asr_model_version=asr["model_version"],
            asr_fixture_sha256=asr["fixture_sha256"],
            rag_alias=rag["alias"],
            rag_snapshot_sha256=rag["snapshot_sha256"],
            rag_query_language=rag["query_language"],
            llm_model_version=llm["model_version"],
            llm_policy_id=llm["policy_id"],
            tts_backend=tts["backend"],
            tts_model_version=None,
            registry_snapshot=self.registry_snapshot,
        )

    def resolve(self, language: str | None) -> RegistryRoute:
        key = self.default_language if language is None else language.casefold()
        alias = self._aliases_by_code.get(key)
        if alias is None:
            raise RegistryRefusal("language is not present in the bound registry snapshot")
        return self._routes_by_alias[alias]
