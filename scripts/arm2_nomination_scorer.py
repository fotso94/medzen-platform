"""Arm-2 Phase-A nomination scorer v2 — the COMPLETE executable decision
pipeline, driven by ONE frozen, committed SCORING PACKET (Codex second-review
2026-08-25 finding 5: v1 accepted caller-chosen splits/vetoes/clusters/alphas
and caller-supplied edit counts; v2 authenticates every input).

Authority: statistical_procedure of
platform/decisions/B5-UNIVERSAL-ARM2-KD-COMPARISON-PROTOCOL-2026-001.json.

What the frozen scoring packet
(platform/decisions/B5-UNIVERSAL-ARM2-NOMINATION-SCORING-PACKET-2026-001.json)
pins, and this scorer enforces:

  - the FROZEN nomination split (path + artifact sha256)
  - per-surface REFERENCE files (identity -> text_normalized [+ speaker_id]),
    sha-pinned, committed, built from the exposure-index-pinned pool
    manifests and the committed veto records BEFORE any candidate results
  - the frozen CLUSTER map (placeholder-speaker rule applied pre-results)
  - candidate KD alphas READ FROM the six committed stage-1 packets
    (path + canonical sha pinned; never CLI-supplied)
  - bootstrap settings (B >= 10000, master_seed) and both margins
  - the receipt schema: per arm a JSON {job_name, model_sha256,
    packet_canonical_sha256, rows: [{audio_checksum_sha256, hyp_normalized}]}.
    Receipts carry HYPOTHESES ONLY — this scorer RECOMPUTES edit_distance and
    ref_words from the hypothesis vs the PINNED reference (word-level
    Levenshtein on whitespace tokens); caller-supplied edit counts REFUSE.
  - the arm->model identity rule: at scoring time an --arm-models JSON maps
    every arm to its REQUIRED export model_sha256 (from the arms' completion
    receipts); every receipts file's model_sha256 must equal its arm's entry.
  - the Stage-1 -> Stage-2 chain: stage2 takes the committed stage-1 RESULT
    (path + sha256) and DERIVES the finalist from it; a caller-chosen
    finalist is not an input.

Determinism: random.Random(derive_seed(master_seed, surface)) per surface;
ONE shared resample plan applied to every arm (paired). No wall-clock, no
environment reads. This scorer launches nothing and holds no credentials.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import subprocess
from pathlib import Path

from arm2_holm import candidate_qualifies, holm_reject, qualifies

SCORING_PACKET_PATH = (
    "platform/decisions/B5-UNIVERSAL-ARM2-NOMINATION-SCORING-PACKET-2026-001.json")

NOMINATION_LANGUAGES = ("english", "french", "pidgin", "swahili")
PRESERVATION_LANGUAGES = ("english", "french", "swahili")
VETO_SURFACES = ("lingala", "kinyarwanda", "ewe")
VETO_REFERENCE = {"lingala": "base", "kinyarwanda": "arm1", "ewe": "arm1"}
CANDIDATES = ("H1", "H2", "H3", "H4")
COMPARATORS = ("base", "arm1", "KD_CONTROL", "H0")
TEST_ORDER = ("preservation_english", "preservation_french",
              "preservation_swahili", "pidgin_retention",
              "beat_KD_CONTROL", "beat_H0")
NONINF_MARGIN = 0.005
VETO_MARGIN = 0.01
ALPHA = 0.05
MIN_B = 10000


class ScorerRefusal(SystemExit):
    """Any malformed, incomplete, unauthenticated or inconsistent input
    refuses loudly."""


def _committed_bytes(root: Path, rel: str) -> bytes:
    """Codex final correction (2026-08-26) items 1/2/4: trust-bearing inputs
    (the scoring packet, arm completion receipts, the stage-1 result) are read
    from the COMMITTED tree at HEAD — working-tree or caller-supplied bytes
    are never authoritative."""
    if rel.startswith("/") or ".." in rel:
        raise ScorerRefusal(f"non-repo-relative path {rel!r} refused")
    proc = subprocess.run(["git", "-C", str(root), "show", f"HEAD:{rel}"],
                          capture_output=True)
    if proc.returncode != 0:
        raise ScorerRefusal(f"{rel} is not committed at HEAD — the scorer "
                            "consumes only committed inputs")
    return proc.stdout


def _head_oid(root: Path) -> str:
    proc = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                          capture_output=True, text=True)
    oid = (proc.stdout or "").strip()
    if proc.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", oid):
        raise ScorerRefusal("cannot resolve the repo HEAD commit")
    return oid


def self_identity(root: Path) -> dict:
    """Item 1: the result binds the EXACT scorer + Holm-gate code that
    produced it (their committed shas must equal the running files)."""
    out = {}
    for rel in ("scripts/arm2_nomination_scorer.py", "scripts/arm2_holm.py"):
        running = (root / rel).read_bytes()
        committed = _committed_bytes(root, rel)
        if hashlib.sha256(running).hexdigest() !=                 hashlib.sha256(committed).hexdigest():
            raise ScorerRefusal(
                f"{rel} working bytes differ from the committed bytes — an "
                "uncommitted scorer cannot produce an authoritative decision")
        out[rel.split("/")[-1] + "_sha256"] =             hashlib.sha256(committed).hexdigest()
    return out


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def derive_seed(master_seed: int, surface: str) -> int:
    digest = hashlib.sha256(f"{master_seed}:{surface}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def word_edits(reference: str, hypothesis: str) -> tuple[int, int]:
    """Word-level Levenshtein distance on whitespace tokens + the reference
    word count — the packet-declared metric (recomputed here, never trusted
    from a caller)."""
    ref = reference.split()
    hyp = hypothesis.split()
    m, n = len(ref), len(hyp)
    prev = list(range(n + 1))
    for i in range(1, m + 1):
        cur = [i] + [0] * n
        for j in range(1, n + 1):
            cost = 0 if ref[i - 1] == hyp[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[n], m


# --------------------------------------------------------------------------
# scoring-packet loading (every pin verified)
# --------------------------------------------------------------------------

def _pinned_bytes(root: Path, pin: dict, what: str) -> bytes:
    rel = str(pin.get("path") or "")
    want = str(pin.get("sha256") or "")
    if not rel or not want:
        raise ScorerRefusal(f"scoring packet pins no {{path,sha256}} for {what}")
    raw = (root / rel).read_bytes()
    actual = _sha256_bytes(raw)
    if actual != want:
        raise ScorerRefusal(
            f"{what} {rel} hashes to {actual[:16]}, the scoring packet pins "
            f"{want[:16]} — refusing a substituted input")
    return raw


def load_scoring_packet(root: Path, path: Path, *,
                        references_dir: Path,
                        packet_bytes: bytes | None = None) -> dict:
    raw = packet_bytes if packet_bytes is not None else path.read_bytes()
    packet = json.loads(raw)
    if packet.get("record") != \
            "B5-UNIVERSAL-ARM2-NOMINATION-SCORING-PACKET-2026-001":
        raise ScorerRefusal("not the Arm-2 nomination scoring packet")
    cfg: dict = {"packet_sha256": _sha256_bytes(raw), "packet": packet,
                 "references_dir": str(references_dir)}

    # frozen split
    split_doc = json.loads(_pinned_bytes(root, packet["split"], "frozen split"))
    split = (split_doc.get("manifest") or {}).get("split") or {}
    cfg["split"] = {}
    for language in NOMINATION_LANGUAGES:
        rows = split.get(language)
        if not isinstance(rows, list) or not rows or len(set(rows)) != len(rows):
            raise ScorerRefusal(f"frozen split rows for {language!r} invalid")
        cfg["split"][language] = [str(r) for r in rows]

    # per-surface references (nomination + veto). Item 6 (licence
    # redistribution): reference transcripts are NOT in the repo — the packet
    # pins their exact S3 object (uri + VersionId) and sha256; the caller
    # fetches them to --references-dir and the SHA is the integrity boundary.
    refs_dir = Path(cfg["references_dir"])
    cfg["references"] = {}
    for surface in (*NOMINATION_LANGUAGES, *VETO_SURFACES):
        pin = packet["references"][surface]
        want = str(pin.get("sha256") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", want):
            raise ScorerRefusal(f"references[{surface}] pin lacks a sha256")
        local = refs_dir / f"references-{surface}.jsonl"
        if not local.exists():
            raise ScorerRefusal(
                f"references[{surface}] not fetched to {local} — fetch the "
                f"pinned object {pin.get('s3_uri')} (VersionId "
                f"{pin.get('s3_version_id')}) first")
        raw_refs = local.read_bytes()
        actual = _sha256_bytes(raw_refs)
        if actual != want:
            raise ScorerRefusal(
                f"references[{surface}] hash to {actual[:16]}, the scoring "
                f"packet pins {want[:16]} — refusing a substituted input")
        refs: dict[str, str] = {}
        for line in raw_refs.decode().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            identity = str(row.get("audio_checksum_sha256") or "")
            if len(identity) != 64 or identity in refs:
                raise ScorerRefusal(
                    f"references[{surface}]: malformed/duplicate identity")
            refs[identity] = str(row.get("text_normalized") or "")
        if not refs:
            raise ScorerRefusal(f"references[{surface}] empty")
        cfg["references"][surface] = refs
    # nomination reference coverage must equal the split exactly
    for language in NOMINATION_LANGUAGES:
        if set(cfg["references"][language]) != set(cfg["split"][language]):
            raise ScorerRefusal(
                f"references[{language}] do not cover the frozen split exactly")

    # frozen cluster map
    cl_doc = json.loads(_pinned_bytes(root, packet["clusters"], "cluster map"))
    cfg["clusters"] = {str(k): str(v)
                       for k, v in (cl_doc.get("clusters") or {}).items()}
    for language in NOMINATION_LANGUAGES:
        missing = [i for i in cfg["split"][language]
                   if i not in cfg["clusters"]]
        if missing:
            raise ScorerRefusal(
                f"cluster map misses {len(missing)} {language} rows")

    # candidate alphas from the six committed stage-1 packets
    cfg["alphas"] = {}
    for candidate, pin in sorted((packet.get("candidate_packets") or {}).items()):
        body = _pinned_bytes(root, pin, f"stage-1 packet[{candidate}]")
        doc = json.loads(body)
        canonical = hashlib.sha256(json.dumps(
            doc, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if canonical != str(pin.get("canonical_sha256") or ""):
            raise ScorerRefusal(
                f"stage-1 packet[{candidate}] canonical sha mismatch")
        cfg["alphas"][candidate] = float(doc["distillation"]["kd_alpha"])
    if set(cfg["alphas"]) != set(CANDIDATES):
        raise ScorerRefusal(
            f"scoring packet must pin exactly the candidate packets "
            f"{CANDIDATES}; got {sorted(cfg['alphas'])}")

    boot = packet.get("bootstrap") or {}
    cfg["B"] = int(boot.get("B") or 0)
    cfg["master_seed"] = int(boot.get("master_seed") or 0)
    if cfg["B"] < MIN_B:
        raise ScorerRefusal(f"scoring packet B={cfg['B']} < {MIN_B}")
    if not cfg["master_seed"]:
        raise ScorerRefusal("scoring packet lacks a master_seed")
    return cfg


# --------------------------------------------------------------------------
# authenticated receipts
# --------------------------------------------------------------------------

def verify_receipt_attestation(receipts_path: Path, bundle_path: Path, *,
                               evaluator: dict, workflow_run_ref: str,
                               gh_runner=None) -> dict:
    """Codex final-gap correction item 2: CRYPTOGRAPHIC verification of the
    receipts file's GitHub attestation — never URL strings. Runs
    `gh attestation verify <file> --bundle <bundle>` (offline Sigstore bundle
    verification against the repo's trusted root), then requires:
      - the in-toto subject digest == sha256(the exact receipts bytes);
      - the signing certificate's workflow identity == the packet-pinned
        protected evaluator workflow at refs/heads/master;
      - the certificate's run invocation matching the receipt's
        workflow_run_ref.
    `gh_runner` is an injectable seam (tests); the default shells out to gh."""
    repo = str(evaluator.get("repository") or "fotso94/medzen-platform")
    want_identity = str(evaluator.get("workflow_identity") or "")
    if not want_identity:
        raise ScorerRefusal("scoring packet pins no evaluator workflow_identity")
    if gh_runner is None:
        def gh_runner(args):
            proc = subprocess.run(["gh", *args], capture_output=True, text=True)
            return proc.returncode, proc.stdout, proc.stderr
    code, out, err = gh_runner([
        "attestation", "verify", str(receipts_path),
        "--bundle", str(bundle_path), "--repo", repo, "--format", "json"])
    if code != 0:
        raise ScorerRefusal(
            f"attestation verification FAILED for {receipts_path.name}: "
            f"{(err or out)[:200]} — unattested hypotheses cannot gate a "
            "nomination")
    try:
        results = json.loads(out)
    except ValueError:
        raise ScorerRefusal("attestation verifier returned non-JSON output")
    if not isinstance(results, list) or not results:
        raise ScorerRefusal("attestation verifier returned no verified bundle")
    res = results[0].get("verificationResult") or results[0]
    stmt = res.get("statement") or {}
    subject = (stmt.get("subject") or [{}])[0]
    digest = (subject.get("digest") or {}).get("sha256")
    actual = _sha256_bytes(receipts_path.read_bytes())
    if digest != actual:
        raise ScorerRefusal(
            f"attestation subject digest {str(digest)[:16]} != the receipts "
            f"file bytes {actual[:16]} — the attestation covers a DIFFERENT "
            "file")
    cert = (res.get("signature") or {}).get("certificate") or {}
    san = str(cert.get("subjectAlternativeName") or "")
    if san != want_identity:
        raise ScorerRefusal(
            f"attestation signer identity {san!r} != the packet-pinned "
            f"protected evaluator {want_identity!r}")
    run_uri = str(cert.get("runInvocationURI") or "")
    if workflow_run_ref and not workflow_run_ref.startswith(run_uri.split(
            "/attempts")[0]) and not run_uri.startswith(workflow_run_ref):
        raise ScorerRefusal(
            f"attestation run {run_uri!r} does not match the receipt's "
            f"workflow_run_ref {workflow_run_ref!r}")
    return {"subject_sha256": digest, "signer_identity": san,
            "run_invocation_uri": run_uri}


def load_receipts(path: Path, *, arm: str, expected_model_sha: str,
                  evaluator: dict, cfg: dict,
                  expected_model_artifact: dict | None = None,
                  expected_training_packet_sha: str | None = None,
                  bundle_path: Path | None = None,
                  gh_runner=None) -> tuple[dict[str, str], str, dict]:
    """One arm's hypothesis receipts, FULLY BOUND (item 3): protected-job
    name, cryptographically verified attestation over these exact bytes,
    model sha AND artifact VersionId (vs the arm's completion receipt), the
    arm's training-packet canonical sha, the evaluator image digest and the
    frozen split sha. Returns ({identity: hyp}, file sha, attestation facts).
    REFUSES caller-supplied edit counts."""
    raw = path.read_bytes()
    doc = json.loads(raw)
    for key in ("job_name", "model_sha256", "rows", "workflow_run_ref",
                "model_artifact", "split_sha256", "evaluator_image_digest"):
        if not doc.get(key):
            raise ScorerRefusal(f"receipts[{arm}] lack {key!r}")
    pattern = str(evaluator.get("job_name_pattern") or "")
    if pattern and not re.fullmatch(pattern, str(doc["job_name"])):
        raise ScorerRefusal(
            f"receipts[{arm}] job_name {doc['job_name']!r} does not match "
            f"the packet-declared protected-evaluator pattern {pattern!r}")
    if str(doc["model_sha256"]) != expected_model_sha:
        raise ScorerRefusal(
            f"receipts[{arm}] declare model {str(doc['model_sha256'])[:16]} "
            f"but the arm's completion receipt requires "
            f"{expected_model_sha[:16]} — refusing hypotheses from an "
            "unproven model")
    if expected_model_artifact is not None:
        got = doc.get("model_artifact") or {}
        for field in ("s3_uri", "s3_version_id"):
            if str(got.get(field)) != str(expected_model_artifact.get(field)):
                raise ScorerRefusal(
                    f"receipts[{arm}] model_artifact.{field} "
                    f"{got.get(field)!r} != the completion receipt's "
                    f"{expected_model_artifact.get(field)!r}")
    if expected_training_packet_sha is not None:
        if str(doc.get("training_packet_canonical_sha256") or "") != \
                expected_training_packet_sha:
            raise ScorerRefusal(
                f"receipts[{arm}] training_packet_canonical_sha256 does not "
                "match the arm's committed training packet")
    if str(doc["split_sha256"]) != \
            str((cfg["packet"].get("split") or {}).get("sha256")):
        raise ScorerRefusal(
            f"receipts[{arm}] split_sha256 != the frozen nomination split")
    want_image = str((cfg["packet"].get("evaluator") or {}).get(
        "image_digest") or "")
    if not want_image:
        raise ScorerRefusal(
            "the scoring packet pins no evaluator image_digest yet — the "
            "digest is pinned by a reviewed packet update BEFORE any scoring "
            "pass; receipts refuse until then (fail closed)")
    if str(doc["evaluator_image_digest"]) != want_image:
        raise ScorerRefusal(
            f"receipts[{arm}] evaluator_image_digest != the packet-pinned "
            "evaluator image")
    if bundle_path is None:
        raise ScorerRefusal(
            f"receipts[{arm}] have no attestation bundle — unattested "
            "hypotheses cannot gate a nomination")
    attestation = verify_receipt_attestation(
        path, bundle_path, evaluator=evaluator,
        workflow_run_ref=str(doc["workflow_run_ref"]), gh_runner=gh_runner)
    out: dict[str, str] = {}
    for row in doc["rows"]:
        if "edit_distance" in row or "ref_words" in row:
            raise ScorerRefusal(
                f"receipts[{arm}] carry caller-supplied edit counts — the "
                "scorer RECOMPUTES from hypotheses; precomputed numbers refuse")
        identity = str(row.get("audio_checksum_sha256") or "")
        if len(identity) != 64 or identity in out:
            raise ScorerRefusal(f"receipts[{arm}]: malformed/duplicate row")
        if "hyp_normalized" not in row:
            raise ScorerRefusal(f"receipts[{arm}]: row lacks hyp_normalized")
        out[identity] = str(row["hyp_normalized"])
    return out, _sha256_bytes(raw), attestation


def load_arm_completion_receipt(raw: bytes, *, arm: str) -> str:
    """Item 2: an arm's REQUIRED model identity comes from its COMMITTED,
    AWS-verified completion receipt — never a caller-supplied map. Returns
    export.model_sha256 after requiring the receipt's authoritative --live
    attestation to be an unambiguous PASS."""
    doc = json.loads(raw)
    export = doc.get("export") or {}
    model = str(export.get("model_sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", model):
        raise ScorerRefusal(f"arm receipt[{arm}] lacks export.model_sha256")
    auth = doc.get("authoritative_verification") or {}
    if auth.get("authoritative") is not True or auth.get("verdict") != "PASS" \
            or auth.get("failures") != [] or auth.get("mode") != "live":
        raise ScorerRefusal(
            f"arm receipt[{arm}] authoritative_verification is not an "
            "unambiguous live PASS — an unverified model cannot be scored")
    if doc.get("terminal_status") not in (None, "Completed"):
        raise ScorerRefusal(f"arm receipt[{arm}] terminal_status is not Completed")
    return {"model_sha256": model,
            "artifact": doc.get("artifact") or {},
            "training_packet_canonical_sha256":
                str(doc.get("calibration_bindings_sha256") or "") or None,
            "doc": doc}


def aws_reverify_completion(doc: dict, *, arm: str, session=None) -> None:
    """Codex final-gap correction item 4: a completion receipt's self-declared
    PASS is not enough — reverify the AWS-observable facts live: the training
    job exists, Completed, ran the receipt's image, produced the receipt's
    artifact URI; the artifact object exists at the EXACT VersionId with the
    receipt's byte size and KMS key. `session` is an injectable boto3-like
    seam (tests inject fakes; the default builds a real session)."""
    if session is None:
        import boto3
        session = boto3.session.Session(region_name="eu-central-1")
    job = str(doc.get("job") or "")
    if not job:
        raise ScorerRefusal(f"arm receipt[{arm}] names no job to reverify")
    desc = session.client("sagemaker").describe_training_job(
        TrainingJobName=job)
    if desc.get("TrainingJobStatus") != "Completed":
        raise ScorerRefusal(
            f"arm receipt[{arm}] job {job} is "
            f"{desc.get('TrainingJobStatus')!r} in AWS, not Completed")
    image = str((desc.get("AlgorithmSpecification") or {}).get(
        "TrainingImage") or "")
    if image != str(doc.get("image_uri_with_digest") or ""):
        raise ScorerRefusal(
            f"arm receipt[{arm}] image does not match the AWS job's image")
    artifact = doc.get("artifact") or {}
    aws_uri = str((desc.get("ModelArtifacts") or {}).get(
        "S3ModelArtifacts") or "")
    if aws_uri != str(artifact.get("s3_uri") or ""):
        raise ScorerRefusal(
            f"arm receipt[{arm}] artifact URI does not match the AWS job's")
    bucket, _, key = aws_uri.removeprefix("s3://").partition("/")
    head = session.client("s3").head_object(
        Bucket=bucket, Key=key, VersionId=str(artifact.get("s3_version_id")))
    if int(head.get("ContentLength") or -1) != int(artifact.get("s3_bytes")
                                                   or -2):
        raise ScorerRefusal(
            f"arm receipt[{arm}] artifact byte size does not match S3")
    if str(head.get("SSEKMSKeyId") or "") != str(artifact.get("kms_key")
                                                 or ""):
        raise ScorerRefusal(
            f"arm receipt[{arm}] artifact KMS key does not match S3")


def surface_rows(identities: list[str], hyps: dict[str, str],
                 refs: dict[str, str], *, arm: str, surface: str
                 ) -> list[tuple[int, int]]:
    """Exact coverage + RECOMPUTED (edits, ref_words) per row."""
    missing = [i for i in identities if i not in hyps]
    if missing:
        raise ScorerRefusal(
            f"arm {arm!r} receipts are missing {len(missing)} row(s) of the "
            f"{surface!r} surface (first: {missing[0][:12]}) — refusing a "
            "partial scoring")
    return [word_edits(refs[i], hyps[i]) for i in identities]


# --------------------------------------------------------------------------
# WER + paired cluster bootstrap (unchanged core)
# --------------------------------------------------------------------------

def corpus_wer(rows: list[tuple[int, int]]) -> float:
    edits = sum(r[0] for r in rows)
    words = sum(r[1] for r in rows)
    if words <= 0:
        raise ScorerRefusal("surface has zero reference words")
    return edits / words


def build_clusters(identities: list[str], cluster_map: dict[str, str] | None
                   ) -> list[list[int]]:
    if cluster_map is None:
        return [[i] for i in range(len(identities))]
    grouped: dict[str, list[int]] = {}
    for index, identity in enumerate(identities):
        cluster = cluster_map.get(identity)
        if cluster is None:
            raise ScorerRefusal(
                f"identity {identity[:12]} has no cluster assignment")
        grouped.setdefault(cluster, []).append(index)
    return [grouped[k] for k in sorted(grouped)]


def resample_plan(n_clusters: int, B: int, seed: int) -> list[list[int]]:
    rng = random.Random(seed)
    return [[rng.randrange(n_clusters) for _ in range(n_clusters)]
            for _ in range(B)]


def bootstrap_wers(rows_by_arm: dict[str, list[tuple[int, int]]],
                   clusters: list[list[int]], plan: list[list[int]]
                   ) -> dict[str, list[float]]:
    per_cluster: dict[str, list[tuple[int, int]]] = {}
    for arm, rows in rows_by_arm.items():
        per_cluster[arm] = [
            (sum(rows[i][0] for i in cluster),
             sum(rows[i][1] for i in cluster)) for cluster in clusters]
    out: dict[str, list[float]] = {arm: [] for arm in rows_by_arm}
    for draw in plan:
        for arm, cl in per_cluster.items():
            edits = sum(cl[j][0] for j in draw)
            words = sum(cl[j][1] for j in draw)
            out[arm].append(edits / words if words > 0 else float("inf"))
    return out


def one_sided_p_and_ci(samples: list[float], *, direction: str,
                       threshold: float) -> dict:
    n = len(samples)
    ordered = sorted(samples)
    if direction == "ge":
        p = sum(1 for x in samples if x >= threshold) / n
        return {"p": p, "upper_ci95": ordered[min(n - 1, int(0.95 * n))]}
    if direction == "le":
        p = sum(1 for x in samples if x <= threshold) / n
        ci = ordered[max(0, int(0.05 * n) - 1)] if n >= 20 else ordered[0]
        return {"p": p, "lower_ci95": ci}
    raise ScorerRefusal(f"unknown direction {direction!r}")


# --------------------------------------------------------------------------
# the decision pipeline (identical statistics to v1)
# --------------------------------------------------------------------------

def candidate_tests(candidate: str, wers) -> dict[str, dict]:
    tests: dict[str, dict] = {}
    for language in PRESERVATION_LANGUAGES:
        cand = wers[language][candidate]
        base = wers[language]["base"]
        diffs = [c - b for c, b in zip(cand, base)]
        tests[f"preservation_{language}"] = one_sided_p_and_ci(
            diffs, direction="ge", threshold=NONINF_MARGIN)
    cand = wers["pidgin"][candidate]
    arm1 = wers["pidgin"]["arm1"]
    diffs = [c - a for c, a in zip(cand, arm1)]
    tests["pidgin_retention"] = one_sided_p_and_ci(
        diffs, direction="ge", threshold=NONINF_MARGIN)
    for comparator, name in (("KD_CONTROL", "beat_KD_CONTROL"),
                             ("H0", "beat_H0")):
        reds = []
        for b in range(len(cand)):
            macro_cand = sum(wers[lang][candidate][b]
                             for lang in PRESERVATION_LANGUAGES) / 3.0
            macro_comp = sum(wers[lang][comparator][b]
                             for lang in PRESERVATION_LANGUAGES) / 3.0
            reds.append(macro_comp - macro_cand)
        tests[name] = one_sided_p_and_ci(reds, direction="le", threshold=0.0)
    return tests


def candidate_vetoes(candidate: str, veto_wers) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for surface in VETO_SURFACES:
        reference = VETO_REFERENCE[surface]
        cand = veto_wers[surface][candidate]
        ref = veto_wers[surface][reference]
        diffs = [c - r for c, r in zip(cand, ref)]
        summary = one_sided_p_and_ci(diffs, direction="ge",
                                     threshold=VETO_MARGIN)
        summary["vetoed"] = summary["upper_ci95"] > VETO_MARGIN
        summary["reference"] = reference
        out[surface] = summary
    return out


def stage1_decision(wers, veto_wers, *, alphas: dict[str, float]) -> dict:
    family: list[float] = []
    per_candidate: dict[str, dict] = {}
    for candidate in CANDIDATES:
        tests = candidate_tests(candidate, wers)
        vetoes = candidate_vetoes(candidate, veto_wers)
        per_candidate[candidate] = {"tests": tests, "vetoes": vetoes}
        family.extend(tests[name]["p"] for name in TEST_ORDER)
    mask = holm_reject(family, ALPHA)
    qualifiers = []
    for slot, candidate in enumerate(CANDIDATES):
        indices = list(range(slot * len(TEST_ORDER),
                             (slot + 1) * len(TEST_ORDER)))
        holm_ok = candidate_qualifies(family, indices, ALPHA)
        vetoed = any(v["vetoed"]
                     for v in per_candidate[candidate]["vetoes"].values())
        per_candidate[candidate]["holm_all_rejected"] = holm_ok
        per_candidate[candidate]["vetoed"] = vetoed
        per_candidate[candidate]["qualifies"] = holm_ok and not vetoed
        if holm_ok and not vetoed:
            qualifiers.append(candidate)
    decision: dict = {
        "family_pvalues": family,
        "family_order": [f"{c}:{t}" for c in CANDIDATES for t in TEST_ORDER],
        "holm_mask": mask,
        "per_candidate": per_candidate,
        "qualifiers": qualifiers,
    }
    if not qualifiers:
        decision["outcome"] = "NO_RECIPE_QUALIFIES"
        return decision

    def macro_point(candidate: str) -> float:
        return sum(wers[lang][candidate + "__point"][0]
                   for lang in PRESERVATION_LANGUAGES) / 3.0
    ranked = sorted(qualifiers,
                    key=lambda c: (macro_point(c), alphas[c], c))
    decision["tie_break"] = [
        {"candidate": c, "macro_point_wer": macro_point(c),
         "kd_alpha": alphas[c]} for c in ranked]
    decision["outcome"] = "PROVISIONAL_FINALIST"
    decision["provisional_finalist"] = ranked[0]
    return decision


def stage2_decision(finalist: str, wers, veto_wers) -> dict:
    tests = candidate_tests(finalist, wers)
    vetoes = candidate_vetoes(finalist, veto_wers)
    family = [tests[name]["p"] for name in TEST_ORDER]
    holm_ok = qualifies(family, ALPHA)
    vetoed = any(v["vetoed"] for v in vetoes.values())
    return {
        "finalist": finalist,
        "family_pvalues": family,
        "family_order": list(TEST_ORDER),
        "tests": tests, "vetoes": vetoes,
        "holm_all_rejected": holm_ok, "vetoed": vetoed,
        "outcome": ("FINAL_NOMINATION" if holm_ok and not vetoed
                     else "NO_RECIPE_QUALIFIES"),
    }


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------

def score_stage(*, stage: int, cfg: dict,
                hyps_by_arm: dict[str, dict[str, str]],
                finalist: str | None = None) -> dict:
    arms = (list(COMPARATORS)
            + (list(CANDIDATES) if stage == 1 else [str(finalist)]))
    missing_arms = [a for a in arms if a not in hyps_by_arm]
    if missing_arms:
        raise ScorerRefusal(f"receipts missing for arms {missing_arms}")
    B, seed = cfg["B"], cfg["master_seed"]
    wers: dict[str, dict[str, list[float]]] = {}
    for language in NOMINATION_LANGUAGES:
        identities = cfg["split"][language]
        refs = cfg["references"][language]
        clusters = build_clusters(identities, cfg["clusters"])
        plan = resample_plan(len(clusters), B,
                             derive_seed(seed, f"nom:{language}"))
        rows_by_arm = {arm: surface_rows(identities, hyps_by_arm[arm], refs,
                                         arm=arm, surface=language)
                       for arm in arms}
        wers[language] = bootstrap_wers(rows_by_arm, clusters, plan)
        for arm in arms:
            wers[language][arm + "__point"] = [corpus_wer(rows_by_arm[arm])]
    veto_wers: dict[str, dict[str, list[float]]] = {}
    for surface in VETO_SURFACES:
        refs = cfg["references"][surface]
        identities = sorted(refs)
        clusters = build_clusters(identities, None)      # ROW-level, always
        plan = resample_plan(len(clusters), B,
                             derive_seed(seed, f"veto:{surface}"))
        needed = ([VETO_REFERENCE[surface]]
                  + (list(CANDIDATES) if stage == 1 else [str(finalist)]))
        rows_by_arm = {arm: surface_rows(identities, hyps_by_arm[arm], refs,
                                         arm=arm, surface=surface)
                       for arm in needed}
        veto_wers[surface] = bootstrap_wers(rows_by_arm, clusters, plan)
    if stage == 1:
        return stage1_decision(wers, veto_wers, alphas=cfg["alphas"])
    return stage2_decision(str(finalist), wers, veto_wers)


def _load_bound_receipts(specs, *, cfg, arm_ids, evaluator, gh_runner,
                         what: str):
    """Shared loader for a set of ARM=PATH:BUNDLE hypothesis-receipt specs,
    fully bound + attested against each arm's completion identity."""
    hyps, shas, att = {}, {}, {}
    for spec in specs:
        arm, _, rest = spec.partition("=")
        path_s, _, bundle_s = rest.rpartition(":")
        if not path_s or not bundle_s:
            raise ScorerRefusal(
                f"{what}[{arm}] must be ARM=RECEIPTS_PATH:BUNDLE_PATH")
        if arm in hyps:
            raise ScorerRefusal(f"duplicate {what} for arm {arm!r}")
        if arm not in arm_ids:
            raise ScorerRefusal(f"--arm-receipts lacks an entry for {arm!r}")
        ident = arm_ids[arm]
        hyps[arm], shas[arm], att[arm] = load_receipts(
            Path(path_s), arm=arm,
            expected_model_sha=ident["model_sha256"],
            evaluator=evaluator, cfg=cfg,
            expected_model_artifact=ident["artifact"] or None,
            expected_training_packet_sha=ident[
                "training_packet_canonical_sha256"],
            bundle_path=Path(bundle_s), gh_runner=gh_runner)
    return hyps, shas, att


def main(argv: list[str] | None = None, *, gh_runner=None,
         aws_session=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("stage1", "stage2"))
    parser.add_argument("--scoring-packet", default=SCORING_PACKET_PATH)
    parser.add_argument("--scoring-packet-sha256", required=True)
    parser.add_argument("--references-dir", type=Path, required=True)
    parser.add_argument("--arm-receipts", action="append", default=[],
                        metavar="ARM=RELPATH", required=True,
                        help="per arm: the COMMITTED completion receipt; its "
                             "AWS-observable facts are REVERIFIED live "
                             "(item 4)")
    parser.add_argument("--receipts", action="append", default=[],
                        metavar="ARM=PATH:BUNDLE", required=True,
                        help="per arm: the protected evaluator's hypothesis "
                             "receipts + its Sigstore attestation bundle "
                             "(cryptographically verified; items 2-3)")
    parser.add_argument("--stage1-result", default=None)
    parser.add_argument("--stage1-result-sha256", default=None)
    parser.add_argument("--stage1-receipts", action="append", default=[],
                        metavar="ARM=PATH:BUNDLE",
                        help="stage2: the ORIGINAL stage-1 hypothesis "
                             "receipts for every stage-1 arm — stage 2 "
                             "RECOMPUTES the stage-1 decision from these "
                             "authenticated inputs and requires it to match "
                             "the committed result (item 5)")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.stage == "stage2" and (args.stage1_result is None
                                   or not args.stage1_result_sha256):
        raise ScorerRefusal(
            "stage2 requires --stage1-result + --stage1-result-sha256 — "
            "the finalist is DERIVED from the Stage-1 decision, never "
            "caller-chosen")

    root = Path(__file__).resolve().parents[1]
    identity = self_identity(root)
    head_oid = _head_oid(root)

    packet_bytes = _committed_bytes(root, str(args.scoring_packet))
    if _sha256_bytes(packet_bytes) != args.scoring_packet_sha256:
        raise ScorerRefusal(
            "the committed scoring packet hashes to "
            f"{_sha256_bytes(packet_bytes)[:16]}, the caller expected "
            f"{str(args.scoring_packet_sha256)[:16]} — refusing")
    cfg = load_scoring_packet(root, Path(args.scoring_packet),
                              references_dir=args.references_dir,
                              packet_bytes=packet_bytes)
    evaluator = (cfg["packet"].get("evaluator") or {})

    arm_ids: dict[str, dict] = {}
    arm_receipt_shas: dict[str, str] = {}
    for spec in args.arm_receipts:
        arm, _, rel = spec.partition("=")
        if arm in arm_ids:
            raise ScorerRefusal(f"duplicate arm receipt for {arm!r}")
        raw = _committed_bytes(root, rel)
        arm_ids[arm] = load_arm_completion_receipt(raw, arm=arm)
        aws_reverify_completion(arm_ids[arm]["doc"], arm=arm,
                                session=aws_session)
        arm_receipt_shas[arm] = _sha256_bytes(raw)

    finalist = None
    stage1_binding = None
    if args.stage == "stage2":
        s1_raw = _committed_bytes(root, str(args.stage1_result))
        if _sha256_bytes(s1_raw) != args.stage1_result_sha256:
            raise ScorerRefusal("stage-1 result sha mismatch")
        s1 = json.loads(s1_raw)
        if (s1.get("inputs") or {}).get("scoring_packet_sha256") != \
                cfg["packet_sha256"]:
            raise ScorerRefusal(
                "the stage-1 result was produced under a DIFFERENT scoring "
                "packet — the two stages must share one frozen packet")
        # item 5: RECOMPUTE Stage 1 from the authenticated stage-1 receipts
        # and require the recomputed decision to match the committed result.
        if not args.stage1_receipts:
            raise ScorerRefusal(
                "stage2 requires --stage1-receipts for EVERY stage-1 arm — "
                "the committed stage-1 result is verified by RECOMPUTATION, "
                "never trusted")
        s1_hyps, _, _ = _load_bound_receipts(
            args.stage1_receipts, cfg=cfg, arm_ids=arm_ids,
            evaluator=evaluator, gh_runner=gh_runner, what="stage1-receipts")
        recomputed = score_stage(stage=1, cfg=cfg, hyps_by_arm=s1_hyps)
        committed_decision = s1.get("decision") or {}
        for field in ("outcome", "provisional_finalist", "family_pvalues"):
            if recomputed.get(field) != committed_decision.get(field):
                raise ScorerRefusal(
                    f"stage-1 RECOMPUTATION disagrees with the committed "
                    f"result on {field!r} — the committed stage-1 decision "
                    "is not reproducible from its authenticated inputs")
        if recomputed.get("outcome") != "PROVISIONAL_FINALIST":
            raise ScorerRefusal(
                f"stage-1 outcome is {recomputed.get('outcome')!r} — stage2 "
                "runs only after a PROVISIONAL_FINALIST")
        finalist = str(recomputed.get("provisional_finalist"))
        if finalist not in CANDIDATES:
            raise ScorerRefusal(f"stage-1 finalist {finalist!r} invalid")
        stage1_binding = {"path": str(args.stage1_result),
                          "sha256": args.stage1_result_sha256,
                          "recomputed_and_matched": True}

    hyps_by_arm, receipt_shas, attestations = _load_bound_receipts(
        args.receipts, cfg=cfg, arm_ids=arm_ids, evaluator=evaluator,
        gh_runner=gh_runner, what="receipts")

    decision = score_stage(stage=1 if args.stage == "stage1" else 2,
                           cfg=cfg, hyps_by_arm=hyps_by_arm,
                           finalist=finalist)
    result = {
        "record": f"B5-UNIVERSAL-ARM2-NOMINATION-{args.stage.upper()}-DECISION",
        "protocol": "B5-UNIVERSAL-ARM2-KD-COMPARISON-PROTOCOL-2026-001",
        "inputs": {
            "scoring_packet": str(args.scoring_packet),
            "scoring_packet_sha256": cfg["packet_sha256"],
            "repo_head_oid": head_oid,
            **identity,
            "arm_completion_receipts_sha256": arm_receipt_shas,
            "arm_model_sha256": {a: i["model_sha256"]
                                 for a, i in arm_ids.items()},
            "aws_reverified": sorted(arm_ids),
            "receipts_sha256": receipt_shas,
            "receipt_attestations": attestations,
            "stage1_result": stage1_binding,
            "B": cfg["B"], "master_seed": cfg["master_seed"],
            "alpha": ALPHA, "noninferiority_margin": NONINF_MARGIN,
            "veto_margin": VETO_MARGIN,
        },
        "decision": decision,
    }
    payload = json.dumps(result, indent=1, sort_keys=True).encode() + b"\n"
    args.out.write_bytes(payload)
    print(json.dumps({"outcome": decision["outcome"], "out": str(args.out),
                      "result_sha256": _sha256_bytes(payload)},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
