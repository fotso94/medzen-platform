"""Round 13 (Codex): rehearse the ENTIRE admission path — assemble,
completely verify, sign the evidence root, and runtime-verify — against
immutable synthetic evidence, before any sealed evaluation exists.
The assembler is driven exactly as the workflow drives it; only the
live AWS fetchers, the signer and the committed authorities are
injected (the real registry is EMPTY by design and would refuse)."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/model-loader"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import assemble_promotion_bundle as assembler  # noqa: E402
import b7_model_promotion_check as admission  # noqa: E402
from medzen_model_loader.loader_v2 import artifact_tree_sha256  # noqa: E402
from test_b6v2_real_provider import (  # noqa: E402
    SEALED_OUTPUT_STORE, _promotion_bundle, _stub_output_fetch, _test_keypair,
    _test_sign, patch_evaluator_pubkey)


def _synthetic_inputs(tmp_path, monkeypatch):
    """Lay out the synthetic evidence as separate INPUT files (the way
    an admission run receives them) from the proven bundle fixture."""
    tree = artifact_tree_sha256("ab" * 32, "12" * 32)
    src, _ = _promotion_bundle(tmp_path / "src", tree)
    inputs = tmp_path / "inputs"
    results, manifests = inputs / "results", inputs / "manifests"
    results.mkdir(parents=True), manifests.mkdir()
    for path in src.iterdir():
        if path.name.endswith(".rows.jsonl"):
            (results / path.name).write_bytes(path.read_bytes())
        elif path.name.endswith(".holdout-manifest.jsonl"):
            (manifests / path.name).write_bytes(path.read_bytes())
    grades = json.loads((src / "HOLDOUT-GRADES.json").read_bytes())
    bindings = json.loads((src / "HOLDOUT-BINDINGS.json").read_bytes())
    holdouts = {lang: {e["sha256"] for e in entries}
                for lang, entries in bindings.items()}
    documents = {name: (src / name).read_bytes() for name in (
        "platform__decisions__SYNTHETIC-CS-LICENSE.json",
        "platform__ledger__SYNTHETIC-CS-RESERVATION.json")}
    packet = json.loads((src / "CANDIDATE-PACKET.json").read_bytes())
    contract = packet["sealed_run"]
    described = {k: v for k, v in contract.items()
                 if k not in ("job_name", "account_id", "region")}
    described.update({
        "creation_utc": "2026-08-23T12:00:00Z",
        "end_utc": "2026-08-23T13:00:00Z", "status": "Completed",
        "arn": (f"arn:aws:sagemaker:{contract['region']}:"
                f"{contract['account_id']}:training-job/"
                f"{contract['job_name']}"),
        "output_artifact_s3_uri": None})
    packet_bytes = (src / "CANDIDATE-PACKET.json").read_bytes()
    # --- inject ONLY the live authorities/fetchers
    monkeypatch.setattr(assembler, "GRADES_PATH", src / "HOLDOUT-GRADES.json")
    monkeypatch.setattr(admission, "_grade_authority",
                        lambda: dict(grades["grades"]))
    monkeypatch.setattr(admission, "_licensed_code_switch_sets",
                        lambda: dict(grades["licensed_code_switch_sets"]))
    monkeypatch.setattr(admission, "_authoritative_holdouts_by_language",
                        lambda: holdouts)
    monkeypatch.setattr(
        admission, "_document_bytes",
        lambda path: documents.get(str(path).lstrip("/").replace("/", "__")))
    monkeypatch.setattr(admission, "_anchor_fetch",
                        lambda storage: (packet_bytes, "2026-08-23T10:00:00Z"))
    monkeypatch.setattr(admission, "_sealed_start_fetch",
                        lambda job: described)
    monkeypatch.setattr(admission, "_output_object_fetch", _stub_output_fetch)
    # the runtime verifier resolves the TEST public key (production
    # resolves the baked/committed pem — no env override exists)
    import medzen_model_loader.signing as signing
    from cryptography.hazmat.primitives.serialization import (
        Encoding, PublicFormat)
    pem = _test_keypair().public_key().public_bytes(
        Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    monkeypatch.setattr(signing, "_public_key_bytes", lambda: pem)
    patch_evaluator_pubkey(monkeypatch)
    # a local signer command standing in for the KMS signer
    from cryptography.hazmat.primitives.serialization import (
        NoEncryption, PrivateFormat)
    key_pem = _test_keypair().private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    (tmp_path / "test-key.pem").write_bytes(key_pem)
    signer = tmp_path / "local_signer.py"
    signer.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "from cryptography.hazmat.primitives import hashes\n"
        "from cryptography.hazmat.primitives.asymmetric import ec\n"
        "from cryptography.hazmat.primitives.serialization import load_pem_private_key\n"
        f"key = load_pem_private_key(Path({str(tmp_path / 'test-key.pem')!r}).read_bytes(), None)\n"
        "doc = Path(sys.argv[1])\n"
        "doc.with_suffix(doc.suffix + '.sig').write_bytes("
        "key.sign(doc.read_bytes(), ec.ECDSA(hashes.SHA256())))\n")
    review = inputs / "review.json"
    review.write_text(json.dumps({"status": "PASS", "findings": 0,
                                  "reviewer": "codex-independent-review"}))
    authorization = inputs / "authorization.json"
    authorization.write_text(json.dumps({
        "statement": f"owner authorizes promotion of {tree[:12]}",
        "recorded_utc": "2026-08-23T00:00:00Z"}))
    argv = ["assemble_promotion_bundle.py",
            "--gate-report", str(src / "T6-GATE-REPORT.json"),
            "--results-dir", str(results),
            "--candidate-packet", str(src / "CANDIDATE-PACKET.json"),
            "--manifests-dir", str(manifests),
            "--artifact-tree", tree,
            "--anchor-envelope", str(src / "ANCHOR-ENVELOPE.json"),
            "--independent-review", str(review),
            "--owner-authorization", str(authorization),
            "--output-dir", str(tmp_path / "bundle"),
            "--signer", f"{sys.executable} {signer}"]
    return tree, argv, src


def test_full_admission_rehearsal_assembles_signs_and_runtime_verifies(
        tmp_path, monkeypatch, capsys):
    tree, argv, _src = _synthetic_inputs(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "argv", argv + ["--sign"])
    assert assembler.main() == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["status"] == "ASSEMBLED"
    assert out["signed"] is True and out["runtime_verified"] is True
    bundle = tmp_path / "bundle"
    index = json.loads((bundle / "bundle.json").read_bytes())
    assert hashlib.sha256((bundle / "bundle.json").read_bytes()).hexdigest() == (
        out["MEDZEN_PROMOTION_BUNDLE_SHA256"])
    assert (bundle / "bundle.json.sig").is_file()
    assert (bundle / "HOLDOUT-GRADES.json.sig").is_file()
    assert not any(n.endswith(".py") for n in index["files"])
    # the bundle is exactly what the runtime loader then accepts
    for name, sha in index["files"].items():
        assert hashlib.sha256((bundle / name).read_bytes()).hexdigest() == sha
    receipt = json.loads((bundle / "ADMISSION-RECEIPT.json").read_bytes())
    assert receipt["sealed_outputs"]["rows"]["english"]["version_id"] == "v-english"
    pins = json.loads((bundle / "ADMISSION-PINS.json").read_bytes())
    assert pins["artifact_tree_sha256"] == tree


def test_rehearsal_refuses_when_the_job_output_is_not_the_bundled_rows(
        tmp_path, monkeypatch, capsys):
    tree, argv, _src = _synthetic_inputs(tmp_path, monkeypatch)
    for key, (body, modified) in list(SEALED_OUTPUT_STORE.items()):
        if key[0].endswith("english.rows.jsonl"):
            SEALED_OUTPUT_STORE[key] = (body + b"\n", modified)
    monkeypatch.setattr(sys, "argv", argv + ["--sign"])
    assert assembler.main() == 1
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["status"] == "REFUSED"
    assert "do not hash" in out["detail"]
    assert not (tmp_path / "bundle" / "bundle.json").exists()


def test_rehearsal_deletes_a_bundle_the_runtime_refuses(tmp_path, monkeypatch, capsys):
    """If the signer signed with a key the runtime does not trust, the
    runtime verifier refuses and the assembled bundle is DELETED."""
    tree, argv, _src = _synthetic_inputs(tmp_path, monkeypatch)
    import medzen_model_loader.signing as signing
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import (
        Encoding, PublicFormat)
    other = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
        Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    monkeypatch.setattr(signing, "_public_key_bytes", lambda: other)
    monkeypatch.setattr(sys, "argv", argv + ["--sign"])
    assert assembler.main() == 1
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert "RUNTIME verifier refused" in out["detail"]
    assert not (tmp_path / "bundle").exists()


def test_unsigned_assembly_never_claims_verification(tmp_path, monkeypatch, capsys):
    tree, argv, _src = _synthetic_inputs(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "argv", argv)
    assert assembler.main() == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["signed"] is False and out["runtime_verified"] is False
