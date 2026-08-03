"""Fail-closed policy: allowed_use, manifest versions, duplicates, label limits.

Each of these guards exists because its absence caused a real failure:

  allowed_use   was required by the schema, set at ingest, enforced nowhere --
                so an ASR run trained on 2,305 TTS-licensed rows unnoticed.
  one version   mixing manifest versions produces a fingerprint describing no
                single state of the corpus, and double-counts the same audio.
  duplicates    the same clip twice silently reweights a language against the
                temperature schedule.
  label limit   one over-length transcript killed a 600-step run at step 59;
                dropping such rows at runtime would silently change the training
                set, so the run refuses unless they are explicitly excluded.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TRAIN = ROOT / "pipeline" / "train_asr.py"
MIGRATE = ROOT / "scripts" / "migrate_allowed_use.py"


def _row(sha: str, split: str = "train", use=("asr_train", "asr_eval"), **kw):
    r = {"audio_checksum_sha256": sha, "split": split, "allowed_use": list(use),
         "duration_s": 5.0, "text_normalized": "hello", "audio_filepath":
         f"s3://medzen-speech/curated/x/asr/x/v1/audio/{sha}.wav"}
    r.update(kw)
    return r


def _cli(manifests: dict[str, list[dict]]):
    """manifests: {s3 key -> rows}"""
    cli = MagicMock()
    cli.list_objects_v2.return_value = {
        "Contents": [{"Key": k, "Size": 1} for k in manifests], "IsTruncated": False}

    def get_object(Bucket, Key):
        body = "\n".join(json.dumps(r) for r in manifests[Key]).encode()
        return {"Body": MagicMock(read=lambda: body)}

    cli.get_object.side_effect = get_object
    return cli


def _load(manifests, **kw):
    """Load with a VALID adopted completion record, so these tests exercise the
    allowed_use / version / duplicate rules rather than the adoption gate.
    The adoption gate has its own tests below."""
    import pipeline.train_asr as T
    version = kw.get("version", "v1")
    scoped = {k: v for k, v in manifests.items() if f"/{version}/" in k}
    cli = _cli_with_completion(manifests, _completion(scoped or manifests))
    with patch.object(T, "list_manifests", lambda c: list(manifests)):
        return T.load_mix(cli, 0.5, 0, None, **kw)


V1 = "curated/pidgin/tts/pcm_tts/v1/manifest.jsonl"
V2 = "curated/pidgin/tts/pcm_tts/v2/manifest.jsonl"


def test_tts_only_rows_are_rejected_without_asr_train():
    """The hole that let an ASR run consume TTS-licensed data."""
    rows = [_row("a" * 64, use=("tts_train", "tts_eval"))]
    with pytest.raises(SystemExit) as e:
        _load({V1: rows}, version="v1")
    assert "no rows permit 'asr_train'" in str(e.value)


def test_rows_are_accepted_once_the_versioned_manifest_grants_asr_train():
    rows = [_row("a" * 64, use=("tts_train", "tts_eval", "asr_train")),
            _row("b" * 64, use=("tts_train", "tts_eval", "asr_train"))]
    mix, prov = _load({V2: rows}, version="v2")
    assert len(mix) == 2
    assert prov["require_allowed_use"] == "asr_train"
    assert prov["manifest_version"] == "v2"
    assert prov["rejected"]["not_permitted"] == 0


def test_non_train_rows_are_rejected_even_when_permitted():
    rows = [_row("a" * 64, split="test", use=("asr_train",)),
            _row("b" * 64, split="train", use=("asr_train",))]
    mix, prov = _load({V1: rows}, version="v1")
    assert len(mix) == 1
    assert prov["rejected"]["wrong_split"] == 1


def test_a_missing_version_is_refused_rather_than_silently_empty():
    with pytest.raises(SystemExit) as e:
        _load({V1: [_row("a" * 64)]}, version="v9")
    assert "no manifests found at version 'v9'" in str(e.value)


def test_only_the_requested_version_is_read_and_others_are_recorded():
    """Mixing versions would double-count the same audio."""
    rows_v1 = [_row("a" * 64, use=("asr_train",))]
    rows_v2 = [_row("a" * 64, use=("asr_train",)), _row("b" * 64, use=("asr_train",))]
    mix, prov = _load({V1: rows_v1, V2: rows_v2}, version="v2")
    assert len(mix) == 2, "only v2 rows may be used"
    assert prov["other_versions_present_but_unused"] == ["v1"]


def test_duplicate_rows_across_corpora_are_refused():
    same = "c" * 64
    a = "curated/pidgin/tts/pcm_tts/v1/manifest.jsonl"
    b = "curated/hausa/tts/hau_tts/v1/manifest.jsonl"
    with pytest.raises(SystemExit) as e:
        _load({a: [_row(same, use=("asr_train",))],
               b: [_row(same, use=("asr_train",))]}, version="v1")
    assert "duplicate row" in str(e.value)


def test_manifest_provenance_records_version_and_hashes():
    rows = [_row("a" * 64, use=("asr_train",))]
    _mix, prov = _load({V1: rows}, version="v1")
    entry = next(iter(prov["manifests"].values()))
    assert entry["version"] == "v1"
    assert len(entry["manifest_sha256"]) == 64
    src = TRAIN.read_text()
    assert '"manifest_provenance": mix_provenance' in src
    assert src.count('"manifest_provenance": mix_provenance') == 2, \
        "must reach BOTH the MLflow params and run.json"


# --------------------------------------------------------------------------- #
# label limit: refuse, never silently drop
# --------------------------------------------------------------------------- #
def test_over_limit_rows_refuse_the_run_rather_than_being_dropped():
    src = TRAIN.read_text()
    assert "REFUSING:" in src and "not in a reviewed exclusion list" in src
    assert "Do not truncate labels" in src
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert "DROPPED" not in code, "the silent dynamic drop must be gone"


def test_label_guard_uses_the_shared_prefix_aware_function():
    """A guard that measures differently from the audit is not a guard."""
    src = TRAIN.read_text()
    assert "from pipeline.label_length import decoder_start_id, label_lengths" in src
    assert "label_lengths(processor.tokenizer" in src
    assert "max_labels = model.config.max_target_positions" in src


def test_exclusion_list_must_be_approved(tmp_path):
    """tmp_path, not the repo: a test that writes into the source tree cannot
    run against a read-only checkout, which is how the verified bundle is
    mounted on the evaluation instance."""
    import pipeline.train_asr as T
    doc = {"list_id": "X", "status": "draft", "exclusions": []}
    p = tmp_path / "excl.json"
    p.write_text(json.dumps(doc))
    with pytest.raises(SystemExit) as e:
        T.load_exclusions(str(p))
    assert "not approved" in str(e.value)


def test_exclusion_entries_carry_a_category_and_reason():
    src = TRAIN.read_text()
    assert "category" in src and "reason" in src


# --------------------------------------------------------------------------- #
# migration refusals
# --------------------------------------------------------------------------- #
def test_migration_refuses_same_version():
    s = MIGRATE.read_text()
    assert "from-version == to-version" in s


def test_migration_pins_expected_totals():
    s = MIGRATE.read_text()
    assert "EXPECT_CONFIGS = 9" in s
    assert "EXPECT_CHANGED_ROWS = 2305" in s
    assert 'tot["changed"] != EXPECT_CHANGED_ROWS' in s
    assert 'tot["configs_changed"] != EXPECT_CONFIGS' in s


def test_migration_grants_only_train_rows():
    s = MIGRATE.read_text()
    assert 'GRANT_SPLIT = "train"' in s
    assert 'rec.get("split") == GRANT_SPLIT' in s


def test_migration_never_adds_asr_eval():
    s = MIGRATE.read_text()
    assert 'NEVER_GRANT = "asr_eval"' in s
    assert "was newly added — forbidden" in s


def test_migration_refuses_existing_destination():
    s = MIGRATE.read_text()
    assert "already exist" in s and "immutable once written" in s


def test_migration_validates_everything_before_uploading():
    s = MIGRATE.read_text()
    assert s.index("phase 1: generate and validate") < s.index("phase 4: upload")
    assert s.index("phase 3: destination must be untouched") < s.index("phase 4: upload")


def test_migration_writes_the_completion_record_last():
    s = MIGRATE.read_text()
    assert s.index('cli.put_object(Bucket=BUCKET, Key=p["dst_key"]') < \
        s.index("Key=completion_key(a.to_version)")
    assert "loader_contract" in s


def test_migration_claims_field_value_not_byte_equivalence():
    """Canonical JSON reorders keys, so the bytes of an unchanged row differ."""
    s = MIGRATE.read_text()
    assert "NOT byte equivalence" in s or "NOT byte-equivalence" in s
    assert "field-value equivalence" in s
    assert "PINNED_FIELDS" in s


def test_migration_writes_lineage_for_every_manifest():
    s = MIGRATE.read_text()
    assert '"lineage_key"' in s
    code = "\n".join(l for l in s.splitlines() if not l.lstrip().startswith("#"))
    assert 'Key=p["lineage_key"]' in code
    assert "carried forward unchanged" in s, "unchanged corpora need lineage too"


# --------------------------------------------------------------------------- #
# a version is usable only if its migration completed AND was adopted
# --------------------------------------------------------------------------- #
DERIVE = object()          # default: a valid adoption bound to the completion


def _adoption(completion, status="approved", bound=True, policy=None):
    """An adoption record as the approver would produce it.

    Binds the RAW BYTES the bucket serves -- the same json.dumps(completion)
    the fake client returns -- not a re-serialisation of the parsed dict."""
    import hashlib
    return {
        "status": status,
        "complete_raw_sha256": (
            hashlib.sha256(json.dumps(completion).encode()).hexdigest()
            if bound else "0" * 64),
        **({"deferral_policy_sha256": policy} if policy else {}),
    }


def _cli_with_completion(manifests, completion, adoption=DERIVE):
    cli = _cli(manifests)
    orig = cli.get_object.side_effect
    if adoption is DERIVE:
        adoption = _adoption(completion) if completion is not None else None

    def get_object(Bucket, Key):
        if Key.endswith("COMPLETE.json"):
            if completion is None:
                raise RuntimeError("NoSuchKey")
            body = json.dumps(completion).encode()
            return {"Body": MagicMock(read=lambda: body)}
        if Key.endswith("ADOPTION.json"):
            if adoption is None:
                raise RuntimeError("NoSuchKey")
            body = json.dumps(adoption).encode()
            return {"Body": MagicMock(read=lambda: body)}
        return orig(Bucket=Bucket, Key=Key)

    cli.get_object.side_effect = get_object
    return cli


def _load_v(manifests, completion, adoption=DERIVE, **kw):
    import pipeline.train_asr as T
    cli = _cli_with_completion(manifests, completion, adoption)
    with patch.object(T, "list_manifests", lambda c: list(manifests)):
        return T.load_mix(cli, 0.5, 0, None, **kw)


# --- adoption is a separate decision from completion ---------------------- #
def test_completed_version_without_an_adoption_record_is_refused():
    """COMPLETE.json only says the writing finished. It is not approval."""
    rows = {V2: [_row("a" * 64, use=("asr_train",))]}
    with pytest.raises(SystemExit) as e:
        _load_v(rows, _completion(rows), adoption=None, version="v2")
    assert "no adoption record" in str(e.value)
    assert "not an approved one" in str(e.value)


def test_unapproved_adoption_record_is_refused():
    rows = {V2: [_row("a" * 64, use=("asr_train",))]}
    comp = _completion(rows)
    with pytest.raises(SystemExit) as e:
        _load_v(rows, comp, adoption=_adoption(comp, status="draft"), version="v2")
    assert "not 'approved'" in str(e.value)


def test_adoption_bound_to_a_different_completion_record_is_refused():
    """Approve v2, then change v2: the approval must not carry over."""
    rows = {V2: [_row("a" * 64, use=("asr_train",))]}
    comp = _completion(rows)
    with pytest.raises(SystemExit) as e:
        _load_v(rows, comp, adoption=_adoption(comp, bound=False), version="v2")
    assert "changed after it was adopted" in str(e.value)


def _completion(manifests, adopted=True):
    import hashlib
    out = {"adopted": adopted, "manifests": {}}
    for key, rows in manifests.items():
        _, lang, task, cfg, _v, _ = key.split("/")
        body = "\n".join(json.dumps(r) for r in rows).encode()
        out["manifests"][f"{lang}/{task}/{cfg}"] = {
            "sha256": hashlib.sha256(body).hexdigest()}
    return out


def test_missing_completion_record_is_refused():
    """It is written last, so its absence means an interrupted migration."""
    rows = {V2: [_row("a" * 64, use=("asr_train",))]}
    with pytest.raises(SystemExit) as e:
        _load_v(rows, None, version="v2")
    assert "no completion record" in str(e.value)


def test_completion_record_does_not_get_a_vote_on_adoption():
    """COMPLETE.json is written when the migration finishes. Approval happens
    afterwards, so a field inside it cannot attest to approval -- and requiring
    one meant the migration had to predict its own review. adopted=false here is
    ignored; the separate ADOPTION.json decides."""
    rows = {V2: [_row("a" * 64, use=("asr_train",))]}
    comp = _completion(rows, adopted=False)
    mix, prov = _load_v(rows, comp, version="v2")
    assert len(mix) == 1
    assert prov["complete_raw_sha256"]


def test_adoption_binds_raw_bytes_not_a_reserialisation():
    """A dict that round-trips through json.dumps is a different byte string.
    An adoption hashing the re-serialised form would accept a bucket object it
    never saw, so the loader must compare against the bytes actually served."""
    import hashlib
    rows = {V2: [_row("a" * 64, use=("asr_train",))]}
    # keys deliberately NOT in sorted order, so the two encodings differ
    base = _completion(rows)
    comp = {"manifests": base["manifests"], "adopted": base["adopted"]}
    reser = hashlib.sha256(json.dumps(comp, sort_keys=True).encode()).hexdigest()
    raw = hashlib.sha256(json.dumps(comp).encode()).hexdigest()
    assert reser != raw, "fixture must actually differ, or this proves nothing"
    with pytest.raises(SystemExit) as e:
        _load_v(rows, comp,
                adoption={"status": "approved", "complete_raw_sha256": reser},
                version="v2")
    assert "changed after it was adopted" in str(e.value)


def test_manifest_hash_mismatch_is_refused():
    rows = {V2: [_row("a" * 64, use=("asr_train",))]}
    comp = _completion(rows)
    comp["manifests"]["pidgin/tts/pcm_tts"]["sha256"] = "0" * 64
    with pytest.raises(SystemExit) as e:
        _load_v(rows, comp, version="v2")
    assert "does not match" in str(e.value)


def test_manifest_absent_from_completion_record_is_refused():
    rows = {V2: [_row("a" * 64, use=("asr_train",))]}
    comp = _completion(rows)
    comp["manifests"] = {}
    with pytest.raises(SystemExit) as e:
        _load_v(rows, comp, version="v2")
    assert "not listed" in str(e.value)


def test_adopted_and_matching_version_loads():
    rows = {V2: [_row("a" * 64, use=("asr_train",)), _row("b" * 64, use=("asr_train",))]}
    mix, prov = _load_v(rows, _completion(rows), version="v2")
    assert len(mix) == 2 and prov["manifest_version"] == "v2"


def test_forced_decoder_ids_is_unconditional():
    """An edit once left it inside `if excluded:`, so a run without an exclusion
    list never cleared it and would train against a fixed decoder prefix."""
    import ast
    tree = ast.parse(TRAIN.read_text())

    class V(ast.NodeVisitor):
        def __init__(self):
            self.depth = 0
            self.found = []

        def visit_If(self, n):
            self.depth += 1
            self.generic_visit(n)
            self.depth -= 1

        def visit_Assign(self, n):
            t = n.targets[0]
            if isinstance(t, ast.Attribute) and t.attr == "forced_decoder_ids":
                self.found.append(self.depth)
            self.generic_visit(n)

    v = V()
    v.visit(tree)
    assert v.found == [0], f"forced_decoder_ids assigned at if-depth {v.found}, must be 0"


def test_audit_defaults_are_fail_closed():
    s = (ROOT / "scripts" / "audit_label_lengths.py").read_text()
    assert 'ap.add_argument("--version", default="v2"' in s
    assert 'ap.add_argument("--require-allowed-use", default="asr_train"' in s
    assert 'ap.add_argument("--split", default="train")' in s
    assert "REFUSING: no completion record" in s


def test_audit_distinguishes_source_pool_from_sampled_mix():
    """4,620 is the eligible pool; the temperature-sampled mix is 4,619 at
    seed 0 -- conflating them makes a fingerprint look wrong."""
    s = (ROOT / "scripts" / "audit_label_lengths.py").read_text()
    assert "eligible_source_pool_rows" in s
    assert "temperature-sampled" in s


# --------------------------------------------------------------------------- #
# exclusions are an input to sampling, not a filter on its output
# --------------------------------------------------------------------------- #
def _policy(checksums, triggers=None):
    return {
        "list_id": "TEST-POLICY", "status": "approved",
        "decision_type": "policy_deferral", "human_review_performed": False,
        "scope": {"promotion_permitted": False},
        "exclusions": [{"audio_checksum_sha256": c, "defect": False,
                        "action": "defer_pending_review",
                        "trigger": (triggers or {}).get(c, "over_decoder_limit")}
                       for c in checksums],
    }


def test_excluded_rows_never_reach_the_sampling_weights():
    """The pool must shrink before counts are taken. If a row is removed after
    sampling it has already shifted its language's weight."""
    rows = {V2: [_row(c * 64, use=("asr_train",)) for c in "abcdef"]}
    comp = _completion(rows)
    mix, prov = _load_v(rows, comp,
                        adoption=_adoption(comp, policy="p" * 64),
                        version="v2", exclusions={"a" * 64: {"trigger": "t"}},
                        exclusions_sha256="p" * 64)
    assert prov["eligible_rows_before_exclusions"] == 6
    assert prov["eligible_rows"] == 5
    assert prov["exclusions"]["removed_from_eligible_pool"] == 1
    assert prov["exclusions"]["applied"] == "before temperature sampling"
    assert all(r["audio_checksum_sha256"] != "a" * 64 for r in mix)


def test_language_scope_requires_only_applicable_policy_rows():
    """Deferred-language policy rows stay adopted but need not appear in a
    mix whose manifest scope deliberately excludes that language."""
    import pipeline.train_asr as T

    acholi = "curated/acholi/asr/ach_asr/v2/manifest.jsonl"
    amharic = "curated/amharic/asr/amh_asr/v2/manifest.jsonl"
    rows = {
        acholi: [_row("a" * 64), _row("b" * 64)],
        amharic: [_row("c" * 64)],
    }
    comp = _completion(rows)
    cli = _cli_with_completion(
        rows, comp, adoption=_adoption(comp, policy="p" * 64))
    exclusions = {
        "a" * 64: {"language": "acholi", "trigger": "t"},
        "c" * 64: {"language": "amharic", "trigger": "t"},
    }
    with patch.object(T, "list_manifests", lambda c: list(rows)):
        mix, prov = T.load_mix(
            cli, 0.5, 0, ["acholi"], version="v2",
            exclusions=exclusions, exclusions_sha256="p" * 64)
    assert [row["audio_checksum_sha256"] for row in mix] == ["b" * 64]
    assert prov["exclusions"] == {
        "list_id": None,
        "policy_sha256": "p" * 64,
        "policy_declared": 2,
        "declared": 1,
        "out_of_scope_declared": 1,
        "removed_from_eligible_pool": 1,
        "by_trigger": {"t": 1},
        "applied": "before temperature sampling",
    }


def test_deferred_row_absent_from_the_pool_is_refused():
    """A policy naming a row this corpus does not contain describes something
    else; silently removing nothing would still report success."""
    rows = {V2: [_row("a" * 64, use=("asr_train",))]}
    comp = _completion(rows)
    with pytest.raises(SystemExit) as e:
        _load_v(rows, comp, adoption=_adoption(comp, policy="p" * 64),
                version="v2", exclusions={"z" * 64: {}}, exclusions_sha256="p" * 64)
    assert "not in the eligible pool" in str(e.value)


def test_adoption_granted_for_a_different_policy_is_refused():
    rows = {V2: [_row("a" * 64, use=("asr_train",))]}
    comp = _completion(rows)
    with pytest.raises(SystemExit) as e:
        _load_v(rows, comp, adoption=_adoption(comp, policy="p" * 64),
                version="v2", exclusions={"a" * 64: {}}, exclusions_sha256="q" * 64)
    assert "does not transfer between policies" in str(e.value)


def test_exclusions_without_an_adopted_policy_are_refused():
    rows = {V2: [_row("a" * 64, use=("asr_train",))]}
    comp = _completion(rows)
    with pytest.raises(SystemExit) as e:
        _load_v(rows, comp, adoption=_adoption(comp), version="v2",
                exclusions={"a" * 64: {}}, exclusions_sha256="q" * 64)
    assert "did not contemplate removing rows" in str(e.value)
