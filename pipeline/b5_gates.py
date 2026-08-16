"""B5 fail-closed promotion gates and deterministic report generation.

The current B4 artifact is a negative test case. This module deliberately
separates a gate's five-state classification from the artifact's overall
promotion outcome: any required state other than PASS or a proven
NOT_APPLICABLE blocks promotion, while an unevaluated language remains
NOT_EVALUATED rather than being mislabeled FAIL.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import subprocess
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

import yaml

ROOT = Path(__file__).resolve().parent.parent
AUTH = ROOT / "platform/decisions/B5-AUTH-2026-001-refusal-engineering.json"
SCOPE = ROOT / "registry/gates/b5-scope-v1.yaml"
TERMINATION = ROOT / "registry/gates/_termination-v1.yaml"
DECODE = ROOT / "registry/decode-approvals/v1.yaml"
LANG_DIR = ROOT / "registry/languages"
GATE_DIR = ROOT / "registry/gates"


class GateState(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_EVALUATED = "NOT_EVALUATED"
    DEFERRED = "DEFERRED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class FailClosedError(ValueError):
    """An ambiguity, provenance failure or malformed control."""


def parse_state(value: Any) -> GateState:
    try:
        return GateState(value)
    except (TypeError, ValueError) as exc:
        raise FailClosedError(f"unknown or malformed gate state: {value!r}") from exc


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_report_bytes(report: dict[str, Any]) -> bytes:
    """Canonical bytes used for both persistence and the content address."""
    return (json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True,
                       allow_nan=False) + "\n").encode("utf-8")


def write_content_addressed_report(report: dict[str, Any], directory: Path) -> dict:
    """Create `<sha256>.json` once; never overwrite an existing report."""
    body = canonical_report_bytes(report)
    digest = sha256_bytes(body)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{digest}.json"
    if path.exists():
        if path.read_bytes() != body:
            raise FailClosedError(
                f"content-address collision at {path}; refusing overwrite")
        created = False
    else:
        with path.open("xb") as stream:
            stream.write(body)
        created = True
    return {
        "path": str(path),
        "sha256": digest,
        "bytes": len(body),
        "created": created,
    }


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _git_head(root: Path) -> str:
    value = ""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
            text=True, check=False)
        if result.returncode == 0:
            value = result.stdout.strip()
    except OSError:
        # The pinned trainer image deliberately omits the git executable. Its
        # verified source tree is nevertheless mounted with read-only .git
        # metadata, which is sufficient to resolve an immutable HEAD.
        pass
    if _is_git_sha(value):
        return value
    return _git_head_from_metadata(root)


def _is_git_sha(value: Any) -> bool:
    return (isinstance(value, str) and len(value) == 40
            and all(c in "0123456789abcdef" for c in value))


def _git_head_from_metadata(root: Path) -> str:
    dotgit = root / ".git"
    if dotgit.is_file():
        marker = dotgit.read_text().strip()
        if not marker.startswith("gitdir: "):
            raise FailClosedError("cannot resolve gate-engine .git directory")
        git_dir = Path(marker.removeprefix("gitdir: "))
        if not git_dir.is_absolute():
            git_dir = (root / git_dir).resolve()
    else:
        git_dir = dotgit
    try:
        head = (git_dir / "HEAD").read_text().strip()
    except OSError as exc:
        raise FailClosedError(
            f"cannot resolve gate-engine Git commit from metadata: {exc}") from exc
    if _is_git_sha(head):
        return head
    if not head.startswith("ref: refs/"):
        raise FailClosedError("gate-engine HEAD is malformed")
    ref = head.removeprefix("ref: ")
    try:
        loose = (git_dir / ref).read_text().strip()
    except OSError:
        loose = ""
    if _is_git_sha(loose):
        return loose
    try:
        packed_lines = (git_dir / "packed-refs").read_text().splitlines()
    except OSError as exc:
        raise FailClosedError(
            f"cannot resolve gate-engine Git ref {ref}: {exc}") from exc
    matches = [line.split(" ", 1)[0] for line in packed_lines
               if not line.startswith(("#", "^")) and line.endswith(f" {ref}")]
    if len(matches) != 1 or not _is_git_sha(matches[0]):
        raise FailClosedError(f"cannot resolve a unique gate-engine Git ref {ref}")
    return matches[0]


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise FailClosedError(f"cannot read JSON provenance {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise FailClosedError(f"JSON provenance {path} is not an object")
    return value


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise FailClosedError(f"cannot read YAML control {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise FailClosedError(f"YAML control {path} is not an object")
    return value


def _verify_hash(path: Path, expected: str) -> str:
    if not isinstance(expected, str) or len(expected) != 64:
        raise FailClosedError(f"malformed expected SHA-256 for {path}")
    try:
        actual = sha256_file(path)
    except OSError as exc:
        raise FailClosedError(f"missing required provenance {path}") from exc
    if actual != expected:
        raise FailClosedError(
            f"provenance hash mismatch for {path}: {actual}, expected {expected}")
    return actual


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FailClosedError(f"{name} must be a finite number")
    value = float(value)
    if not math.isfinite(value):
        raise FailClosedError(f"{name} must be a finite number")
    return value


def _gate(gate_id: str, state: GateState | str, reason: str, *,
          required: bool = True, measurement: Any = None,
          threshold: dict[str, Any] | None = None,
          evidence: Iterable[dict[str, Any]] = ()) -> dict[str, Any]:
    parsed = parse_state(state.value if isinstance(state, GateState) else state)
    row: dict[str, Any] = {
        "gate": gate_id,
        "state": parsed.value,
        "required": bool(required),
        "reason": reason,
        "evidence": list(evidence),
    }
    if measurement is not None:
        row["measurement"] = measurement
    if threshold is not None:
        row["threshold"] = threshold
    return row


def _compare(gate_id: str, measurement: Any, threshold: Any, operator: str,
             evidence: Iterable[dict[str, Any]], *, required: bool = True) -> dict:
    if measurement is None:
        return _gate(
            gate_id, GateState.NOT_EVALUATED,
            "Required measurement is absent; absence never becomes PASS.",
            required=required, threshold={"operator": operator, "value": threshold},
            evidence=evidence)
    try:
        measured = _finite_number(measurement, gate_id)
        limit = _finite_number(threshold, f"{gate_id} threshold")
    except FailClosedError as exc:
        return _gate(gate_id, GateState.FAIL, str(exc), required=required,
                     measurement=measurement,
                     threshold={"operator": operator, "value": threshold},
                     evidence=evidence)
    if operator == "<=":
        passed = measured <= limit
    elif operator == ">=":
        passed = measured >= limit
    else:
        return _gate(gate_id, GateState.FAIL,
                     f"Unknown comparison operator {operator!r}; failed closed.",
                     required=required, measurement=measured,
                     threshold={"operator": operator, "value": limit},
                     evidence=evidence)
    return _gate(
        gate_id, GateState.PASS if passed else GateState.FAIL,
        f"Measured {measured:.6f} {'satisfies' if passed else 'violates'} "
        f"the predeclared {operator} {limit:.6f} threshold.",
        required=required, measurement=measured,
        threshold={"operator": operator, "value": limit}, evidence=evidence)


def _absolute_cer_gate(gate_id: str, measurement: Any, threshold: Any,
                       evidence: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    """A5 tonal rule: CER gates a language only when its frozen threshold is
    numeric. A null threshold is the registry's affirmative declaration that
    CER does not gate the language, so no gate row exists to emit; any other
    threshold flows through the same fail-closed comparison as WER."""
    if threshold is None:
        return None
    return _compare(gate_id, measurement, threshold, "<=", evidence)


def resolve_thresholds(alias: str, root: Path = ROOT) -> dict[str, Any]:
    """Resolve one language deterministically and reject ambiguous overrides."""
    language_path = root / "registry/languages" / f"{alias}.yaml"
    language = _load_yaml(language_path)
    if language.get("alias") != alias:
        raise FailClosedError(f"{language_path}: alias mismatch")
    ref = language.get("thresholds_ref")
    if ref != f"gates/{alias}-v1.yaml":
        raise FailClosedError(f"{alias}: ambiguous thresholds_ref {ref!r}")
    gate_path = root / "registry" / ref
    gate = _load_yaml(gate_path)
    allowed_top = {"inherits", "language", "version", "rationale", "overrides"}
    extras = sorted(set(gate) - allowed_top)
    if extras:
        raise FailClosedError(f"{gate_path}: unknown keys {extras}")
    if gate.get("language") != alias:
        raise FailClosedError(f"{gate_path}: language mismatch")
    if gate.get("inherits") != "_defaults.yaml":
        raise FailClosedError(
            f"{gate_path}: inheritance must resolve uniquely to _defaults.yaml")
    default_path = root / "registry/gates/_defaults.yaml"
    defaults = _load_yaml(default_path)
    if gate.get("version") != defaults.get("version"):
        raise FailClosedError(
            f"{alias}: threshold version mismatch between defaults and override")
    merged = copy.deepcopy(defaults)
    sources: dict[str, dict[str, Any]] = {}
    for section, values in defaults.items():
        if section == "version" or not isinstance(values, dict):
            continue
        for key in values:
            sources[f"{section}.{key}"] = {
                "source": _relative(default_path, root),
                "sha256": sha256_file(default_path),
            }
    overrides = gate.get("overrides") or {}
    if not isinstance(overrides, dict):
        raise FailClosedError(f"{gate_path}: overrides must be an object")
    for section, values in overrides.items():
        if section not in defaults or not isinstance(defaults[section], dict):
            raise FailClosedError(f"{gate_path}: unknown override section {section}")
        if not isinstance(values, dict):
            raise FailClosedError(f"{gate_path}: override {section} is not an object")
        for key, value in values.items():
            # `notes` is descriptive gate metadata used by the original
            # Pidgin TTS policy. It is preserved but never interpreted as a
            # numeric threshold.
            if key not in defaults[section] and key != "notes":
                raise FailClosedError(
                    f"{gate_path}: unknown override threshold {section}.{key}")
            merged[section][key] = value
            sources[f"{section}.{key}"] = {
                "source": _relative(gate_path, root),
                "sha256": sha256_file(gate_path),
            }
    return {
        "language": alias,
        "version": gate["version"],
        "values": merged,
        "value_sources": sources,
        "files": [
            {"path": _relative(default_path, root),
             "sha256": sha256_file(default_path)},
            {"path": _relative(gate_path, root),
             "sha256": sha256_file(gate_path)},
            {"path": _relative(language_path, root),
             "sha256": sha256_file(language_path)},
        ],
        "language_document": language,
    }


def _checksum(value: Any) -> str:
    if (not isinstance(value, str) or len(value) != 64
            or any(c not in "0123456789abcdef" for c in value)):
        raise FailClosedError(f"invalid audio SHA-256 identity {value!r}")
    return value


def unique_termination_failures(rows: Iterable[Any]) -> set[str]:
    """Count one failing audio checksum once even if both conditions hold."""
    failures: set[str] = set()
    for row in rows:
        if isinstance(row, str):
            failures.add(_checksum(row))
            continue
        if not isinstance(row, dict):
            raise FailClosedError("termination failure row must be a checksum or object")
        missing_eos = row.get("missing_eos")
        cap_hit = row.get("cap_hit")
        if not isinstance(missing_eos, bool) or not isinstance(cap_hit, bool):
            raise FailClosedError("termination flags must be booleans")
        if missing_eos or cap_hit:
            failures.add(_checksum(row.get("audio_checksum_sha256")))
    return failures


def checkpoint_termination_gate(history: list[dict[str, Any]],
                                max_unique: int = 1) -> dict[str, Any]:
    """Apply checkpoint tolerance, recurrence and immediate-stop controls."""
    seen: set[str] = set()
    last_step = -1
    for index, checkpoint in enumerate(history):
        step = checkpoint.get("checkpoint")
        if not isinstance(step, int) or step <= last_step:
            return _gate("termination.checkpoint_selection", GateState.FAIL,
                         "Checkpoint history is malformed or not strictly ordered.")
        last_step = step
        try:
            failures = unique_termination_failures(checkpoint.get("failures", []))
        except FailClosedError as exc:
            return _gate("termination.checkpoint_selection", GateState.FAIL, str(exc))
        if len(failures) > max_unique:
            return _gate(
                "termination.checkpoint_selection", GateState.FAIL,
                f"Checkpoint {step} has {len(failures)} unique failures; maximum is {max_unique}.",
                measurement={"checkpoint": step, "unique_failures": len(failures)},
                threshold={"operator": "<=", "value": max_unique})
        repeated = sorted(failures & seen)
        if repeated:
            later_work = index < len(history) - 1 or bool(
                checkpoint.get("subsequent_optimization_executed"))
            suffix = (" Subsequent optimization was also recorded after the "
                      "mandatory stop." if later_work else "")
            return _gate(
                "termination.checkpoint_selection", GateState.FAIL,
                f"Failure checksum recurred at checkpoint {step}; immediate stop required.{suffix}",
                measurement={"checkpoint": step, "repeated_checksums": repeated})
        seen.update(failures)
    return _gate(
        "termination.checkpoint_selection", GateState.PASS,
        "Every checkpoint is within the one-unique-failure tolerance and no checksum recurs.",
        measurement={"checkpoints": len(history),
                     "unique_failure_checksums": sorted(seen)},
        threshold={"operator": "<= per language per checkpoint", "value": max_unique})


def holdout_termination_gate(rows: Iterable[Any], max_unique: int = 0) -> dict:
    try:
        failures = unique_termination_failures(rows)
    except FailClosedError as exc:
        return _gate("termination.untouched_lingala_holdout", GateState.FAIL, str(exc))
    state = GateState.PASS if len(failures) <= max_unique else GateState.FAIL
    return _gate(
        "termination.untouched_lingala_holdout", state,
        f"Untouched holdout has {len(failures)} unique termination failures; maximum is {max_unique}.",
        measurement={"unique_failure_checksums": sorted(failures)},
        threshold={"operator": "<=", "value": max_unique})


def post_conversion_termination_gate(selected_rows: Iterable[Any],
                                     converted_rows: Iterable[Any],
                                     max_unique: int = 1) -> dict:
    try:
        selected = unique_termination_failures(selected_rows)
        converted = unique_termination_failures(converted_rows)
    except FailClosedError as exc:
        return _gate("termination.post_conversion", GateState.FAIL, str(exc))
    if len(converted) > max_unique:
        return _gate("termination.post_conversion", GateState.FAIL,
                     "Post-conversion failure count exceeds the fixed tolerance.",
                     measurement={"selected": sorted(selected),
                                  "converted": sorted(converted)},
                     threshold={"operator": "<=", "value": max_unique})
    if converted - selected:
        return _gate("termination.post_conversion", GateState.FAIL,
                     "Post-conversion introduced a new or different failure checksum.",
                     measurement={"selected": sorted(selected),
                                  "converted": sorted(converted)})
    if len(converted) > len(selected):
        return _gate("termination.post_conversion", GateState.FAIL,
                     "Post-conversion failure count increased.",
                     measurement={"selected_count": len(selected),
                                  "converted_count": len(converted)})
    return _gate(
        "termination.post_conversion", GateState.PASS,
        "Post-conversion failures remain within tolerance with no new checksum or count increase.",
        measurement={"selected": sorted(selected), "converted": sorted(converted)},
        threshold={"operator": "<=", "value": max_unique})


def _decode_gate(alias: str, language: dict, approvals: dict,
                 evidence: list[dict[str, Any]]) -> dict:
    entry = (approvals.get("languages") or {}).get(alias)
    strategy = language["asr"]["decode_strategy"]
    if not entry:
        if strategy.get("mode") != "pending_experiment":
            return _gate("decode_strategy", GateState.FAIL,
                         "Generated registry has non-pending decode state with no versioned approval.",
                         evidence=evidence)
        return _gate("decode_strategy", GateState.NOT_EVALUATED,
                     "No versioned promotion-grade decode approval exists.", evidence=evidence)
    try:
        state = parse_state(entry.get("promotion_state"))
    except FailClosedError as exc:
        return _gate("decode_strategy", GateState.FAIL, str(exc), evidence=evidence)
    if state is GateState.PASS:
        required = {
            "mode", "prompt_tokens", "token_configuration", "search",
            "token_cap", "normalization", "frozen_eval", "chosen_by_run",
            "artifact_provenance", "code_provenance", "container_provenance",
        }
        config = entry.get("approved_configuration") or {}
        missing = sorted(required - set(config))
        if missing:
            return _gate("decode_strategy", GateState.FAIL,
                         f"Decode approval is incomplete; missing {missing}.",
                         evidence=evidence)
    return _gate("decode_strategy", state, entry.get("reason") or
                 "Versioned decode approval state.", evidence=evidence)


def _production_comparison(alias: str, language: dict,
                           evidence: list[dict[str, Any]]) -> dict:
    asr = language.get("asr") or {}
    artifact, version = asr.get("artifact"), asr.get("approved_version")
    if artifact is None and version is None:
        return _gate(
            "current_production_comparison", GateState.NOT_APPLICABLE,
            "The generated language registry proves both artifact and approved_version are null.",
            evidence=evidence)
    if (artifact is None) != (version is None):
        return _gate(
            "current_production_comparison", GateState.FAIL,
            "Current-production identity is internally inconsistent; failed closed.",
            evidence=evidence)
    return _gate(
        "current_production_comparison", GateState.NOT_EVALUATED,
        "A current production artifact exists but candidate-versus-production measurements are absent.",
        evidence=evidence)


def _aggregate_language(scope_state: GateState | None,
                        gates: list[dict[str, Any]]) -> GateState:
    if scope_state in {GateState.DEFERRED, GateState.NOT_APPLICABLE}:
        return scope_state
    states = [parse_state(g["state"]) for g in gates if g.get("required", True)]
    if GateState.FAIL in states:
        return GateState.FAIL
    if GateState.NOT_EVALUATED in states:
        return GateState.NOT_EVALUATED
    if GateState.DEFERRED in states:
        return GateState.DEFERRED
    if states and all(s in {GateState.PASS, GateState.NOT_APPLICABLE} for s in states):
        return GateState.PASS
    return GateState.NOT_EVALUATED


def _evidence(path: Path, root: Path) -> dict[str, Any]:
    return {"path": _relative(path, root), "sha256": sha256_file(path)}


def _verified_report(root: Path) -> dict[str, Any]:
    auth_path = root / AUTH.relative_to(ROOT)
    scope_path = root / SCOPE.relative_to(ROOT)
    termination_path = root / TERMINATION.relative_to(ROOT)
    decode_path = root / DECODE.relative_to(ROOT)
    engine_path = root / "pipeline/b5_gates.py"
    script_path = root / "scripts/evaluate_gates.py"
    auth = _load_json(auth_path)
    scope = _load_yaml(scope_path)
    termination = _load_yaml(termination_path)
    decode = _load_yaml(decode_path)
    if auth.get("id") != "B5-AUTH-2026-001" or auth.get("status") != "owner-approved":
        raise FailClosedError("B5 authorization is missing or not owner-approved")
    if scope.get("version") != "b5-refusal-scope-v1":
        raise FailClosedError("unknown B5 scope version")
    if termination.get("version") != "b4-owner-approved-termination-v1":
        raise FailClosedError("unknown termination control version")
    if decode.get("version") != "decode-approval-v1":
        raise FailClosedError("unknown decode approval version")

    for binding in auth.get("b4_evidence_bindings", []):
        _verify_hash(root / binding["path"], binding["sha256"])
    term_decision = termination.get("decision") or {}
    _verify_hash(root / term_decision["path"], term_decision["sha256"])
    for group in (scope.get("candidate_evidence") or {}).values():
        _verify_hash(root / group["path"], group["sha256"])
    for group in (scope.get("decision_references") or {}).values():
        _verify_hash(root / group["path"], group["sha256"])

    current_scope_ref = scope["decision_references"][
        "active_and_deferred_scope"]
    current_scope_decision = _load_json(root / current_scope_ref["path"])
    if (current_scope_decision.get("record")
            != "B4-B5-LANGUAGE-SCOPE-DEFERRAL"
            or current_scope_decision.get("id") != "B4-B5-SCOPE-2026-003"
            or current_scope_decision.get("status") != "owner_approved"):
        raise FailClosedError("current language-scope decision is not approved")

    candidate_binding = auth["candidate"]
    conversion_ref = scope["candidate_evidence"]["conversion"]
    source_ref = scope["candidate_evidence"]["source_training_and_baseline"]
    mlflow_ref = scope["candidate_evidence"]["mlflow_resolution"]
    conversion = _load_json(root / conversion_ref["path"])
    source = _load_json(root / source_ref["path"])
    mlflow_resolution = _load_json(root / mlflow_ref["path"])
    if conversion.get("campaign_run") != candidate_binding["campaign"]:
        raise FailClosedError("candidate campaign provenance mismatch")
    if conversion.get("attempt") != "5":
        raise FailClosedError("controlled conversion attempt mismatch")
    if conversion.get("artifact", {}).get("tree_sha256") != candidate_binding["artifact_tree_sha256"]:
        raise FailClosedError("candidate artifact tree hash mismatch")
    if conversion.get("artifact", {}).get("bytes") != candidate_binding["artifact_bytes"]:
        raise FailClosedError("candidate artifact byte count mismatch")
    if conversion.get("source", {}).get("adapter_tree_sha256") != candidate_binding["adapter_tree_sha256"]:
        raise FailClosedError("candidate adapter tree hash mismatch")
    if source.get("source_training", {}).get("attempt") != "1":
        raise FailClosedError("source training attempt mismatch")
    if source.get("source_training", {}).get("selected_checkpoint") != candidate_binding["selected_checkpoint"]:
        raise FailClosedError("selected checkpoint mismatch")
    required_conversion_provenance = {
        "git_sha": 40,
        "bundle_tar_sha256": 64,
        "stage_descriptor_sha256": 64,
        "scope_deviation_sha256": 64,
        "artifact_evaluation_sha256": 64,
        "mlflow_snapshot_sha256": 64,
    }
    executed = conversion.get("executed_provenance") or {}
    for key, length in required_conversion_provenance.items():
        value = executed.get(key)
        if (not isinstance(value, str) or len(value) != length
                or any(c not in "0123456789abcdef" for c in value)):
            raise FailClosedError(f"missing or malformed candidate provenance {key}")
    image_digest = executed.get("image_digest", "")
    if (not isinstance(image_digest, str) or not image_digest.startswith("sha256:")
            or len(image_digest) != 71):
        raise FailClosedError("missing or malformed candidate image digest")
    if conversion.get("promotable") is not False:
        raise FailClosedError("candidate evidence no longer says promotable:false")
    if conversion.get("artifact", {}).get("registered_models") != 0:
        raise FailClosedError("candidate evidence no longer has zero registered models")
    if conversion.get("artifact", {}).get("b5_allowed") is not False:
        raise FailClosedError("candidate evidence unexpectedly allows B5 promotion")
    target_run_id = mlflow_resolution.get("attachment_rule", {}).get("target_run_id")
    if (not isinstance(target_run_id, str) or len(target_run_id) != 32
            or mlflow_resolution.get("resolved_runs", {}).get("matches") != 1):
        raise FailClosedError("MLflow source run is unresolved or ambiguous")
    if mlflow_resolution.get("registry_state") != {
            "registered_models": 0, "model_versions": 0}:
        raise FailClosedError("MLflow registry is not empty before B5 attachment")

    groups = scope.get("promotion_languages") or {}
    allowed_groups = {"measured", "not_evaluated", "deferred", "not_applicable"}
    if set(groups) != allowed_groups:
        raise FailClosedError("promotion language groups are incomplete or unknown")
    group_sets = {name: set(values) for name, values in groups.items()}
    flattened: set[str] = set()
    for name, values in group_sets.items():
        if flattened & values:
            raise FailClosedError(f"promotion language groups overlap at {name}")
        flattened |= values
    registry_aliases = {p.stem for p in (root / "registry/languages").glob("*.yaml")}
    expected = registry_aliases - {"english", "french"}
    if flattened != expected:
        raise FailClosedError(
            f"promotion scope does not cover registry aliases: {sorted(expected ^ flattened)}")
    decision_languages = current_scope_decision.get("language_scope") or {}
    if decision_languages.get("active_training") != []:
        raise FailClosedError("current active training scope is not empty")
    if decision_languages.get("active_validation") != []:
        raise FailClosedError("current active validation scope is not empty")
    if group_sets["measured"]:
        raise FailClosedError("measured promotion group is not empty")
    if group_sets["deferred"] != set(decision_languages.get("deferred") or []):
        raise FailClosedError("deferred promotion group differs from scope decision")
    if group_sets["not_evaluated"] != set(
            decision_languages.get("inactive_not_evaluated") or []):
        raise FailClosedError(
            "not-evaluated promotion group differs from scope decision")

    resolved: dict[str, dict[str, Any]] = {}
    for alias in sorted(registry_aliases):
        resolved[alias] = resolve_thresholds(alias, root)
    auth_gates = auth["executable_gate_bindings"]
    _verify_hash(root / auth_gates["defaults"]["path"],
                 auth_gates["defaults"]["sha256"])
    for alias, binding in auth_gates["languages"].items():
        _verify_hash(root / binding["path"], binding["sha256"])

    source_ev = [_evidence(root / source_ref["path"], root)]
    conversion_ev = [_evidence(root / conversion_ref["path"], root)]
    termination_ev = [_evidence(termination_path, root),
                      _evidence(root / term_decision["path"], root)]
    decode_ev = [_evidence(decode_path, root)]

    languages: dict[str, Any] = {}
    selection_arm = conversion["selection_arms"]["ctranslate2_float16"]
    baseline = source["source_training"]["base_wer"]
    selected_failures = source["source_training"][
        "selected_checkpoint_termination_failures"]
    for alias in sorted(flattened):
        threshold = resolved[alias]
        lang_doc = threshold["language_document"]
        lang_evidence = threshold["files"]
        gates: list[dict[str, Any]] = []
        scope_state: GateState | None = None
        generated_scope = lang_doc.get("b4_b5_scope") or {}
        if generated_scope.get("decision_ref") != current_scope_ref["path"]:
            raise FailClosedError(
                f"{alias}: generated scope decision reference differs")
        if generated_scope.get("decision_sha256") != current_scope_ref["sha256"]:
            raise FailClosedError(
                f"{alias}: generated scope decision hash differs")
        expected_promotion_state = (
            GateState.DEFERRED.value if alias in group_sets["deferred"]
            else GateState.NOT_APPLICABLE.value
            if alias in group_sets["not_applicable"]
            else GateState.NOT_EVALUATED.value)
        if generated_scope.get("promotion_state") != expected_promotion_state:
            raise FailClosedError(
                f"{alias}: generated promotion state differs from gate scope")
        if alias in group_sets["deferred"]:
            scope_state = GateState.DEFERRED
            deferral = (current_scope_decision.get("per_language_deferral")
                        or {}).get(alias) or {}
            reason = deferral.get("reason")
            reactivation = deferral.get("reactivation_condition")
            if not reason or not reactivation:
                raise FailClosedError(
                    f"{alias}: deferral reason or reactivation condition missing")
            gates.append(_gate(
                "promotion_scope", GateState.DEFERRED,
                f"Language is DEFERRED. {reason} Reactivation condition: "
                f"{reactivation}",
                evidence=[_evidence(root / scope["decision_references"]["active_and_deferred_scope"]["path"], root)]))
        elif alias in group_sets["not_applicable"]:
            scope_state = GateState.NOT_APPLICABLE
            gates.append(_gate(
                "promotion_scope", GateState.NOT_APPLICABLE,
                "This registry language has no ASR capability and is outside this ASR artifact promotion.",
                evidence=lang_evidence))
        elif alias in group_sets["not_evaluated"]:
            scope_state = GateState.NOT_EVALUATED
            gates.append(_gate(
                "promotion_quality_evidence", GateState.NOT_EVALUATED,
                "Promotion-grade candidate measurements are absent; the language is unapproved, not failed or passed.",
                evidence=[_evidence(root / scope["decision_references"]["unevaluated_gate_matrix"]["path"], root)]))
            gates.append(_decode_gate(alias, lang_doc, decode, decode_ev + lang_evidence))
        else:
            candidate_wer = selection_arm["wer"].get(alias)
            base_wer = baseline.get(alias)
            relative_gain = None
            if candidate_wer is not None and base_wer is not None:
                base_numeric = _finite_number(base_wer, f"{alias} frozen baseline WER")
                if base_numeric <= 0:
                    raise FailClosedError(f"{alias}: baseline WER must be positive")
                relative_gain = (base_numeric - _finite_number(
                    candidate_wer, f"{alias} candidate WER")) / base_numeric
                recorded = _finite_number(selection_arm["relative_wer_gain"].get(alias),
                                          f"{alias} recorded relative gain")
                if abs(relative_gain - recorded) > 0.0001:
                    raise FailClosedError(f"{alias}: relative-gain evidence is inconsistent")
            asr_thresholds = threshold["values"]["asr"]
            threshold_ev = threshold["files"]
            gates.append(_compare("asr.relative_wer_gain", relative_gain,
                                  asr_thresholds["relative_wer_gain_min"], ">=",
                                  source_ev + conversion_ev + threshold_ev))
            gates[-1]["comparison"] = {
                "candidate_wer": candidate_wer,
                "frozen_baseline_wer": base_wer,
                "current_production": "ABSENT",
            }
            gates.append(_compare("asr.absolute_wer", candidate_wer,
                                  asr_thresholds["absolute_wer_max"], "<=",
                                  conversion_ev + threshold_ev))
            cer_measurements = selection_arm.get("cer")
            cer_gate = _absolute_cer_gate(
                "asr.absolute_cer",
                cer_measurements.get(alias)
                if isinstance(cer_measurements, dict) else None,
                asr_thresholds["absolute_cer_max"],
                conversion_ev + threshold_ev)
            if cer_gate is not None:
                gates.append(cer_gate)
            for missing_gate, key, operator in (
                    ("asr.slice_regression", "slice_regression_max", "<="),
                    ("asr.general_replay_regression", "general_replay_regression_max", "<="),
                    ("asr.named_entity_wer", "named_entity_wer_max", "<="),
                    ("asr.code_switch_wer_ratio", "code_switch_wer_ratio_max", "<="),
                    ("asr.code_switch_span_langid", "code_switch_span_langid_min", ">=")):
                gates.append(_compare(missing_gate, None, asr_thresholds[key],
                                      operator, threshold_ev))
            checkpoint_control = termination["checkpoint_selection"]
            gates.append(checkpoint_termination_gate([
                {"checkpoint": candidate_binding["selected_checkpoint"],
                 "failures": selected_failures.get(alias, [])}
            ], checkpoint_control["max_unique_failures_per_language_per_checkpoint"]))
            converted_failures: list[str] = []
            eos = _finite_number(selection_arm["eos_rate"].get(alias),
                                 f"{alias} post-conversion EOS rate")
            cap = _finite_number(selection_arm["cap_hit_rate"].get(alias),
                                 f"{alias} post-conversion cap-hit rate")
            if eos != 1.0 or cap != 0.0:
                return _fail_closed_report(
                    f"{alias}: aggregate termination failures exist but checksum evidence is absent",
                    auth_path, root)
            gates.append(post_conversion_termination_gate(
                selected_failures.get(alias, []), converted_failures,
                termination["post_conversion"]["max_unique_failures_per_language"]))
            gates[-2]["evidence"] = termination_ev + source_ev
            gates[-1]["evidence"] = termination_ev + source_ev + conversion_ev
            gates[-1]["measurement"].update({
                "eos_rate": eos,
                "cap_hit_rate": cap,
            })
            gates.append(_decode_gate(alias, lang_doc, decode,
                                      decode_ev + lang_evidence))
            if alias == "lingala":
                holdout = conversion["untouched_lingala_holdout"]
                holdout_gain = (_finite_number(holdout["same_precision_base_wer"],
                                               "holdout base WER")
                                - _finite_number(holdout["candidate_wer"],
                                                 "holdout candidate WER")) \
                    / _finite_number(holdout["same_precision_base_wer"],
                                     "holdout base WER")
                gates.append(_compare(
                    "asr.untouched_holdout.relative_wer_gain", holdout_gain,
                    asr_thresholds["relative_wer_gain_min"], ">=",
                    conversion_ev + threshold_ev))
                gates[-1]["comparison"] = {
                    "candidate_wer": holdout["candidate_wer"],
                    "frozen_baseline_wer": holdout["same_precision_base_wer"],
                    "current_production": "ABSENT",
                }
                gates.append(_compare(
                    "asr.untouched_holdout.absolute_wer", holdout["candidate_wer"],
                    asr_thresholds["absolute_wer_max"], "<=",
                    conversion_ev + threshold_ev))
                holdout_cer_gate = _absolute_cer_gate(
                    "asr.untouched_holdout.absolute_cer",
                    holdout.get("candidate_cer"),
                    asr_thresholds["absolute_cer_max"],
                    conversion_ev + threshold_ev)
                if holdout_cer_gate is not None:
                    gates.append(holdout_cer_gate)
                if (_finite_number(holdout["candidate_eos_rate"], "holdout EOS") != 1.0
                        or _finite_number(holdout["candidate_cap_hit_rate"],
                                          "holdout cap rate") != 0.0):
                    gates.append(_gate(
                        "termination.untouched_lingala_holdout", GateState.FAIL,
                        "Holdout aggregate shows a failure but checksum evidence is absent."))
                else:
                    gates.append(holdout_termination_gate(
                        [], termination["untouched_lingala_holdout"][
                            "max_unique_failures"]))
                    gates[-1]["evidence"] = termination_ev + conversion_ev
        gates.append(_production_comparison(alias, lang_doc, lang_evidence))
        state = _aggregate_language(scope_state, gates)
        languages[alias] = {
            "state": state.value,
            "gates": gates,
            "threshold_resolution": {
                "version": threshold["version"],
                "files": threshold["files"],
                "asr": threshold["values"]["asr"],
                "value_sources": {
                    k: v for k, v in threshold["value_sources"].items()
                    if k.startswith("asr.")
                },
            },
        }

    globals_: list[dict[str, Any]] = []
    for gate_id, entry in sorted(scope["global_required_gates"].items()):
        globals_.append(_gate(
            gate_id, parse_state(entry.get("state")), entry.get("reason", ""),
            required=entry.get("required_for_current_refusal", True),
            evidence=[_evidence(
                root / scope["decision_references"][
                    entry.get("decision_ref", "unevaluated_gate_matrix")]["path"], root)]
            if entry.get("decision_ref") else [_evidence(scope_path, root)]))

    all_gates = [g for row in languages.values() for g in row["gates"]] + globals_
    required_states = [parse_state(g["state"]) for g in all_gates
                       if g.get("required", True)]
    overall = ("PASS" if required_states and all(
        s in {GateState.PASS, GateState.NOT_APPLICABLE}
        for s in required_states) else "BLOCKED")
    if overall != "BLOCKED":
        raise FailClosedError("current negative test artifact unexpectedly passed")
    counts = {state.value: sum(1 for g in all_gates if g["state"] == state.value)
              for state in GateState}
    return {
        "schema_version": "medzen-b5-gate-report-v1",
        "content_address": "sha256 of the exact canonical JSON report bytes; filename is <sha256>.json",
        "authorization": _evidence(auth_path, root),
        "engine": {
            "git_commit": _git_head(root),
            "implementation": _evidence(engine_path, root),
            "entrypoint": _evidence(script_path, root),
        },
        "evaluation_purpose": "REFUSAL_ONLY_NEGATIVE_TEST_CASE",
        "candidate": copy.deepcopy(candidate_binding),
        "overall": overall,
        "overall_reason": (
            "Promotion remains blocked under the current scope: nine languages "
            "are owner-deferred and five trained languages remain "
            "NOT_EVALUATED for promotion. The immutable historical B5.1 "
            "BLOCKED report remains the quality evidence for the evaluated "
            "artifact and is not regenerated by this scheduling decision."),
        "languages": languages,
        "global_gates": globals_,
        "gate_state_counts": counts,
        "engine_errors": [],
        "mlflow_attachment": {
            "state": "PENDING_EXPLICIT_AWS_WRITE_APPROVAL",
            "target_run_id": target_run_id,
            "resolution_evidence": _evidence(root / mlflow_ref["path"], root),
            "registered_models_before": 0,
            "model_versions_before": 0,
            "registration_permitted": False,
        },
        "serving_resource_observations": {
            "evidence": [{
                "path": (candidate_binding["candidate_prefix"].split(
                    "/ctranslate2-float16/", 1)[0]
                    + "/artifact-evaluation.json"),
                "sha256": executed["artifact_evaluation_sha256"],
                "binding": "owner instruction plus immutable attempt-5 artifact evaluation",
            }],
            "ctranslate2_float16": {
                "artifact_bytes_on_disk": selection_arm["artifact_bytes"],
                "median_latency_seconds": 0.9252,
                "p95_latency_seconds": 1.3476,
                "lingala_relative_wer_gain": selection_arm[
                    "relative_wer_gain"]["lingala"],
            },
            "ctranslate2_int8_float16": {
                "artifact_bytes_on_disk": conversion["selection_arms"][
                    "ctranslate2_int8_float16"]["artifact_bytes"],
                "median_latency_seconds": 0.7972,
                "p95_latency_seconds": 1.0939,
                "lingala_relative_wer_gain": conversion["selection_arms"][
                    "ctranslate2_int8_float16"]["relative_wer_gain"]["lingala"],
            },
            "artifact_byte_difference": 1535958060,
            "artifact_byte_difference_is_gpu_memory_savings": False,
            "peak_l4_gpu_memory": "NOT_MEASURED",
            "float16_production_resource_deviation_complete": False,
        },
        "side_effects": copy.deepcopy(auth["required_outcome"]),
        "b5_completion": {
            "b5_1_engineering_and_report": "COMPLETE_PENDING_MLFLOW_ATTACHMENT",
            "b5_2_artifact_publication": "BLOCKED_NOT_APPLICABLE_FOR_CURRENT_ARTIFACT",
            "b5_3_registry_promotion": "BLOCKED_NOT_APPLICABLE_FOR_CURRENT_ARTIFACT",
            "b6": "BLOCKED",
        },
        "source_hashes": sorted(
            {_relative(p, root): sha256_file(p) for p in (
                auth_path, scope_path, termination_path, decode_path,
                engine_path, script_path, root / conversion_ref["path"],
                root / source_ref["path"], root / mlflow_ref["path"])}.items()),
    }


def _fail_closed_report(reason: str, auth_path: Path, root: Path) -> dict[str, Any]:
    auth: dict[str, Any] = {}
    try:
        auth = _load_json(auth_path)
    except FailClosedError:
        pass
    return {
        "schema_version": "medzen-b5-gate-report-v1",
        "content_address": "sha256 of the exact canonical JSON report bytes; filename is <sha256>.json",
        "evaluation_purpose": "FAIL_CLOSED_ENGINE_ERROR",
        "candidate": auth.get("candidate", {}),
        "overall": "BLOCKED",
        "overall_reason": reason,
        "languages": {},
        "global_gates": [
            _gate("engine.provenance_and_controls", GateState.FAIL, reason)
        ],
        "gate_state_counts": {
            state.value: (1 if state is GateState.FAIL else 0)
            for state in GateState
        },
        "engine_errors": [reason],
        "side_effects": auth.get("required_outcome", {
            "models_registered": 0,
            "objects_copied_to_approved_asr": 0,
            "language_artifact_changes": 0,
            "language_approved_version_changes": 0,
            "production_ssm_serving_alias_changes": 0,
            "deployments": 0,
            "b6_status": "BLOCKED",
        }),
    }


def evaluate_current_artifact(root: Path = ROOT) -> dict[str, Any]:
    """Always return a deterministic report; any engine error becomes BLOCKED."""
    auth_path = root / AUTH.relative_to(ROOT)
    try:
        return _verified_report(root)
    except (FailClosedError, KeyError, TypeError, OSError) as exc:
        return _fail_closed_report(str(exc), auth_path, root)
