# A3-RETENTION-AND-DELETION-2026-001 — zones, retention, deletion paths

Status: ACTIVE record (parallel task D). Source of truth: Base v5 §A3
table, pinned here with the live-audited state of 2026-08-17 and the
mechanical conditions that close each gap. PII posture: raw/ and
user-audio/ are the only zones that can ever hold personal audio;
everything else derives under licence and consent controls.

## 1. The A3 retention table, as it MUST hold

| Prefix | Contents | Retention / control |
|---|---|---|
| raw/{lang}/{source}/ | immutable as-acquired data + licence record | keep while licence valid; deletion CASCADES from the consent registry |
| curated/{lang}/vN/ | cleaned, manifest.jsonl | immutable per version; rebuilt, never edited |
| eval/{lang}/vN/ | frozen eval sets | write-DENIED to training roles by IAM policy, not by discipline |
| approved/{track}/vN/ | signed model artifacts + SHA-256 manifest | immutable; registry points here |
| audio-cache/tts/ | generated speech, content-hash keys | lifecycle expiry 90 days |
| user-audio/ | production request audio | 30-day expiry unless consent_for_training; then COPIED to raw/ with consent id and the original STILL expires |

## 2. Live audit 2026-08-17 (bucket medzen-speech, SSE-KMS, versioned)

| Control | State | Verdict |
|---|---|---|
| curated immutability | lifecycle `expire-old-curated-versions` (noncurrent 90d); versions rebuilt (v1,v2,gb1,gb2), never edited; COMPLETE/ADOPTION raw-byte binding refuses post-adoption edits | CONFORMS |
| candidates expiry | lifecycle `expire-ungated-candidates` 60d | CONFORMS (stricter than A3 requires) |
| audio-cache/tts 90d | prefix does not exist yet (B6 TTS serving not in production) | NOT YET DUE — rule JSON pre-authored in §3, MUST ship with the first production TTS deploy |
| user-audio 30d + consent copy | prefix does not exist yet | NOT YET DUE — same condition; the consent-copy path lands with B6 production intake |
| eval write-denial to training roles | **VIOLATION**: `medzen-trainer-role` inline policy grants `s3:PutObject`+`s3:DeleteObject` on `eval/*` (B4-era grant, no current legitimate producer) | GAP — remediation in §3, scheduled immediately after the running T5 job completes (no IAM edits under a live job on that role) |
| raw deletion cascade from consent registry | no consent-registry-driven deletion automation exists; raw deletions would be manual | GAP — mechanical design in §3; acceptable while raw/ holds only owner-supplied and public-licence corpora (gb1 audit: zero research-only/NC rows), becomes BLOCKING before any user-consented audio enters raw/ |

## 3. Remediations, exact

1. **Trainer eval-write revocation** (after T5 terminal): remove the
   `s3:PutObject`/`s3:DeleteObject` statement for `eval/*` from
   `medzen-trainer-access`; keep `GetObject`. Backport to infra/iam.tf
   with the T5 SageMaker delta already chip-tasked.
2. **audio-cache/tts lifecycle** (ships with first TTS deploy):
   `{"ID":"expire-tts-cache","Filter":{"Prefix":"audio-cache/tts/"},"Status":"Enabled","Expiration":{"Days":90}}`
3. **user-audio lifecycle** (ships with B6 production intake):
   `{"ID":"expire-user-audio","Filter":{"Prefix":"user-audio/"},"Status":"Enabled","Expiration":{"Days":30}}`
   plus the consent-copy contract: copy to `raw/{lang}/user-consented/`
   carrying `consent_id`; the original under user-audio/ is never
   exempted from expiry.
4. **Consent-cascade deletion**: a consent-registry record withdrawal
   emits the exact raw/ keys acquired under that consent id (the
   manifest rows carry `consent_id` since gb1); deletion runs as a
   reviewed packet enumerating those keys, then curated versions
   containing the rows are REBUILT (never edited) without them. The
   licence_filter/load_mix machinery already refuses rows whose
   manifests changed post-adoption, so a stale adoption cannot silently
   keep training on withdrawn audio.

## 4. Standing invariants this record binds

- No new top-level S3 prefix without a row in the §1 table and a
  retention rule decided BEFORE first write.
- Training roles never gain write on eval/ or approved/ again; any IAM
  change touching those prefixes cites this record.
- research/b5-training/** (SageMaker jobs) carries job-scoped rw for
  the trainer role only; artifacts there are working state, promotable
  only via the signed export path into approved/.
