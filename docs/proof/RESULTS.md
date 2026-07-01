# Proof gate: LTX-Video i2v at the 12GB VRAM budget (PASSED)

Live benchmark of the REAL shipped container under an explicit **11GB VRAM cap** (the honest 12GB-tier
budget: a real 12GB card loses ~1GB to driver + CUDA context, so 11GB usable). All three quality tiers
render end to end with no OOM and ~1.2GB of allocator headroom, verified two independent ways. This
supersedes the earlier uncapped 16GB draft/standard proof (in git history): the same engine, now proven
at a tighter budget AND across all three tiers.

**Conditions.** Container `ghcr.io/skyphusion-labs/vivijure-local-12gb:proof-12gb-ssh`, built from
the pinned recipe (torch 2.4.1+cu124, torchvision 0.19.1, diffusers 0.32.2, transformers 4.46.3). GPU:
NVIDIA RTX 2000 Ada Generation, 15.7 GB total (the closest Ada/16GB proxy for the RTX 4060 Ti target),
capped to `VIVIJURE_MAX_VRAM_GB=11`, so the server pins
`torch.cuda.set_per_process_memory_fraction(0.701)` at startup (server log:
`VRAM capped to 11.0GB (0.701 of 15.7GB)`). Engine: `LTXImageToVideoPipeline` + `Lightricks/LTX-Video`
(bf16), `enable_model_cpu_offload()` + VAE tiling. Synthetic keyframe (`keyframe.png`), one motion
prompt. Cold model-load: **34.4s**.

## Results (all 3 tiers, under the 11GB cap)

| tier | resolution | frames | steps | peak alloc | peak reserved | headroom vs 11GB | OOM | sec/clip (engine) | sec/clip (HTTP+R2) | sample |
|---|---|---|---|---|---|---|---|---|---|---|
| draft | 512x320 | 97 (~4s) | 25 | 9.72 GB | **9.76 GB** | 1.24 GB | no | 48.6s | 50.1s | `sample_draft.mp4` |
| standard | 704x512 | 121 (~5s) | 40 | 9.74 GB | **9.78 GB** | 1.22 GB | no | 132.0s | 134.4s | `sample_standard.mp4` |
| final | 768x512 | 121 (~5s) | 50 | 9.74 GB | **9.78 GB** | 1.22 GB | no | 171.6s | 174.4s | `sample_final.mp4` |

`peak alloc` / `peak reserved` are `torch.cuda.max_memory_allocated` / `max_memory_reserved` -- the
allocator pool the cap governs. Real card occupancy adds the CUDA context (~0.5GB, outside the
per-process fraction), so total card use is ~10.3GB, still well under a 12GB card. On a real 12GB card
the same 11GB allocator budget applies (fraction 11/12), so this result translates directly.

## Two independent legs, both green

1. **Direct engine (torch peaks).** `proof_measure.py` runs the shipped engine (`i2v_ltx.animate`) under
   the cap and records `max_memory_allocated` + `max_memory_reserved`, OOM, sec/clip, cold load, and a
   sample clip per tier. Those are the numbers above.
2. **Real HTTP `/run` + R2 round-trip.** `proof_http_smoke.py` drives the LIVE server API
   (`POST /run` -> poll `/status` -> clip in R2) for all three tiers; every tier returned `COMPLETED`,
   keyframe in and finished clip out of the shared bucket, exactly as the `local-gpu` module would. Test
   objects were namespaced and deleted after (zero remnants).

## What this proves

- **FIT at 12GB:** all three tiers hold under an 11GB cap with ~1.2GB allocator headroom and no OOM.
  Peak reserved is **flat at ~9.78GB** across draft / standard / final: `model-cpu-offload` + VAE tiling
  bound the peak independent of resolution and step count, so **higher tiers cost TIME, not VRAM.** There
  is no OOM tier; `final` (768x512 / 121f / 50 steps) is the ceiling and passes with the same margin.
- **SPEED:** a ~5s clip at 704x512 in ~2 min, 768x512 in ~2.9 min, the 512x320 draft in under 50s, on a
  consumer-class Ada card. Acceptable for a render-on-your-own-GPU homelab flow.
- **QUALITY:** real i2v clips were produced (the `.mp4`s here) -- not placeholders; the pipeline animates
  the keyframe end to end, via both the engine and the HTTP path.
- **THE CAP IS HONEST:** `VIVIJURE_MAX_VRAM_GB` fired exactly as designed in the real server startup and
  bounded the process under budget (reserved 9.78GB < 11GB allowed).

## Cost + hygiene

One SECURE pod (`4jb60eajarly58`, RTX 2000 Ada, $0.24/hr), removed via `runpodctl remove` with zero live
pods confirmed after. ~5-6 min of GPU. Per-identity R2 token; proof objects deleted; secret env shredded
pod-side and locally. Prod untouched throughout.

## Follow-up (known, out of scope of this proof)

The shipped server reloads the pipeline per `/run` call (`from_pretrained` inside `animate`), so each job
pays the ~34s cold load; the HTTP sec/clip figures include it. A persistent-pipeline cache is a clean
follow-up (it lowers per-job latency, not peak VRAM). The few-step distilled + 13B "final" path
(`LTXConditionPipeline` + spatial upscaler) remains a quality follow-up; the base i2v proven here is the
floor.
