"""Deterministic fake AWS/kubectl execution used by the cold rehearsal."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "services" / "asr-eval-runtime"
if str(PACKAGE) not in sys.path:
    sys.path.insert(0, str(PACKAGE))

from medzen_asr_eval.backends import Transcript  # noqa: E402
from medzen_asr_eval.harness import canonical_json  # noqa: E402
from medzen_asr_eval.pilot import run_pilot  # noqa: E402
from scripts.asr_base_model_ecr_scanning import (  # noqa: E402
    canonical_configuration,
    merge_scan_on_push_filter,
    validate_configuration,
)
from scripts.asr_base_model_pilot_runner import AttemptContext, OperationRefusal  # noqa: E402
from scripts.asr_eval_digest_rescan import DigestRescanRefusal, validate_security_binding  # noqa: E402
from scripts.asr_eval_oci_publication import (  # noqa: E402
    ECR_PART_BYTES,
    OCI_INDEX,
    OCI_MANIFEST,
    OciLayout,
    OciPublicationRefusal,
    publish_exact_layout,
)


class FakeBackend:
    def __init__(self, candidate: str):
        self.candidate = candidate

    def transcribe(self, audio: Path, language_id: str | None) -> Transcript:
        suffix = " conditioned" if language_id else ""
        return Transcript(
            text=f"synthetic reference{suffix}",
            eos_observed=True,
            cap_hit=False,
            termination_evidence="fake backend completed",
        )


class FakeSampler:
    def __init__(self):
        self.samples = [100.0, 125.0, 120.0]
        self.errors: list[str] = []

    def start(self) -> None: pass
    def stop(self) -> None: pass


class FakeRegistryScanning:
    """ECR fake that enforces AWS's one-rule-per-frequency constraint."""

    def __init__(self):
        fixture = ROOT / (
            "tests/fixtures/aws/"
            "ecr-get-registry-scanning-configuration-basic-before-asr-eval.json"
        )
        self.configuration = validate_configuration(
            json.loads(fixture.read_bytes())["scanningConfiguration"]
        )
        self.initial = canonical_configuration(self.configuration)
        self.put_calls = 0

    def get(self) -> dict[str, Any]:
        return json.loads(json.dumps(self.configuration))

    def put(self, value: dict[str, Any]) -> None:
        self.configuration = validate_configuration(value)
        self.put_calls += 1

    def restored(self) -> bool:
        return canonical_configuration(self.configuration) == self.initial


def _blob(root: Path, content: bytes, media_type: str) -> dict[str, Any]:
    digest = "sha256:" + hashlib.sha256(content).hexdigest()
    path = root / "blobs/sha256" / digest.removeprefix("sha256:")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return {"digest": digest, "size": len(content), "mediaType": media_type}


def _json_blob(root: Path, value: dict[str, Any], media_type: str) -> dict[str, Any]:
    return _blob(root, json.dumps(value, sort_keys=True, separators=(",", ":")).encode(), media_type)


def _fake_oci_layout(root: Path) -> tuple[OciLayout, dict[str, str]]:
    root.mkdir(parents=True, exist_ok=True)
    (root / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}\n')
    config = _json_blob(root, {"architecture": "amd64", "os": "linux"}, "application/vnd.oci.image.config.v1+json")
    layer = _blob(root, b"x" * (ECR_PART_BYTES + 7), "application/vnd.oci.image.layer.v1.tar+gzip")
    child_value = {"schemaVersion": 2, "mediaType": OCI_MANIFEST, "config": config, "layers": [layer]}
    child = {**_json_blob(root, child_value, OCI_MANIFEST), "platform": {"os": "linux", "architecture": "amd64"}}
    attestation_config = _json_blob(root, {"architecture": "unknown", "os": "unknown"}, "application/vnd.oci.image.config.v1+json")
    predicate = _blob(root, b"attestation", "application/vnd.in-toto+json")
    attestation_value = {"schemaVersion": 2, "mediaType": OCI_MANIFEST, "config": attestation_config, "layers": [predicate]}
    attestation = {**_json_blob(root, attestation_value, OCI_MANIFEST), "platform": {"os": "unknown", "architecture": "unknown"}}
    index_value = {"schemaVersion": 2, "mediaType": OCI_INDEX, "manifests": [child, attestation]}
    index = _json_blob(root, index_value, OCI_INDEX)
    (root / "index.json").write_text(json.dumps({"schemaVersion": 2, "mediaType": OCI_INDEX, "manifests": [index]}))
    return OciLayout(
        root,
        expected_index=index["digest"],
        expected_child=child["digest"],
        expected_config=config["digest"],
        expected_attestation=attestation["digest"],
    ), {"child": child["digest"]}


class FakeMultipartEcr:
    """Compact fake enforcing the exact ECR byte-continuity contract."""

    def __init__(self, *, truncate_part: bool = False, drift_manifest: str | None = None):
        self.truncate_part = truncate_part
        self.drift_manifest = drift_manifest
        self.uploads: dict[str, bytearray] = {}
        self.manifests: dict[str, str] = {}
        self.tags: dict[str, str] = {}

    def batch_check_layer_availability(self, **kwargs: Any) -> dict[str, Any]:
        return {"layers": [], "failures": []}

    def initiate_layer_upload(self, **kwargs: Any) -> dict[str, Any]:
        upload_id = f"upload-{len(self.uploads)}"
        self.uploads[upload_id] = bytearray()
        return {"uploadId": upload_id, "partSize": ECR_PART_BYTES}

    def upload_layer_part(self, **kwargs: Any) -> dict[str, Any]:
        upload = self.uploads[kwargs["uploadId"]]
        if kwargs["partFirstByte"] != len(upload):
            raise RuntimeError("fake received a non-consecutive part")
        upload.extend(kwargs["layerPartBlob"])
        last = kwargs["partLastByte"] - int(self.truncate_part)
        return {"uploadId": kwargs["uploadId"], "lastByteReceived": last}

    def complete_layer_upload(self, **kwargs: Any) -> dict[str, Any]:
        digest = "sha256:" + hashlib.sha256(self.uploads[kwargs["uploadId"]]).hexdigest()
        if kwargs["layerDigests"] != [digest]:
            raise RuntimeError("fake completed digest differs")
        return {"layerDigest": digest}

    def put_image(self, **kwargs: Any) -> dict[str, Any]:
        digest = "sha256:" + hashlib.sha256(kwargs["imageManifest"].encode()).hexdigest()
        if digest != kwargs["imageDigest"]:
            raise RuntimeError("fake manifest digest differs")
        self.manifests[digest] = kwargs["imageManifest"]
        if "imageTag" in kwargs:
            self.tags[kwargs["imageTag"]] = digest
        return {"image": {"imageId": {"imageDigest": digest}}}

    def batch_get_image(self, **kwargs: Any) -> dict[str, Any]:
        image_id = kwargs["imageIds"][0]
        digest = self.tags[image_id["imageTag"]] if "imageTag" in image_id else image_id["imageDigest"]
        body = self.manifests[digest]
        if digest == self.drift_manifest:
            body += "\n"
        return {"images": [{"imageId": {"imageDigest": digest}, "imageManifest": body}], "failures": []}


class FakeOperations:
    def __init__(self, *, inject: str | None = None):
        self.inject = inject
        self.stage_order: list[str] = []
        self.state = {
            "deadline": False,
            "reservation": False,
            "ecr": False,
            "artifacts": False,
            "endpoints": False,
            "strict_cni": False,
            "gpu": 0,
            "volume": False,
            "namespace": False,
            "staging": False,
        }
        self.aggregate: dict[str, Any] | None = None
        self.registry_scanning = FakeRegistryScanning()

    def _enter(self, stage: str) -> None:
        self.stage_order.append(stage)
        if self.inject == stage:
            raise OperationRefusal(f"INJECTED_{stage.upper()}", f"injected failure at {stage}")

    def deadline_identity_and_acceptance(
        self,
        context: AttemptContext,
        *,
        dry_run: bool = False,
        caller_arn: str | None = None,
    ) -> dict[str, Any]:
        self._enter("deadline_identity_and_acceptance")
        self.state["deadline"] = not dry_run
        return {
            "status": "PASS_DEADLINE_IDENTITY_AND_ACCEPTANCE",
            "caller": caller_arn or "arn:aws:iam::558069890522:user/s.fotso",
            "deadline_seconds": 10800,
            "dry_run": dry_run,
        }

    def input_freeze_and_no_phi(self, context: AttemptContext) -> dict[str, Any]:
        self._enter("input_freeze_and_no_phi")
        return {"status": "PASS_INPUT_FREEZE_AND_NO_PHI", "runs": 2, "byte_identical": True, "rows": 24230, "phi": False}

    def cost_and_zero_state(self, context: AttemptContext) -> dict[str, Any]:
        self._enter("cost_and_zero_state")
        self.state["reservation"] = True
        return {"status": "PASS_COST_AND_ZERO_STATE", "reservation_usd": 10.0, "cpu": 0, "gpu": 0}

    def image_publication_and_scan(self, context: AttemptContext) -> dict[str, Any]:
        self._enter("image_publication_and_scan")
        self.state["ecr"] = True
        if context.attempt in {5, 6, 7, 8}:
            try:
                gate_binding = validate_security_binding(context.bindings.get("security_gate", {}))
            except DigestRescanRefusal as exc:
                raise OperationRefusal(exc.reason_code, exc.detail) from exc
            if self.inject in {"security_wrong_digest", "security_extra_finding"}:
                code = (
                    "ECR_RESCAN_CHILD_BINDING_DIFFERS"
                    if self.inject == "security_wrong_digest"
                    else "SCOUT_FINDINGS_DIFFER"
                )
                raise OperationRefusal(code, f"injected attempt-5 security refusal: {self.inject}")
            return {
                "status": "PASS_IMAGE_PUBLICATION_AND_SCAN",
                "publication": {
                    "status": "SKIPPED_EXISTING_EXACT_IMAGE",
                    "aws_image_mutations": 0,
                },
                "security_gate_binding": gate_binding,
                "security_gate": {
                    "status": "PASS_DIGEST_VERIFIED_DUAL_SCAN_GATE",
                    "reconstruction": {
                        "status": "PASS_EXACT_ECR_CHILD_RECONSTRUCTION",
                        "all_downloaded_descriptors_byte_verified": True,
                    },
                    "ecr_basic": {
                        "status": "PASS_ECR_BASIC_OS_GATE",
                        "coverage": "OPERATING_SYSTEM_PACKAGES_ONLY",
                        "critical": 0,
                        "high": 0,
                    },
                    "docker_scout": {
                        "status": "PASS_DOCKER_SCOUT_ACCEPTED_RISK_GATE",
                        "scanner_version": "1.18.3",
                        "scanner_git_commit": "aa68fc25c596bea659d54867443238fd30218d23",
                        "critical": 0,
                        "high": 4,
                    },
                },
            }
        updated, changed = merge_scan_on_push_filter(
            self.registry_scanning.get(), "medzen-asr-eval-runtime"
        )
        if not changed:
            raise OperationRefusal(
                "FAKE_SCAN_FILTER_ALREADY_PRESENT",
                "cold rehearsal expected a pre-merge registry scanning fixture",
            )
        self.registry_scanning.put(updated)
        scan_rules = [
            rule
            for rule in self.registry_scanning.get()["rules"]
            if rule["scanFrequency"] == "SCAN_ON_PUSH"
        ]
        if len(scan_rules) != 1:
            raise OperationRefusal(
                "FAKE_DUPLICATE_SCAN_FREQUENCY",
                "fake ECR accepted more than one SCAN_ON_PUSH rule",
            )
        with tempfile.TemporaryDirectory(prefix="medzen-fake-oci-", dir=context.workdir) as temporary:
            layout, refs = _fake_oci_layout(Path(temporary))
            ecr = FakeMultipartEcr(
                truncate_part=self.inject == "image_upload_part_truncation",
                drift_manifest=refs["child"] if self.inject == "image_manifest_readback_drift" else None,
            )
            try:
                publication = publish_exact_layout(
                    ecr, "medzen-asr-eval-runtime", layout, tag="pilot-exact"
                )
            except OciPublicationRefusal as exc:
                raise OperationRefusal(exc.reason_code, exc.detail) from exc
        return {
            "status": "PASS_IMAGE_PUBLICATION_AND_SCAN",
            "critical": 0,
            "accepted_high": 4,
            "scan_on_push_rules": len(scan_rules),
            "filter_merged_into_existing_rule": True,
            "publication": {
                **publication,
                "fake_aws_service": True,
            },
        }

    def artifact_stage(self, context: AttemptContext) -> dict[str, Any]:
        self._enter("artifact_stage")
        self.state["artifacts"] = True
        return {"status": "PASS_ARTIFACT_STAGE", "create_only": True, "hashes_verified": True}

    def private_endpoint_and_policy_gate(self, context: AttemptContext) -> dict[str, Any]:
        self._enter("private_endpoint_and_policy_gate")
        self.state["endpoints"] = True
        self.state["strict_cni"] = True
        self.state["namespace"] = True
        return {"status": "PASS_PRIVATE_ENDPOINT_AND_POLICY_GATE", "allowed_probes": 3, "denied_probes": 4}

    def gpu_and_sampler_gate(self, context: AttemptContext) -> dict[str, Any]:
        self._enter("gpu_and_sampler_gate")
        self.state["gpu"] = 1
        self.state["volume"] = True
        return {"status": "PASS_GPU_AND_SAMPLER_GATE", "samples": 120, "gpu": 1, "volume_gib": 60}

    def node_local_input_stage(self, context: AttemptContext) -> dict[str, Any]:
        self._enter("node_local_input_stage")
        self.state["staging"] = True
        return {"status": "PASS_NODE_LOCAL_INPUT_STAGE", "bundle_hash_verified": True, "credentials_in_container": False}

    def pilot_rows(self, context: AttemptContext) -> dict[str, Any]:
        self._enter("pilot_rows")
        synthetic = context.workdir / "synthetic-runtime"
        synthetic.mkdir(parents=True, exist_ok=True)
        audio = synthetic / "audio.wav"
        audio.write_bytes(b"synthetic-audio")
        checksum = hashlib.sha256(audio.read_bytes()).hexdigest()
        reference = "synthetic reference"
        rows = {
            "schema_version": 1,
            "classification": "PUBLIC_RESEARCH_NO_PHI",
            "rows": [{
                "manifest": "eval/english/asr/fleurs-v1/manifest.jsonl",
                "language": "english",
                "source_id": "synthetic",
                "audio_local_path": str(audio),
                "audio_checksum_sha256": checksum,
                "duration_s": 1.0,
                "reference": reference,
                "reference_sha256": hashlib.sha256(reference.encode()).hexdigest(),
                "selection_ordinal": 1,
            }],
        }
        rows_path = synthetic / "runtime-rows.json"
        rows_path.write_bytes(canonical_json(rows))
        binding = synthetic / "model-bindings.json"
        binding.write_bytes(b"{}\n")
        self.aggregate = run_pilot(
            rows_path=rows_path,
            model_root=synthetic,
            model_binding_path=binding,
            conditioning_path=PACKAGE / "assets" / "language-conditioning-v1.json",
            receipt_root=synthetic / "row-receipts",
            aggregate_path=synthetic / "aggregate.json",
            backend_loader=lambda candidate, mode, language, root: FakeBackend(candidate),
            model_verifier=lambda root, path: {"status": "PASS_FAKE_MODEL_IDENTITY"},
            sampler=FakeSampler(),
            clock=iter([float(value) for value in range(1000)]).__next__,
        )
        if self.aggregate["status"] != "PASS_AGGREGATE":
            raise OperationRefusal("FAKE_PILOT_DID_NOT_PASS", "local fake pilot aggregate differs")
        return {"status": "PASS_PILOT_ROWS", "completed_inferences": self.aggregate["completed_inferences"], "not_applicable": self.aggregate["not_applicable"]}

    def aggregate_report(self, context: AttemptContext) -> dict[str, Any]:
        self._enter("aggregate_report")
        if self.aggregate is None:
            raise OperationRefusal("AGGREGATE_ABSENT", "pilot aggregate was not created")
        return {"status": "PASS_AGGREGATE_REPORT", "runtime_status": self.aggregate["status"], "groups": len(self.aggregate["aggregate"]["groups"])}

    def cleanup_and_expiry(self, context: AttemptContext) -> dict[str, Any]:
        self.stage_order.append("cleanup_and_expiry")
        for key in ("deadline", "reservation", "endpoints", "strict_cni", "gpu", "volume", "namespace", "staging"):
            self.state[key] = 0 if key == "gpu" else False
        if not self.registry_scanning.restored():
            self.registry_scanning.put(
                json.loads(self.registry_scanning.initial)
            )
        if self.inject == "cleanup_and_expiry":
            raise OperationRefusal("INJECTED_CLEANUP_AND_EXPIRY", "injected cleanup receipt failure after zero state")
        return {"status": "PASS_CLEANUP_AND_EXPIRY", "cpu": 0, "gpu": 0, "endpoints": 0, "namespace": 0, "volume": 0}

    def zero_state(self) -> bool:
        transient = ("deadline", "reservation", "endpoints", "strict_cni", "gpu", "volume", "namespace", "staging")
        return all(not self.state[key] for key in transient) and self.registry_scanning.restored()
