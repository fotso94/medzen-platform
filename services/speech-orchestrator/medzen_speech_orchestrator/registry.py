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
ROOT_RE = re.compile(
    r"^/medzen/registry/(?:test/b6|nonprod/b6v2)/([0-9a-f]{64})$")
LOCAL_CLASSIFICATION = "B6_3_LOCAL_SYNTHETIC_ONLY"
DEPLOYED_CLASSIFICATION = "B6_6_SYNTHETIC_INTEGRATION_ONLY"
# B6v2 (Codex serving review): the REAL-provider nonprod namespace.
# Round 3: ONE classification string — the contract's, shared with
# loader_v2 — not a router-private spelling that matches nothing.
V2_CLASSIFICATION = "NONPROD_REAL_PROVIDER_V2"
V2_ROOT_PREFIX = "/medzen/registry/nonprod/b6v2/"
# real identities the v2 route may carry (in addition to the
# synthetic ones, so the composed stub chain stays testable)
V2_LLM_RE = re.compile(r"^(fake-bedrock-local-v1|bedrock:[a-z0-9.:-]+)$")
V2_TTS_RE = re.compile(r"^fish:s(1|2\.1-pro-free)$")
V2_ASR_RE = re.compile(r"^(v0|omniasr_ctc_1b:[0-9a-f]{12})$")
CONTRACT_VERSION = "medzen.speech.v1"
V2_CONTRACT_VERSION = "medzen.speech.v2"
DEPLOYED_ENDPOINTS = {
    "asr": "http://asr-runtime.medzen.svc.cluster.local:8081/internal/v1/transcriptions",
    "rag": "http://rag-index.medzen.svc.cluster.local:8083/internal/v1/retrievals",
    "llm": "http://llm-gateway.medzen.svc.cluster.local:8082/internal/v1/responses",
    "tts": "http://tts-gateway.medzen.svc.cluster.local:8080/internal/v1/syntheses",
}
DEPLOYED_CONTRACTS = {
    "asr": (
        "MEDZEN-SPEECH-CONTRACT-2026-001",
        "e544141a7ad894ac0b5d411c7d8a3b64767de40ca63de4b96afc579f6a244d0d",
    ),
    "rag": (
        "MEDZEN-SPEECH-CONTRACT-2026-001",
        "e544141a7ad894ac0b5d411c7d8a3b64767de40ca63de4b96afc579f6a244d0d",
    ),
    "llm": (
        "MEDZEN-LLM-CONTRACT-2026-001",
        "67ee016e205a287d74c415d457b4520f4ded5e6962d7e0d11551c715aaea581a",
    ),
    "tts": (
        "MEDZEN-TTS-CONTRACT-2026-001",
        "2576a46f535a42e9986220b003df136e2aef2001ecb51597df09b2d6f09956d8",
    ),
}
# B6v2 round 4 (Codex): v2 routes bound the SYNTHETIC v1 contract ids —
# a route claiming real providers while pinning the proof-only contract.
# Each sha is sha256 of the committed platform/contracts/<svc>-v2.yaml,
# which in turn pins every v2 schema file (same discipline as v1).
V2_DEPLOYED_CONTRACTS = {
    "asr": (
        "MEDZEN-SPEECH-CONTRACT-2026-002",
        "ec6bbc2f2b967f4e1742b1139213e638a99cb24622cff22997ca997c717545dc",
    ),
    "rag": (
        "MEDZEN-SPEECH-CONTRACT-2026-002",
        "ec6bbc2f2b967f4e1742b1139213e638a99cb24622cff22997ca997c717545dc",
    ),
    "llm": (
        "MEDZEN-LLM-CONTRACT-2026-002",
        "9e3989a11744772260c3e222a59c14b64c4cdb2e3085f1c573a675b00750c2c9",
    ),
    "tts": (
        "MEDZEN-TTS-CONTRACT-2026-002",
        "5be0b477e1bb2a2136c20562a36268cbd5a8823418c0ea5cff8c82c2e9343363",
    ),
}


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
                or not (name.startswith("/medzen/registry/test/b6/")
                        or name.startswith(V2_ROOT_PREFIX))
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


class SSMParameterStore:
    """Narrow deployed adapter for the two read-only registry operations."""

    def __init__(self, client: Any):
        self._client = client

    @staticmethod
    def _parameter(raw: Any) -> Parameter:
        if not isinstance(raw, dict):
            raise RegistryRefusal("SSM returned a malformed registry parameter")
        try:
            parameter = Parameter(
                Name=raw["Name"],
                Type=raw["Type"],
                Value=raw["Value"],
                Version=raw["Version"],
            )
        except (KeyError, TypeError) as exc:
            raise RegistryRefusal("SSM returned an incomplete registry parameter") from exc
        if (
            parameter.Type != "SecureString"
            or not isinstance(parameter.Name, str)
            or not isinstance(parameter.Value, str)
            or isinstance(parameter.Version, bool)
            or not isinstance(parameter.Version, int)
            or parameter.Version < 1
        ):
            raise RegistryRefusal("SSM registry parameter identity is invalid")
        return parameter

    def get_parameter(self, name: str) -> Parameter:
        try:
            response = self._client.get_parameter(Name=name, WithDecryption=True)
            return self._parameter(response["Parameter"])
        except RegistryRefusal:
            raise
        except Exception as exc:
            raise RegistryRefusal("required SSM registry parameter is unavailable") from exc

    def get_parameters_by_path(self, path: str) -> tuple[Parameter, ...]:
        parameters: dict[str, Parameter] = {}
        token: str | None = None
        try:
            while True:
                request: dict[str, Any] = {
                    "Path": path.rstrip("/"),
                    "Recursive": True,
                    "WithDecryption": True,
                    "MaxResults": 10,
                }
                if token is not None:
                    request["NextToken"] = token
                response = self._client.get_parameters_by_path(**request)
                for raw in response.get("Parameters", []):
                    parameter = self._parameter(raw)
                    if parameter.Name in parameters:
                        raise RegistryRefusal(
                            "SSM registry snapshot contains a duplicate parameter"
                        )
                    parameters[parameter.Name] = parameter
                if len(parameters) > 32:
                    raise RegistryRefusal("SSM registry snapshot exceeds the bounded size")
                token = response.get("NextToken")
                if token is None:
                    break
                if not isinstance(token, str) or not token:
                    raise RegistryRefusal("SSM registry pagination token is malformed")
        except RegistryRefusal:
            raise
        except Exception as exc:
            raise RegistryRefusal("SSM registry snapshot is unavailable") from exc
        return tuple(parameters[name] for name in sorted(parameters))


@dataclass(frozen=True)
class RegistryRoute:
    alias: str
    response_code: str
    accepted_codes: tuple[str, ...]
    asr_backend: str
    asr_model_version: str
    asr_fixture_sha256: str | None
    asr_artifact_tree_sha256: str | None
    asr_reported_registry_snapshot: str
    rag_alias: str
    rag_snapshot_sha256: str
    rag_query_language: str
    llm_model_version: str
    llm_policy_id: str
    tts_backend: str
    # v1 routes bind None (text-only proof); v2 routes may bind the
    # governed Fish identity ("fish:s1") — the orchestrator's version
    # fill must equal EXACTLY this value, never "any non-empty string"
    tts_model_version: str | None
    registry_snapshot: str
    classification: str
    dependency_endpoints: dict[str, str]

    @property
    def model_versions(self) -> dict[str, str | None]:
        # PRE-pipeline versions: llm/rag/tts stay None until their step
        # fills them. The route-BOUND tts identity (fish:s1) lives in
        # tts_model_version and may only enter via the orchestrator's
        # fill check after a real synthesis — reporting it here would
        # claim a synthesis that never happened (B6v2 round 3).
        return {
            "asr": self.asr_model_version,
            "registry_snapshot": self.registry_snapshot,
            "llm": None,
            "rag": None,
            "tts": None,
        }

    @property
    def expected_asr_versions(self) -> dict[str, str | None]:
        return {
            "asr": self.asr_model_version,
            "registry_snapshot": self.asr_reported_registry_snapshot,
            "llm": None,
            "rag": None,
            "tts": None,
        }

    def endpoint(self, dependency: str) -> str:
        try:
            return self.dependency_endpoints[dependency]
        except KeyError as exc:
            raise RegistryRefusal(
                f"registry route has no deployed {dependency} endpoint"
            ) from exc


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


def enforce_single_asr_digest(routes: dict) -> None:
    """ARCH-2026-001 (Codex review #8: a snapshot with divergent English and
    French ASR digests was ACCEPTED by the router): production serves
    exactly ONE multilingual ASR artifact, so every language route that
    binds an ASR artifact identity must bind the SAME one. Fails closed at
    snapshot load, not at request time."""
    digests: dict[str, set[str]] = {}
    for alias, route in routes.items():
        identity = getattr(route, "asr_artifact_tree_sha256", None)
        if identity:
            digests.setdefault(str(identity), set()).add(alias)
    if len(digests) > 1:
        summary = {k[:16]: sorted(v) for k, v in digests.items()}
        raise RegistryRefusal(
            f"registry binds MULTIPLE ASR artifact digests across languages "
            f"({summary}) — ARCH-2026-001 requires the one production "
            "artifact")


class RegistryRouter:
    def __init__(
        self,
        store: ParameterStore,
        root: str,
        *,
        expected_classification: str = LOCAL_CLASSIFICATION,
    ):
        match = ROOT_RE.fullmatch(root.rstrip("/"))
        if match is None:
            raise RegistryRefusal("registry root is not a versioned B6 test snapshot")
        if expected_classification not in {
            LOCAL_CLASSIFICATION,
            DEPLOYED_CLASSIFICATION,
            V2_CLASSIFICATION,
        }:
            raise RegistryRefusal("registry classification policy is unknown")
        self.root = root.rstrip("/")
        self._root = self.root
        # v2 roots and classification travel together — a v2 namespace
        # under a v1 classification (or vice versa) is a config error
        is_v2_root = self.root.startswith(V2_ROOT_PREFIX.rstrip("/"))
        if is_v2_root != (expected_classification == V2_CLASSIFICATION):
            raise RegistryRefusal(
                "registry root and classification disagree (b6v2 roots "
                "require NONPROD_REAL_PROVIDER_V2 and only them)")
        self.classification = expected_classification
        self.snapshot_sha256 = match.group(1)
        label = "b6v2-nonprod" if is_v2_root else "b6-test"
        self.registry_snapshot = f"{label}:{self.snapshot_sha256}"
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
            or manifest["classification"] != self.classification
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
            "classification": self.classification,
            "index": index,
            "routes": raw_routes,
        }
        if hashlib.sha256(canonical_json(material)).hexdigest() != self.snapshot_sha256:
            raise RegistryRefusal("registry snapshot content hash mismatch")
        enforce_single_asr_digest(routes)
        return routes, code_index, default

    def _route(
        self, alias: str, codes: tuple[str, ...], value: Any
    ) -> RegistryRoute:
        if self.classification == LOCAL_CLASSIFICATION:
            return self._local_route(alias, codes, value)
        if self.classification == V2_CLASSIFICATION:
            return self._v2_route(alias, codes, value)
        return self._deployed_route(alias, codes, value)

    def _common_route(
        self,
        *,
        alias: str,
        codes: tuple[str, ...],
        route: dict[str, Any],
        contract_version: str = CONTRACT_VERSION,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
        language = _exact(
            route["language"], {"alias", "response_code", "accepted_codes"},
            "registry route language"
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
            route["contract_version"] != contract_version
            or language["alias"] != alias
            or language["accepted_codes"] != list(codes)
            or not isinstance(language["response_code"], str)
            or CODE_RE.fullmatch(language["response_code"]) is None
            or rag["alias"] != "current"
            or not isinstance(rag["snapshot_sha256"], str)
            or SHA256_RE.fullmatch(rag["snapshot_sha256"]) is None
            or not isinstance(rag["query_language"], str)
            or CODE_RE.fullmatch(rag["query_language"]) is None
            or not self._llm_version_ok(llm["model_version"])
            or llm["policy_id"] != f"{alias}-medzen-v1"
            or not self._tts_version_ok(tts["model_version"])
        ):
            raise RegistryRefusal("registry route is unsafe or inconsistent")
        return language, rag, llm, tts

    # B6v2: under the nonprod/b6v2 root, routes may bind REAL provider
    # identities; under the test/b6 roots only the synthetic ones pass.
    def _is_v2(self) -> bool:
        return self._root.startswith(V2_ROOT_PREFIX)

    def _llm_version_ok(self, version: Any) -> bool:
        if version == "fake-bedrock-local-v1":
            return True
        return (self._is_v2() and isinstance(version, str)
                and V2_LLM_RE.fullmatch(version) is not None)

    def _tts_version_ok(self, version: Any) -> bool:
        if version is None:
            return True
        return (self._is_v2() and isinstance(version, str)
                and V2_TTS_RE.fullmatch(version) is not None)

    def _local_route(
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
        asr = _exact(
            route["asr"], {"backend", "model_version", "fixture_sha256"},
            "registry ASR route"
        )
        language, rag, llm, tts = self._common_route(
            alias=alias, codes=codes, route=route
        )
        if (
            route["schema_version"] != 1
            or route["classification"] != LOCAL_CLASSIFICATION
            or asr["backend"] != "local_synthetic_fixture"
            or not isinstance(asr["model_version"], str)
            or not asr["model_version"]
            or not isinstance(asr["fixture_sha256"], str)
            or SHA256_RE.fullmatch(asr["fixture_sha256"]) is None
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
            asr_artifact_tree_sha256=None,
            asr_reported_registry_snapshot=self.registry_snapshot,
            rag_alias=rag["alias"],
            rag_snapshot_sha256=rag["snapshot_sha256"],
            rag_query_language=rag["query_language"],
            llm_model_version=llm["model_version"],
            llm_policy_id=llm["policy_id"],
            tts_backend=tts["backend"],
            tts_model_version=None,
            registry_snapshot=self.registry_snapshot,
            classification=LOCAL_CLASSIFICATION,
            dependency_endpoints={},
        )

    def _deployed_route(
        self, alias: str, codes: tuple[str, ...], value: Any
    ) -> RegistryRoute:
        route = _exact(
            value,
            {
                "schema_version", "classification", "language", "contract_version",
                "asr", "rag", "llm", "tts", "dependencies"
            },
            "registry route",
        )
        language, rag, llm, tts = self._common_route(
            alias=alias, codes=codes, route=route
        )
        asr = _exact(
            route["asr"],
            {
                "backend", "model_version", "artifact_tree_sha256",
                "reported_registry_snapshot"
            },
            "registry ASR route",
        )
        endpoints = self._cluster_dependencies(route)
        if (
            route["schema_version"] != 2
            or route["classification"] != DEPLOYED_CLASSIFICATION
            or asr["backend"] != "http_cluster_v1"
            or asr["model_version"] != "v0"
            or not isinstance(asr["artifact_tree_sha256"], str)
            or SHA256_RE.fullmatch(asr["artifact_tree_sha256"]) is None
            or not isinstance(asr["reported_registry_snapshot"], str)
            or not asr["reported_registry_snapshot"].startswith("b6a-non-serving:")
            or SHA256_RE.fullmatch(
                asr["reported_registry_snapshot"].removeprefix("b6a-non-serving:")
            ) is None
            or tts != {"backend": "http_text_only_v1", "model_version": None}
        ):
            raise RegistryRefusal("deployed registry route is unsafe or inconsistent")
        return RegistryRoute(
            alias=alias,
            response_code=language["response_code"],
            accepted_codes=codes,
            asr_backend=asr["backend"],
            asr_model_version=asr["model_version"],
            asr_fixture_sha256=None,
            asr_artifact_tree_sha256=asr["artifact_tree_sha256"],
            asr_reported_registry_snapshot=asr["reported_registry_snapshot"],
            rag_alias=rag["alias"],
            rag_snapshot_sha256=rag["snapshot_sha256"],
            rag_query_language=rag["query_language"],
            llm_model_version=llm["model_version"],
            llm_policy_id=llm["policy_id"],
            tts_backend=tts["backend"],
            tts_model_version=None,
            registry_snapshot=self.registry_snapshot,
            classification=DEPLOYED_CLASSIFICATION,
            dependency_endpoints=endpoints,
        )

    def _cluster_dependencies(
        self, route: dict[str, Any],
        contracts: dict[str, tuple[str, str]] = DEPLOYED_CONTRACTS,
    ) -> dict[str, str]:
        dependencies = _exact(
            route["dependencies"], set(DEPLOYED_ENDPOINTS),
            "registry dependency routes",
        )
        endpoints: dict[str, str] = {}
        for name, expected_endpoint in DEPLOYED_ENDPOINTS.items():
            dependency = _exact(
                dependencies[name], {"endpoint", "contract_id", "contract_sha256"},
                f"registry {name} dependency",
            )
            expected_contract, expected_sha = contracts[name]
            if dependency != {
                "endpoint": expected_endpoint,
                "contract_id": expected_contract,
                "contract_sha256": expected_sha,
            }:
                raise RegistryRefusal(
                    f"registry {name} dependency is not the reviewed cluster contract"
                )
            endpoints[name] = dependency["endpoint"]
        return endpoints

    def _v2_route(
        self, alias: str, codes: tuple[str, ...], value: Any
    ) -> RegistryRoute:
        """B6v2 round 3 (Codex): the v2 root previously fell through to
        _deployed_route, whose hard v1 identities (asr v0, text-only tts,
        B6_6 classification) refused every real-provider snapshot. A v2
        route binds the OmniASR artifact, a bedrock:/fake LLM identity and
        an optional governed Fish voice — same reviewed cluster boundary."""
        route = _exact(
            value,
            {
                "schema_version", "classification", "language", "contract_version",
                "asr", "rag", "llm", "tts", "dependencies"
            },
            "registry route",
        )
        language, rag, llm, tts = self._common_route(
            alias=alias, codes=codes, route=route,
            contract_version=V2_CONTRACT_VERSION,
        )
        asr = _exact(
            route["asr"],
            {
                "backend", "model_version", "artifact_tree_sha256",
                "reported_registry_snapshot"
            },
            "registry ASR route",
        )
        endpoints = self._cluster_dependencies(
            route, contracts=V2_DEPLOYED_CONTRACTS)
        tts_ok = (
            tts == {"backend": "http_text_only_v1", "model_version": None}
            or (
                tts["backend"] == "http_fish_v2"
                and isinstance(tts["model_version"], str)
                and V2_TTS_RE.fullmatch(tts["model_version"]) is not None
            )
        )
        if (
            route["schema_version"] != 2
            or route["classification"] != V2_CLASSIFICATION
            or asr["backend"] != "http_cluster_v1"
            or not isinstance(asr["model_version"], str)
            # v2 serves the trained multilingual artifact — the synthetic
            # "v0" identity belongs to the closed v1 proof
            or not asr["model_version"].startswith("omniasr_ctc_1b:")
            or V2_ASR_RE.fullmatch(asr["model_version"]) is None
            or not isinstance(asr["artifact_tree_sha256"], str)
            or SHA256_RE.fullmatch(asr["artifact_tree_sha256"]) is None
            or not isinstance(asr["reported_registry_snapshot"], str)
            or not asr["reported_registry_snapshot"].startswith("omniasr-nonprod:")
            or SHA256_RE.fullmatch(
                asr["reported_registry_snapshot"].removeprefix("omniasr-nonprod:")
            ) is None
            or not tts_ok
        ):
            raise RegistryRefusal("v2 registry route is unsafe or inconsistent")
        return RegistryRoute(
            alias=alias,
            response_code=language["response_code"],
            accepted_codes=codes,
            asr_backend=asr["backend"],
            asr_model_version=asr["model_version"],
            asr_fixture_sha256=None,
            asr_artifact_tree_sha256=asr["artifact_tree_sha256"],
            asr_reported_registry_snapshot=asr["reported_registry_snapshot"],
            rag_alias=rag["alias"],
            rag_snapshot_sha256=rag["snapshot_sha256"],
            rag_query_language=rag["query_language"],
            llm_model_version=llm["model_version"],
            llm_policy_id=llm["policy_id"],
            tts_backend=tts["backend"],
            tts_model_version=tts["model_version"],
            registry_snapshot=self.registry_snapshot,
            classification=V2_CLASSIFICATION,
            dependency_endpoints=endpoints,
        )

    def resolve(self, language: str | None) -> RegistryRoute:
        key = self.default_language if language is None else language.casefold()
        alias = self._aliases_by_code.get(key)
        if alias is None:
            raise RegistryRefusal("language is not present in the bound registry snapshot")
        return self._routes_by_alias[alias]
