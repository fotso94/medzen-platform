# B5 Data-Sourcing Scoping — the 34 eval-only languages

**Record:** B5-DATA-SOURCING-SCOPING-2026-001 · **Status:** proposed (owner
decides which tiers to fund) · **Date:** 2026-08-18

Every language below has a frozen evaluation set and a measured zero-shot
baseline (full-coverage merge report, 47 languages) but fewer than 1.0
trainable hours in the curated zone — the B5 floor below which a fine-tune
memorizes instead of adapting. This document scopes WHERE training audio
could come from, what it costs to find out, and what only the owner can
unblock. Sources marked *verified* are pinned in this repository; sources
marked *verify* must be confirmed against the live source card before any
ingest binds.

## Tier A — cleared source exists; ingest engineering only

| Language | Baseline WER | Source (licence) | Expected scale | State |
|---|---|---|---|---|
| hausa | 24% | Common Voice 17 `ha` (cc0, *verified* — adapter wired) + FLEURS train (CC-BY, *verify*) | CV small + ~7–10 h FLEURS | ingest-ready |
| igbo | 44% | CV17 `ig` (cc0, *verified* — wired) + FLEURS train (*verify*) | CV small + ~7–10 h | ingest-ready |
| yoruba | 51% | CV17 `yo` (cc0, *verified* — wired) + FLEURS train (*verify*) | CV small + ~7–10 h | ingest-ready |
| malagasy | 70% | WaxalNLP digital_umuganda ASR (sharealike_review — **cleared by LIC-2026-002**, *verified in the source matrix*) | unknown until fetch (card lists malagasy among the 19 ASR languages) | adapter exists (waxalnlp); config addition |
| english | 12% | CV17 `en` (cc0, *verified mirror*; locale addition + cap like kinyarwanda) + FLEURS | capped (e.g. 100 h) | one-line locale add |
| sepedi | 77% | NCHLT Sepedi via SADiLaR (**verify licence text** — believed CC-BY-class; ~50 h read speech) | ~50 h if licence verifies | new adapter |

Engineering: one new FLEURS adapter, one NCHLT adapter, two config
additions. Ingest compute/S3 for the whole tier is small (the kinyarwanda
CV ingest pattern; single-digit dollars per language, reported max +$100
per the standing rule). No owner action needed beyond approving the tier —
except sepedi, which binds only if the NCHLT licence text verifies as
commercial-ok.

## Tier B — source exists; owner-level unblock required

| Language | Baseline WER | Source | Blocker | Owner action |
|---|---|---|---|---|
| pidgin | 54% | africanvoices.io (~1,900 h incl. Nigerian Pidgin) | `pending_written_terms` — no written licence exists (*verified in the source matrix*) | request written commercial terms |
| hausa/igbo/yoruba at scale | — | NaijaVoices (~1,800 h across the three) | CC-BY-NC-SA — `research_only_nc`, never enters a commercial mix (*verified*) | commercial licence negotiation, or accept Tier A scale only |

Nothing in Tier B can be engineered around: the platform's licence gate
refuses these sources by design until the paperwork changes.

## Tier C — no public training source; own collection required (27 languages)

bafia, baka, bakoko, bamun, basaa, duala, ejagham, eton, ewondo, fefe,
fulfulde, gbaya, ghomala, isu, kera, kom, kwasio, lamso, maka, medumba,
mundang, ngiemboon, ngombala, nomaande, nugunu, yambeta, yangben
(fulfulde carries the transfer caveat below). Baselines run 75–128% WER:
the base
model is effectively unusable for all of them, and no public corpus
exists (these eval sets themselves had to be commissioned).

- **Channel:** the SOREVA relationship that produced the eval sets is the
  proven collection channel. Next action is a quote request for
  training-scale collection (indicatively 20–50 h per language,
  read + spontaneous, consent-documented, `own_consented` licence class)
  — no pricing is assumed in this record; the quote decides.
- **Prioritization inside the tier (proposed):** by Cameroon clinical
  reach first — ewondo, duala, basaa, bamun, ghomala, medumba, fefe —
  then by eval-set quality and baseline.
- **fulfulde caveat:** closely related to fula (147 h now training).
  Before commissioning fulfulde collection, evaluate the wave-1/2 model
  on the frozen fulfulde eval set — cross-lingual transfer may cover it
  cheaply, and the same measurement is free for every Tier C language.

## Cross-cutting recommendation (free, high information)

When campaign r2 (and later wave 2) completes, run the T6-style
evaluation over ALL 47 eval sets, not just the trained 9/13. The transfer
table that falls out ranks the 34 by how much data each actually needs —
before any Tier C money is spent. Driver v2's `--units` selection exists
for exactly this run.

## Decision asked of the owner

1. Approve Tier A ingest (engineering starts; sepedi conditional on
   licence verification).
2. Say which Tier B unblocks to pursue (africanvoices terms; NaijaVoices
   negotiation) — both are owner-to-counterparty actions.
3. Approve the SOREVA quote request for Tier C (no spend commitment; the
   quote comes back as its own decision).

## Addendum 2026-08-20 — code-switch corpora (ARCH-2026-001 requirement)

The one-multilingual-model architecture must eventually PROVE code-switch
handling; no licensed code-switch set exists today (VERIFIED_GAP,
B4-REPLAY-CODESWITCH-GAP-2026-001). Sourcing targets, in priority order
for real MedZen pairs:

1. kinyarwanda–english and kinyarwanda–french
2. pidgin–english and pidgin–french

Required properties (promotion-evidence grade): licensed for evaluation;
speaker- AND session-disjoint from all training corpora; NATURAL
conversational switches (not concatenated monolingual clips);
switch-boundary annotations; coverage of medical terms, numbers, dosages,
negation, and emergency phrases. Synthetic/TTS-generated audio may
support TRAINING experiments but is never promotion evidence
(PROMOTION-PROTOCOL-2026-001).

Likely sources: commissioned recording (the owner's africanvoices/
NaijaVoices relationships are the strongest channel — both teams record
natively); academic code-switch corpora are rare for these pairs.
This is data acquisition with licensing — owner-led, engineering
supports with specs + freeze audits.
