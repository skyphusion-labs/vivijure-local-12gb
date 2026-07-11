# Proof gate: the 13B quality tier under a HARD 12GB allocator cap (#1)

Live LTX-Video i2v benchmark of the new tier->engine dispatch on real silicon, under an explicit,
hard **12GB VRAM allocator cap**. Outcome: the **13B-distilled `final` tier FITS a 12GB budget** with
large headroom via sequential offload + VAE tiling, and is even faster per clip than `standard`
(fewer distilled steps). `draft` + `standard` stay the PROVEN base 2B i2v. Raw artifacts:
`bench-13b/benchmark-propagandhi.json` + `.md`, sample clips `bench-13b/sample_*.mp4`.

## The cap (how a 20GB card models a 12GB one)

The benchmark ran on **propagandhi** (RTX 4000 SFF Ada Generation, 19.55 GB usable), the free GPU
verify lane, inside the repo's own runtime image (built from `deploy/Dockerfile`: torch 2.5.1+cu124,
diffusers 0.38.0, transformers 4.57.6). `scripts/benchmark.py --cap-gb 12` pins
`torch.cuda.set_per_process_memory_fraction(12 / 19.55) = 0.6138` on the device BEFORE any model loads,
so this process's caching allocator may claim at most 12GB and an over-budget pipeline raises CUDA OOM
exactly as a real 12GB card would. Startup log: `VRAM capped to 12.0GB (0.6138 of 19.6GB)`.

**Honest caveat (why "runs on 12GB" for the new tier stays PENDING).** A real 12GB card also spends
~0.5-1GB on the CUDA context, which lives OUTSIDE the per-process fraction, so a 12GB *allocator* cap is
the optimistic edge of a real 12GB card's usable budget (the earlier base proof, `RESULTS.md`, used an
11GB cap for exactly that headroom). This run proves fit under a hard 12GB allocator cap; a flat "runs
on a 12GB card" claim for `final` waits on a true-12GB-card confirmation run (parked, Conrad's ruling).
The 7.4GB of headroom `final` shows below makes that confirmation very likely to pass, but it is not
claimed until measured.

## Results (warm clip, hard 12GB cap)

| tier | model | engine | res | frames | steps | offload | peak alloc | peak reserved | fits 12GB | cold load | sec/clip (warm) | sample |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| draft (as-tested, REJECTED) | LTX-Video-0.9.7-distilled | condition | 512x320 | 97 | 8 | model | -- | -- | **OOM** | -- | -- | -- |
| standard | LTX-Video (2B) | base_i2v | 704x512 | 121 | 40 | model | 10.46 GB | 10.49 GB | yes | 305.6s | 142.2s | `sample_standard.mp4` |
| **final** | **LTX-Video-0.9.8-13B-distilled** | condition | 768x512 | 121 | 10 | sequential | **3.23 GB** | **4.63 GB** | **yes** | 406.5s | **108.4s** | `sample_final.mp4` |

`peak reserved` is `torch.cuda.max_memory_reserved` (the pool the cap governs), reset immediately before
the timed warm clip so it is the steady-state render peak, not the one-time weights load. Both sample
clips are real h264 (ffprobe: 704x512 and 768x512, 24 fps, 121 frames, 5.04s), not placeholders.

## What this proves

- **The 13B `final` tier FITS 12GB, with room to spare.** 4.63 GB peak reserved under the 12GB cap
  (7.4 GB headroom). Sequential (per-layer) CPU offload pages the 13B transformer a layer at a time, so
  the resident footprint is tiny; VAE tiling bounds the decode. It renders a 768x512 / 5s clip in
  **108.4s warm** -- FASTER than `standard` (142.2s) because the distilled variant needs only 10 steps
  vs the base's 40, so `final` is both higher quality AND quicker here.
- **The dispatch is honest end to end.** `standard` (base 2B, `image=`) and `final` (13B,
  `conditions=[LTXVideoCondition]`) both produced real clips through their respective pipeline classes;
  `final`'s output reports `distilled=true`.
- **The hard cap is real.** The `draft` OOM below is the cap working: the process error reads
  `12.00 GiB allowed; ... 10.50 GiB memory in use ... Tried to allocate 54.00 MiB` -- an honest fit
  failure, not a silent spill into VRAM a 12GB card would not have.

## The `draft` finding (why it stays base, not 0.9.7-distilled)

The tier was first wired `draft -> Lightricks/LTX-Video-0.9.7-distilled` on the premise that
0.9.7-distilled is a light, fast 2B. **It is not: 0.9.7-distilled is itself a 13B-class model**
(transformer config: 48 layers, inner dim 4096), the same scale as the 0.9.8-13B-distilled `final`
model. The base `LTX-Video` is the 2B (28 layers, 2048).

Consequences, both measured/derived:
- At **model-cpu-offload**, a 13B (~26 GB bf16) keeps the whole transformer resident and exceeds even
  the physical 20GB card, so `draft` on 0.9.7-distilled **OOMed** (above).
- Via **sequential offload** it would fit (like `final`) but pages per layer, making it SLOWER than the
  base 2B draft (~48.6s, `RESULTS.md`), which defeats the entire point of a fast preview tier.

So `draft` stays the **proven base 2B i2v** (fast + fitting). The CONDITION engine path, the
`is_distilled` flag, and the optional spatial upsampler all still ship -- `final` uses them.

**Parked follow-up (needs a ruling):** a genuinely-faster distilled draft would need a real 2B distilled
model, e.g. `Lightricks/LTX-Video-0.9.6-distilled` (few-step, 2B). Not wired here; flagged for the next
sprint so the model choice is a decision, not a silent swap.

## Cost + hygiene

One warm run of all three tiers on the free propagandhi lane ($0 GPU spend; no RunPod pod). Model
downloads (base 2B + 0.9.7-distilled 13B + 0.9.8-13B-distilled + shared T5 text encoder) were staged to
a scratch HF cache on the box and removed after this proof; the repo carries only the small artifacts
here. Prod untouched.
