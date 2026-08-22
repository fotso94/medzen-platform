from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.datastructures import UploadFile

from .auth import AuthRefusal, KeyStore, LocalKeyStore, SecretsManagerKeyStore
from .emergency import EmergencyChecker
from .local_dependencies import LocalLLMClient, LocalRAGClient, SyntheticASRClient
from .orchestrator import OrchestratorRefusal, SpeechOrchestrator
from .registry import (
    DEPLOYED_CLASSIFICATION,
    V2_CLASSIFICATION,
    LocalParameterStore,
    RegistryRouter,
    SSMParameterStore,
)
from .remote_dependencies import (
    ClusterHTTPTransport,
    RemoteASRClient,
    RemoteLLMClient,
    RemoteRAGClient,
    RemoteTTSClient,
)


LOGGER = logging.getLogger("medzen.orchestrator")
CONTRACT_VERSION = "medzen.speech.v1"
MAX_AUDIO_BYTES = 26_214_400
LANGUAGE_RE = re.compile(r"^[a-z]{2,3}$")
FORM_FIELDS = {"audio", "request_id", "language_hint", "response_audio"}


def _root() -> Path:
    return Path(__file__).resolve().parents[3]


def _error(
    *,
    request_id: str,
    registry_snapshot: str,
    code: str,
    message: str,
    status_code: int,
    retryable: bool,
) -> JSONResponse:
    return JSONResponse(
        {
            "request_id": request_id,
            "model_versions": {
                "asr": None,
                "registry_snapshot": registry_snapshot,
                "llm": None,
                "rag": None,
                "tts": None,
            },
            "error": {"code": code, "message": message, "retryable": retryable},
        },
        status_code=status_code,
    )


def build_local_orchestrator() -> tuple[SpeechOrchestrator, LocalKeyStore]:
    root = _root()
    fixture_path = Path(os.environ.get(
        "MEDZEN_LOCAL_REGISTRY_FIXTURE",
        root / "platform/testdata/registry-ssm/b6-local-v1.json",
    ))
    fixture = json.loads(fixture_path.read_bytes())
    names = [item.get("Name", "") for item in fixture.get("parameters", [])]
    manifest_names = [name for name in names if name.endswith("/_manifest")]
    if len(manifest_names) != 1:
        raise RuntimeError("local registry fixture has no unique manifest")
    registry_root = manifest_names[0].removesuffix("/_manifest")
    router = RegistryRouter(LocalParameterStore(fixture_path), registry_root)
    orchestrator = SpeechOrchestrator(
        router=router,
        emergency=EmergencyChecker(root / "registry/emergency-policies/v1.yaml"),
        asr=SyntheticASRClient(
            root / "platform/testdata/orchestrator/asr-fixture.json"
        ),
        rag=LocalRAGClient(root / "platform/testdata/rag-index"),
        llm=LocalLLMClient(
            root / "registry/languages", root / "registry/llm-policies/v1.yaml"
        ),
    )
    auth = LocalKeyStore(root / "platform/testdata/orchestrator/client-keys.json")
    return orchestrator, auth


def build_deployed_orchestrator() -> tuple[SpeechOrchestrator, SecretsManagerKeyStore]:
    if os.environ.get("AWS_REGION") != "eu-central-1":
        raise RuntimeError("deployed orchestrator requires the reviewed AWS region")
    registry_root = os.environ.get("MEDZEN_REGISTRY_ROOT", "")
    # B6v2 round 3 (Codex): the deployed builder only matched the v1 test
    # namespace, so no environment could ever select the v2 root — the
    # classification follows the namespace, never a second free variable.
    if re.fullmatch(r"/medzen/registry/test/b6/[0-9a-f]{64}", registry_root):
        expected_classification = DEPLOYED_CLASSIFICATION
    elif re.fullmatch(r"/medzen/registry/nonprod/b6v2/[0-9a-f]{64}", registry_root):
        expected_classification = V2_CLASSIFICATION
    else:
        raise RuntimeError(
            "deployed orchestrator requires an exact test/b6 or nonprod/b6v2 "
            "registry root")
    if os.environ.get("MEDZEN_CLIENT_KEYS_SECRET_ID") != "medzen/client-api-keys":
        raise RuntimeError("deployed orchestrator requires the exact client key secret")
    import boto3

    router = RegistryRouter(
        SSMParameterStore(boto3.client("ssm", region_name="eu-central-1")),
        registry_root,
        expected_classification=expected_classification,
    )
    transport = ClusterHTTPTransport(timeout_seconds=30.0)
    orchestrator = SpeechOrchestrator(
        router=router,
        emergency=EmergencyChecker(_root() / "registry/emergency-policies/v1.yaml"),
        asr=RemoteASRClient(transport),
        rag=RemoteRAGClient(transport),
        llm=RemoteLLMClient(transport),
        tts=RemoteTTSClient(transport),
    )
    auth = SecretsManagerKeyStore(
        boto3.client("secretsmanager", region_name="eu-central-1"),
        "medzen/client-api-keys",
    )
    return orchestrator, auth


def build_configured_orchestrator() -> tuple[SpeechOrchestrator, KeyStore]:
    mode = os.environ.get("MEDZEN_ORCHESTRATOR_MODE", "local_fixture")
    if mode == "local_fixture":
        return build_local_orchestrator()
    if mode == "deployed_http_ssm":
        return build_deployed_orchestrator()
    raise RuntimeError("orchestrator mode is unknown and was refused")


def create_app(
    orchestrator: SpeechOrchestrator | None = None,
    auth: KeyStore | None = None,
    *,
    max_audio_bytes: int = MAX_AUDIO_BYTES,
) -> FastAPI:
    supplied_orchestrator = orchestrator
    supplied_auth = auth

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            if supplied_orchestrator is None or supplied_auth is None:
                built_orchestrator, built_auth = build_configured_orchestrator()
                app.state.orchestrator = supplied_orchestrator or built_orchestrator
                app.state.auth = supplied_auth or built_auth
            else:
                app.state.orchestrator = supplied_orchestrator
                app.state.auth = supplied_auth
            app.state.startup_error = None
            app.state.mode = (
                "local_fixture"
                if app.state.orchestrator.router.classification
                == "B6_3_LOCAL_SYNTHETIC_ONLY"
                else "deployed_http_ssm"
            )
        except Exception as exc:
            app.state.orchestrator = None
            app.state.auth = None
            app.state.startup_error = type(exc).__name__
            app.state.mode = os.environ.get(
                "MEDZEN_ORCHESTRATOR_MODE", "local_fixture"
            )
        yield

    app = FastAPI(
        title="MedZen B6.3 local speech orchestrator",
        version="b6.3-local-file-v1",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.middleware("http")
    async def phi_safe_access_log(request: Request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        record: dict[str, Any] = {
            "event_type": "http_request",
            "request_id": getattr(request.state, "request_id", "absent"),
            "hashed_session_id": getattr(request.state, "hashed_session_id", "absent"),
            "language": getattr(request.state, "language", "absent"),
            "model_versions": getattr(request.state, "model_versions", None),
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            "status_code": response.status_code,
            "error_code": getattr(request.state, "error_code", None),
        }
        LOGGER.info(json.dumps(record, sort_keys=True))
        return response

    @app.get("/healthz")
    async def healthz():
        return {"status": "alive", "service": "speech-orchestrator"}

    @app.get("/readyz")
    async def readyz(request: Request):
        service = request.app.state.orchestrator
        ready = service is not None and request.app.state.auth is not None
        payload: dict[str, Any] = {
            "ready": ready,
            "mode": request.app.state.mode,
            "registry_loaded": service is not None,
            "authentication_loaded": request.app.state.auth is not None,
            "external_network_access": False,
        }
        if ready:
            payload["registry_snapshot"] = service.registry_snapshot
        else:
            payload["error_code"] = request.app.state.startup_error or "NOT_READY"
        return JSONResponse(payload, status_code=200 if ready else 503)

    @app.post("/v1/conversations/speech")
    async def speech(request: Request):
        service = request.app.state.orchestrator
        key_store = request.app.state.auth
        request_id = str(uuid.uuid4())
        registry_snapshot = (
            service.registry_snapshot if service is not None else "local-registry:unavailable"
        )

        def refuse(
            code: str, message: str, status_code: int, retryable: bool = False
        ) -> JSONResponse:
            request.state.request_id = request_id
            request.state.error_code = code
            return _error(
                request_id=request_id,
                registry_snapshot=registry_snapshot,
                code=code,
                message=message,
                status_code=status_code,
                retryable=retryable,
            )

        if service is None or key_store is None:
            return refuse(
                "DEPENDENCY_UNAVAILABLE", "orchestrator is not ready", 503, True
            )
        try:
            key_store.authenticate(request.headers.get("Authorization"))
        except AuthRefusal as exc:
            return refuse(exc.code, exc.message, exc.status_code)
        if request.headers.get("X-MedZen-Contract-Version") != CONTRACT_VERSION:
            return refuse(
                "CONTRACT_VERSION_UNSUPPORTED",
                "X-MedZen-Contract-Version must be medzen.speech.v1",
                426,
            )
        media_type = request.headers.get("content-type", "").split(";", 1)[0]
        if media_type != "multipart/form-data":
            return refuse("INVALID_REQUEST", "multipart/form-data is required", 415)
        content_length = request.headers.get("content-length")
        if content_length is None:
            return refuse("INVALID_REQUEST", "Content-Length is required", 400)
        try:
            parsed_content_length = int(content_length)
            if parsed_content_length <= 0:
                return refuse("INVALID_REQUEST", "Content-Length is invalid", 400)
            if parsed_content_length > max_audio_bytes + 65_536:
                return refuse(
                    "PAYLOAD_TOO_LARGE", "audio exceeds the configured limit", 413
                )
        except ValueError:
            return refuse("INVALID_REQUEST", "Content-Length is invalid", 400)
        try:
            form = await request.form(max_files=1, max_fields=3, max_part_size=4096)
            items = form.multi_items()
            names = [name for name, _ in items]
            if set(names).difference(FORM_FIELDS) or len(names) != len(set(names)):
                return refuse("INVALID_REQUEST", "form fields are unknown or repeated", 400)
            audio = form.get("audio")
            if not isinstance(audio, UploadFile):
                return refuse("INVALID_REQUEST", "audio file is required", 400)
            if audio.content_type not in {"audio/wav", "audio/x-wav"}:
                return refuse(
                    "UNSUPPORTED_AUDIO_TYPE", "audio must use the WAV media type", 415
                )
            raw_audio = await audio.read(max_audio_bytes + 1)
            if not raw_audio:
                return refuse("INVALID_REQUEST", "audio file is empty", 400)
            if len(raw_audio) > max_audio_bytes:
                return refuse(
                    "PAYLOAD_TOO_LARGE", "audio exceeds the configured limit", 413
                )
            supplied_request_id = form.get("request_id")
            if supplied_request_id is not None:
                if not isinstance(supplied_request_id, str):
                    return refuse("INVALID_REQUEST", "request_id is invalid", 400)
                try:
                    request_id = str(uuid.UUID(supplied_request_id))
                except ValueError:
                    return refuse("INVALID_REQUEST", "request_id must be a UUID", 400)
            language_hint = form.get("language_hint")
            if language_hint is not None and (
                not isinstance(language_hint, str)
                or LANGUAGE_RE.fullmatch(language_hint) is None
            ):
                return refuse(
                    "INVALID_REQUEST", "language_hint must be a lowercase language code", 400
                )
            response_audio = form.get("response_audio")
            if response_audio is not None and response_audio not in {"true", "false"}:
                return refuse(
                    "INVALID_REQUEST", "response_audio must be true or false", 400
                )
        except Exception:
            return refuse("INVALID_REQUEST", "multipart request is malformed", 400)
        finally:
            if "form" in locals():
                await form.close()
        request.state.request_id = request_id
        try:
            session_id, result = service.handle(
                audio=raw_audio,
                request_id=request_id,
                language_hint=language_hint,
                response_audio=(response_audio == "true"),
            )
        except OrchestratorRefusal as exc:
            return refuse(
                exc.code, exc.message, exc.status_code, retryable=exc.retryable
            )
        request.state.hashed_session_id = hashlib.sha256(
            session_id.encode("utf-8")
        ).hexdigest()
        request.state.language = result["language"]
        request.state.model_versions = result["model_versions"]
        return result

    return app


app = create_app()
