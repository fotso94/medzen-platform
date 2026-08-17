# B4-OMNIASR-ADAPTATION-DESIGN-2026-001 — Fine-tuning and serving omniASR

Status: DRAFT for owner review — desk analysis, no training executed.
Every architecture figure below was measured by instantiating the real
model configurations on the meta device inside the pinned runtime image
(medzen-asr-eval-runtime:pilot-0822ead), not read from marketing pages.

## 1. What the models actually are (measured)

**omniASR_LLM_1B_v2 — 2.28B parameters total** (the "1B" names the
decoder class, not the model):

| Component | Params | Structure |
|---|---|---|
| encoder_frontend | 18.0M | Wav2Vec2 conv feature extractor (7 conv layers) |
| encoder | 944.5M | 48 Transformer encoder layers |
| encoder_proj | 5.2M | bridge into the decoder |
| text_frontend | 42.1M | token embedding |
| llama_decoder | 1,220.6M | 12 LLaMA layers, model_dim 4096, RoPE, GLU FFN, RMSNorm |
| final_proj | 42.1M | vocabulary projection |
| lang_embeddings | 6.9M | language conditioning |

**omniASR_CTC_1B_v2** — the same 944.5M encoder family with a CTC head
(~963M total): no autoregressive decode, which is why we measured 25–41ms
medians vs the LLM's 0.72–1.2s.

## 2. LoRA design

fairseq2 0.6 ships **no LoRA module** (verified: no `fairseq2.nn.lora`,
no lora module anywhere in the package). HF PEFT does not know
fairseq2's own `Linear` class. **Work item T1: a ~100-line LoRA wrapper**
(A/B low-rank matrices around fairseq2 Linear, merge-at-export), unit
tested against frozen-output equivalence at init (B=0).

Exact target modules (verified names from the module tree):

- **LLM variant** (mirrors the proven Whisper r=32 q/v recipe):
  `llama_decoder.layers.{0..11}.self_attn.q_proj` and `.v_proj`
  → **6.29M trainable params** (0.28% of the model; verified against
  real module dims — square 4096 projections, no GQA). Optional second
  phase: encoder `layers.{0..47}.self_attn.{q_proj,v_proj}` (dim 1280,
  +7.86M) for acoustically distant languages.
- **CTC variant**: encoder `layers.{0..47}.self_attn.{q_proj,v_proj}`
  + full fine-tune of the small CTC head.

## 3. Serving (the CTranslate2 chain is dead — replacement is live-proven)

- **CTranslate2 does not and will not support fairseq2/omniASR** — the
  B4.3 "merge → CT2 int8" step is Whisper-only. The conversion step is
  REMOVED for omniASR, not replaced speculatively.
- **The replacement already ran in production-identical conditions for
  ~10 hours across attempts 28–36**: native fairseq2/PyTorch bf16 via
  `ASRInferencePipeline` on L4 (g6.xlarge) — CTC 25–41ms median, LLM
  0.72–1.2s median, 5.1GB VRAM observed with three models co-resident.
  An L4 serves the CTC workhorse with enormous headroom.
- LoRA deployment: **merge adapters into the checkpoint at export**
  (work item T2) so serving stays identical to what the suite proved;
  per-language adapter hot-swap is a later optimization, not v1.
- Future optimizations (explicitly deferred): torch.compile, int8
  quantization (torchao), ONNX for the CTC encoder.

## 4. Training data actually available (gb1, measured) and the licence wall

gb1 holds 500,181 rows / 1,331.6 hours across **16 languages** (the
other 31 pool languages have evaluation data only — round-1 fine-tuning
scope is these 16). After the isolation audit (GB1-EVAL-ISOLATION-AUDIT-
2026-001: frozen pool fully isolated) the remaining constraint is the
**ShareAlike-on-weights legal review** (Base v5 B9 requires it):

| Language | Clear hours | ShareAlike-gated hours |
|---|---|---|
| kinyarwanda | 300.0 | 0 |
| swahili | 89.0 | 0 |
| ewe | 79.4 | 0 |
| akan | 60.0 | 0 |
| fula | 49.0 | 98.1 |
| serer | 32.0 | 0 |
| pulaar | 24.6 | 0 |
| french | 15.0 | 0 |
| wolof | 10.1 | 0 |
| lingala | 5.0 | 68.0 |
| yemba | 2.3 | 0 |
| **amharic** | **0** | 189.5 |
| **oromo** | **0** | 189.5 |
| **shona** | **0** | 75.7 |
| **luganda** | **0** | 24.8 |
| **acholi** | **0** | 19.8 |

**Five languages have zero legally-clear training hours.** Until the
ShareAlike review concludes, their fine-tuning is blocked on licensing,
not engineering. Work item T3: a hard licence-policy exclusion filter in
the trainer (default excludes `sharealike_review`), so a green legal
review widens the filter instead of anyone editing data.

## 5. Cost projection (g6.xlarge, on-demand $0.805/h, spot ~35% of that)

Assumptions (stated, falsifiable by the first calibration run): LoRA
bf16 + gradient checkpointing processes ~2.5 audio-hours per GPU-hour on
L4 for the 2.28B LLM variant (~5 for the 963M CTC), 2 effective epochs,
per-language audio capped at 100h (kinyarwanda's 300h subsampled).

| Scenario | GPU-hours | On-demand | Spot |
|---|---|---|---|
| CTC, 11 clear languages (~466h audio capped) | ~186 | ~$150 | ~$55 |
| LLM, 11 clear languages | ~373 | ~$300 | ~$105 |
| Both variants, clear languages | ~559 | ~$450 | ~$160 |
| Add 5 SA-blocked languages after legal review (+499h capped 320h) | +~384 | +~$309 | +~$108 |

First calibration run (work item T5) measures the true audio-h/GPU-h on
one small language (yemba 2.3h or wolof 10.1h, ~$2) and re-prices this
table before any full campaign packet.

## 6. Work items for the B5 training packet

- T1: fairseq2 LoRA wrapper + equivalence tests (local).
- T2: adapter-merge export + SHA-256-manifested artifact per §B5 signing.
- T3: licence-policy exclusion filter (default-deny `sharealike_review`).
- T4: trainer image on the eval image's base (same fairseq2 pins, plus
  optimizer state; SageMaker-compatible per B10).
- T5: $2 calibration run, then re-price §5.
- T6: gate wiring — candidate vs the suite merge report's per-language
  baseline on the frozen pool (the only uncontaminated gate surface).

Dependencies: T1–T4 are local and can start now; T5 needs a $10-class
packet; the full campaign needs the decision record published and the
ShareAlike review for the five blocked languages.

## 5b. Re-priced from measurement (T5, 2026-08-17)

T5 measured **7.48 audio-h/GPU-h** for the CTC variant on ml.g6.xlarge
(whole-job basis including staging overhead; pure loop ~9.6) against the
assumed 5.0 — training is ~50% faster than priced. §5 re-priced at the
measured rate and SageMaker rates (~$1.21/h on-demand; spot pending the
filed quota increase, ~35%):

| Scenario | GPU-hours (was) | GPU-hours (measured) | On-demand | Spot |
|---|---|---|---|---|
| CTC, 11 clear languages (932 audio-h, 2 epochs) | ~186 | ~125 | ~$151 | ~$53 |
| LLM, 11 clear languages (assume 2x CTC cost pending its own calibration) | ~373 | ~249 | ~$301 | ~$105 |
| Both variants | ~559 | ~374 | ~$452 | ~$158 |
| +5 SA-blocked languages after legal review | +~384 | +~257 | +~$311 | +~$109 |

Evidence: B5-T5-CALIBRATION-RESULT-2026-001.json (job r6: 600 steps,
1,298 billable seconds, PASS_MERGED_EXPORT, model 9682b679...). The LLM
variant remains refused in the trainer until its own T5-class
calibration; its rows above carry the old 2x assumption explicitly.

## 5c. OWNER-APPROVED campaign basis (2026-08-17)

The owner approved the §5b measured table with two standing rules that
now govern ALL MedZen cost planning: (1) on-demand pricing is always
the basis; (2) +$100 buffer on every reported maximum. Approved
budgeted ceilings:

| Scenario | On-demand estimate | BUDGETED CEILING |
|---|---|---|
| CTC, 11 clear languages | ~$151 | **$251** |
| Both variants, clear languages | ~$452 | **$552** |
| +5 SA-blocked languages after legal review | +~$311 | **+$411** |

CTC-first sequencing per the owner's earlier decision; the LLM variant
stays refused in the trainer until its own T5-class calibration.
