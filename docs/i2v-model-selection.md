# Image-to-video model selection for the 12GB door

> Deliverable: the DRY fit analysis. Which i2v model the local-consumer backend runs on a single
> 12GB consumer GPU, and why. Desk research from model cards / official repos / diffusers docs /
> reputable community reports -- NO rented hardware. Numbers marked **[community]** are community
> reports, not vendor-official. Sources at the bottom.

## The constraint

Conrad set the original design target at the **RTX 4060 Ti 16GB**. Verbatim: *"4060Ti, that's the
lowest we can really go if you expect a quality render."* The engine was scoped to fit that comfortably;
the later 12GB VRAM-budget proof (`docs/proof/RESULTS.md`) then showed all three tiers hold under an
11GB cap, so the PROVEN floor is a **12GB consumer GPU** (e.g. RTX 3060 12GB, RTX 4070, RTX 4070 Ti).
Model choice, offload, and the resolution/frame ceilings are scoped to fit AND produce acceptable
quality there; 16GB+ cards just get comfortable headroom.

The datacenter backend's i2v is **Wan 2.2 A14B**, a two-expert (14B + 14B) MoE that runs on H200 /
B200. It does NOT fit a consumer card. So the local door needs a different engine. This is a model-SELECTION
decision, evaluated on three axes against the floor: **fit** (runs without OOM), **speed**, **quality**
-- plus **license** (the project is AGPL-3.0, given freely, so a clean self-host/commercial license
matters).

## The comparison (single consumer card)

| Axis | **LTX-Video** (2B / 13B distilled) | CogVideoX-5B-I2V | SVD / SVD-XT | AnimateDiff (+SparseCtrl / Lightning) |
|---|---|---|---|---|
| **Consumer-card fit** | **Excellent** -- lightest real i2v; 2B distilled runs even on 8GB [community] | Tight but works (fp8 + **sequential** CPU offload + VAE tiling; ~16GB [community]) | Good (model offload + `decode_chunk_size` + VAE tiling; <10GB [community]) | Excellent -- most headroom (SD1.5 16f ~8GB [community]) |
| **Speed** | **Fastest** -- few-step distilled (4-10 steps), sub-minute class [community] | **Slowest** -- ~14-15 min/clip on a 12-16GB card [community] (sequential offload paging) | Moderate -- a few min/clip with offload [community] | Very fast (Lightning 1-8 step) |
| **True i2v quality** | Good, fast-improving; 2B < 13B fidelity | **Best** -- strong first-frame identity + coherent motion + text prompt | Good motion, **no text control**, weak faces/large-motion drift | Weakest *true* i2v (SparseCtrl is approximate); best for *stylized* motion |
| **License (free self-host)** | **Cleanest** -- LTX Open Weights License, free commercial **< $10M revenue**, no gating/metering | Friction: 5B is custom (register + **1M visits/mo cap**); **2B is Apache-2.0** | OK -- Stability Community License, free **< $1M**, custom | Code Apache-2.0 (clean), but output bound by the base SD/SDXL checkpoint's license |
| **diffusers maturity** | First-class `LTXImageToVideoPipeline` (+ native fp8) | First-class `CogVideoXImageToVideoPipeline` | First-class `StableVideoDiffusionPipeline` | Mature, but i2v only via SparseCtrl/IPAdapter (no single-image i2v pipeline) |
| **Res / length ceiling** | ~512-768p, up to 257 frames (8k+1), 24-30 fps | Fixed 720x480, 49 frames, 8 fps (~6s) | 576x1024, 14 or 25 frames (~4s) | SD1.5 512x512 16f (~2s, extendable); SDXL 1024 beta |

## Recommendation: LTX-Video

**LTX-Video (2B-distilled as the default; 13B-fp8-distilled as the honest ceiling).** It is the only
candidate that wins **fit + speed + license simultaneously** on a 12GB card:

- **Fit.** The lightest real i2v model here. The 2B-distilled runs comfortably on a 12GB card (community
  reports it even on 8GB), leaving headroom for keyframe/finish to share the card. The 13B-fp8-distilled
  still fits Ada 16GB (fp8 is an Ada feature, and the 4060 Ti is Ada) with sequential offload + VAE
  tiling -- our `final` tier.
- **Speed.** Designed as a few-step (4-10) distilled DiT, the fastest by a wide margin. On a homelab
  card where the user waits on their own silicon, iteration speed is the difference between usable and
  abandoned. CogVideoX's ~15 min/clip [community] is a non-starter for an interactive local studio.
- **License.** The LTX Open Weights License is the cleanest fit for a freely-given AGPL project: free
  for commercial use under $10M revenue, no gating, no usage metering. CogVideoX-5B's register +
  1M-visits/month cap is an awkward fit for "given freely."
- **Ecosystem.** First-class `LTXImageToVideoPipeline` in diffusers with native fp8, so the engine code
  is a thin, maintainable wrapper (see `src/vivijure_local/i2v_ltx.py`).

**The honest trade-off:** LTX 2B is visibly lower fidelity than 13B-class models, and at the fastest
distilled settings it can trade some prompt adherence for speed. The `final` tier (13B-fp8-distilled)
buys back fidelity at the cost of runtime. This is exactly why the tiers are mapped per-backend and
labeled honestly (below) -- a 12GB card's `final` is its honest ceiling, not datacenter parity.

**The runner-up, explicitly:** if a user prioritizes i2v fidelity over speed and can tolerate ~15
min/clip, **CogVideoX-5B-I2V** is the quality leader (best first-frame identity + motion + text
control). It is a natural FUTURE second local motion module (a `cogvideo-local` module alongside
`local-gpu`), not the default. The Apache-2.0 **CogVideoX-2B** is the license-clean way to offer
CogVideoX without the 5B registration friction, at lower quality. SVD-XT is a fine simple short-clip
workhorse (no text control). AnimateDiff is the pick only for stylized/animated motion, not photoreal
"animate this exact photo."

## The honest tier mapping (12GB)

The control plane owns the tier vocabulary (`draft` / `standard` / `final`) and injects the chosen
tier into every motion.backend module; an enum value not in the module's schema is silently dropped
(vivijure #124). So the `local-gpu` module keeps the same three names, and THIS backend maps each to an
LTX config a 12GB card can actually deliver. `final` here is the card's honest ceiling, NOT Wan-on-B200
parity. (Mapping lives in `src/vivijure_local/config.py`; VALIDATED on a 16GB Ada -- peak ~10.4GB, no
OOM -- see `docs/proof/RESULTS.md`.)

| Tier | Model | Steps | Resolution | Max frames | Offload | Intent |
|---|---|---|---|---|---|---|
| `draft` | LTX-Video (base) | 25 | 512x320 | 97 | model CPU offload + VAE tiling | fast preview (38.6s) |
| `standard` | LTX-Video (base) | 40 | 704x512 | 121 (~5s @ 24fps) | model CPU offload + VAE tiling | the comfortable middle (125.6s) |
| `final` | LTX-Video (base) | 50 | 768x512 | 121 | model CPU offload + VAE tiling | the card's honest ceiling |

The pure VRAM budgeter (`src/vivijure_local/vram.py`) estimates each tier's peak against the 12GB floor
and picks the weakest offload that fits, conservatively (it would rather page more than OOM the user's
only GPU). All three tiers are estimated to fit the floor; the live benchmark replaces the coarse
coefficients with measured peaks.

## What still needs real silicon

The exact resolution / frame / step ceilings that fit a 12GB card -- and the real per-clip wall-clock -- can
only be confirmed on the card. That is the one step behind the spend gate; it is NOT executed here.
The costed plan is in [`live-benchmark-plan.md`](./live-benchmark-plan.md).

## Sources

- LTX-Video card / repo / diffusers: https://huggingface.co/Lightricks/LTX-Video ,
  https://github.com/Lightricks/LTX-Video ,
  https://huggingface.co/docs/diffusers/main/en/api/pipelines/ltx_video ,
  https://huggingface.co/Lightricks/LTX-Video-0.9.8-13B-distilled
- LTX license ($10M threshold): https://ltx.io/model/license
- CogVideoX-5B-I2V card (memory table, specs) + LICENSE: https://huggingface.co/zai-org/CogVideoX-5b-I2V ,
  https://huggingface.co/zai-org/CogVideoX-5b-I2V/blob/main/LICENSE ; CogVideoX-2B (Apache-2.0):
  https://huggingface.co/zai-org/CogVideoX-2b ; diffusers offload/quantization:
  https://huggingface.co/docs/diffusers/main/en/api/pipelines/cogvideox
- CogVideoX consumer-GPU timing [community]: https://huggingface.co/zai-org/CogVideoX-5b/discussions/7
- SVD-XT card (license, specs, limits) + diffusers offload: https://huggingface.co/stabilityai/stable-video-diffusion-img2vid-xt ,
  https://huggingface.co/docs/diffusers/en/using-diffusers/svd ; Stability Community License ($1M):
  https://stability.ai/license
- AnimateDiff repo/license + diffusers pipelines: https://github.com/guoyww/AnimateDiff ,
  https://github.com/huggingface/diffusers/blob/main/docs/source/en/api/pipelines/animatediff.md

## Measured update (#1, 2026-07): the shipped 12GB tier mapping

The desk-research table above was written before the 13B path was benchmarked. Two facts changed on
real silicon (full proof: [`proof/BENCH-13B.md`](./proof/BENCH-13B.md), measured under a hard 12GB
allocator cap):

1. **`Lightricks/LTX-Video-0.9.7-distilled` is a 13B-class model**, not a light 2B (transformer config:
   48 layers, inner dim 4096; the base `LTX-Video` is the 2B at 28 layers / 2048). So it cannot serve as
   a fast `draft`: at model-cpu-offload a 13B OOMs a 12GB card, and via sequential offload it is slower
   than the base draft.
2. **The 13B-distilled `final` tier FITS 12GB comfortably** via sequential (per-layer) CPU offload + VAE
   tiling: peak reserved 4.63 GB, 108.4s per 768x512 5s clip -- faster than `standard` (fewer distilled
   steps) and higher quality.

Shipped mapping (`src/vivijure_local/config.py`):

| Tier | Model | Steps | Resolution | Offload | Engine | Intent |
|---|---|---|---|---|---|---|
| `draft` | LTX-Video (base 2B) | 25 | 512x320 | model CPU offload + VAE tiling | LTXImageToVideoPipeline | fast preview (~49s) |
| `standard` | LTX-Video (base 2B) | 40 | 704x512 | model CPU offload + VAE tiling | LTXImageToVideoPipeline | the comfortable middle (~132-142s) |
| `final` | LTX-Video-0.9.8-13B-distilled | 10 | 768x512 | sequential CPU offload + VAE tiling | LTXConditionPipeline | 13B quality ceiling, PROVEN 12GB (108s, 4.63GB reserved) |

A genuinely-faster distilled `draft` would need a real 2B distilled model (e.g.
`Lightricks/LTX-Video-0.9.6-distilled`); that is a parked follow-up, not wired, so the model choice stays
a deliberate decision rather than a silent swap.
