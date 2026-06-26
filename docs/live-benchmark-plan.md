# Live benchmark plan (FLAGGED FOR CONRAD -- NOT EXECUTED)

> Spend gate. Everything else in this repo (research, scaffold, the dry fit analysis, the CPU test
> suite) needed ZERO GPU spend and is done. This is the one step that needs real silicon to finalize:
> confirming the resolution / frame / step ceilings that actually fit 16GB and the real per-clip
> wall-clock. **Per the brief, this is NOT executed. It is costed and flagged here for Conrad to
> approve before any paid GPU is spun up.**

## Why a live run is needed at all

The model-selection (LTX-Video) and the tier ladder are settled on paper from model cards + community
reports. What desk research CANNOT give us:

1. The TRUE peak VRAM of each tier's config on a 16GB card (the `vram.py` coefficients are coarse
   first-order estimates, deliberately conservative). A benchmark replaces them with measured peaks.
2. The real ceilings: the largest resolution x frame-count that fits 16GB at each tier without OOM,
   and whether `final` (13B-fp8-distilled + sequential offload) is usable or too slow to be worth it.
3. Real per-clip wall-clock on the card -- the number that decides whether the local door is pleasant
   or painful, and whether `draft` is genuinely sub-minute.
4. The exact diffusers pipeline kwargs for the deployed version (the `i2v_ltx.animate` body is a
   scaffold; the LTX revision + conditioning argument names are pinned against the real package here).

## The card

The 4060 Ti 16GB is a CONSUMER card; RunPod does not offer it. Two honest ways to validate:

- **Option A (PREFERRED, zero cloud spend): Conrad's own 4060 Ti 16GB**, if he has one. This is the
  truest test -- the exact target silicon -- and costs nothing but the box's power. Run the harness
  below locally.
- **Option B (cloud proxy): RunPod RTX 2000 Ada 16GB.** The closest cloud match -- same architecture
  (Ada), same 16GB VRAM, same fp8 capability -- so VRAM fit + fp8 behavior transfer faithfully; only
  raw clocks differ slightly from the 4060 Ti. (RTX 4000 Ada 20GB is a looser proxy: more VRAM, so it
  would NOT prove the 16GB fit -- avoid it for the fit test.)

RunPod 16GB-class Ada cards confirmed available (read-only `list-gpu-types`, no spend):

| Card | VRAM | Match to 4060 Ti 16GB |
|---|---|---|
| RTX 2000 Ada | 16 GB | **best proxy** (Ada + 16GB + fp8) |
| RTX 4000 Ada | 20 GB | loose (more VRAM; does not prove the 16GB fit) |

## The matrix (small + bounded)

For each of the three tiers (`draft` 2B, `standard` 2B, `final` 13B-fp8), one representative shot
(a real keyframe + a motion prompt), measuring: peak VRAM (`torch.cuda.max_memory_allocated`),
wall-clock, OOM/no-OOM, and a visual quality eyeball. Then, for the tier that fits with headroom, push
resolution/frames one notch up to find the real ceiling.

- ~9 generations baseline (3 tiers x ~3 res/frame points) + a model-download warmup per model.
- The harness is already here: `POST /run {"selftest": true}` proves transport, then real `i2v_clip`
  jobs through the same `JobRegistry` + `i2v_ltx.animate` path the production server uses.

## Cost estimate (Option B, RunPod RTX 2000 Ada 16GB)

| Item | Estimate |
|---|---|
| GPU rate (RTX 2000 Ada, community/secure) | ~$0.20-0.40 / hr (CONFIRM at the RunPod console at booking) |
| Model downloads (LTX 2B + 13B-fp8) + warmup | ~0.5-1.0 hr |
| The generation matrix (~9 clips, LTX is fast) | ~0.5-1.5 hr |
| Headroom / debugging the pinned kwargs | ~1 hr |
| **Total GPU time** | **~2-4 GPU-hours** |
| **Total cost** | **~$1-5** (Option A: $0, just power) |

The dollar cost is small; the gate is about DISCIPLINE, not amount -- no paid GPU spins up without
Conrad's go. (Option A on Conrad's own card is free and is the better test if the card is on hand.)

## Definition of done for the benchmark

- Measured peak VRAM per tier -> replace the `vram.py` coefficients with real numbers; confirm all
  three tiers fit 16GB (or down-shift a tier's ceiling honestly if one does not).
- Confirmed resolution/frame ceilings per tier -> fix the `_TIERS` table in `config.py`.
- Real per-clip wall-clock per tier -> document in the README so users know what to expect.
- The `i2v_ltx.animate` body's pipeline kwargs pinned against the validated diffusers version, and one
  end-to-end clip rendered through the live server + a real R2 round-trip.
- Tag the validated version (mirroring vivijure-backend's "GPU-validated features land tagged").

## Until then

The repo ships honestly as a scaffold: pure logic (config / vram / frame math / jobs / server routing)
is CPU-tested and green; the GPU body raises a clear error rather than faking output if torch/diffusers
is absent (a producer stage never fakes a clip). Nothing claims to be card-validated that is not.
