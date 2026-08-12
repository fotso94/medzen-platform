"""Deterministic fake AWS/kubectl execution used by the cold rehearsal."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "services" / "asr-eval-runtime"
if str(PACKAGE) not in sys.path:
    sys.path.insert(0, str(PACKAGE))

from medzen_asr_eval.backends import Transcript  # noqa: E402
from medzen_asr_eval.harness import canonical_json  # noqa: E402
from medzen_asr_eval.pilot import run_pilot  # noqa: E402
from scripts.asr_base_model_pilot_runner import AttemptContext, OperationRefusal  # noqa: E402


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

    def _enter(self, stage: str) -> None:
        self.stage_order.append(stage)
        if self.inject == stage:
            raise OperationRefusal(f"INJECTED_{stage.upper()}", f"injected failure at {stage}")

    def deadline_identity_and_acceptance(self, context: AttemptContext) -> dict[str, Any]:
        self._enter("deadline_identity_and_acceptance")
        self.state["deadline"] = True
        return {"status": "PASS_DEADLINE_IDENTITY_AND_ACCEPTANCE", "caller": "arn:aws:iam::558069890522:user/s.fotso", "deadline_seconds": 10800}

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
        return {"status": "PASS_IMAGE_PUBLICATION_AND_SCAN", "critical": 0, "accepted_high": 4}

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
        if self.inject == "cleanup_and_expiry":
            raise OperationRefusal("INJECTED_CLEANUP_AND_EXPIRY", "injected cleanup receipt failure after zero state")
        return {"status": "PASS_CLEANUP_AND_EXPIRY", "cpu": 0, "gpu": 0, "endpoints": 0, "namespace": 0, "volume": 0}

    def zero_state(self) -> bool:
        transient = ("deadline", "reservation", "endpoints", "strict_cni", "gpu", "volume", "namespace", "staging")
        return all(not self.state[key] for key in transient)
