# Proof gate: LTX-Video i2v on the 16GB floor (PASSED)

Live benchmark on the design floor (RTX 2000 Ada 16GB, the closest Ada/16GB/fp8 proxy for the RTX
4060 Ti 16GB target). Engine: `LTXImageToVideoPipeline` + `Lightricks/LTX-Video` (bf16) with
`enable_model_cpu_offload()` + VAE tiling. Synthetic keyframe (`keyframe.png`); same motion prompt.

| point | resolution | frames | steps | peak VRAM | fits 16GB | sec/clip | sample |
|---|---|---|---|---|---|---|---|
| draft | 512x320 | 97 (~4s @24fps) | 25 | **10.44 GB** | yes (~6GB headroom) | **38.6s** | `sample_draft.mp4` |
| standard | 704x512 | 121 (~5s @24fps) | 40 | **10.46 GB** | yes (~6GB headroom) | **125.6s** | `sample_standard.mp4` |

Model load (cold): 39.7s. GPU: NVIDIA RTX 2000 Ada Generation (16.7 GB total). No OOM at either point.

## What this proves

- **FIT:** LTX i2v runs in **~10.4 GB** on a 16GB card with model-cpu-offload + VAE tiling -- a ~6GB
  margin. The 16GB floor is comfortable, not marginal; bigger cards have ample headroom, and there is
  room to push resolution or add the heavier 13B path later.
- **SPEED:** a usable ~5s clip at 704x512 in ~2 minutes on a consumer-class Ada card; the 512x320
  draft in under 40s. Acceptable for a homelab render-on-your-own-GPU workflow.
- **QUALITY:** real i2v clips were produced (the `.mp4`s here) -- eyeball them. Not a placeholder; the
  pipeline animates the keyframe end to end.

## Cost + hygiene

Iterated across 4 short pods (env fixes: PEP668 pip, a deps gap for the T5 tokenizer); every pod was
auto-deleted the instant its run finished (DELETE http 204 each). Total spend a few minutes of a
$0.24/hr pod -- well under ~$0.10. No pod left idling.

## Follow-ups (fold into the engine)

- The measured peak (~10.4GB) and the working path (`Lightricks/LTX-Video` base, ~40 steps,
  guidance 3) are folded into `src/vivijure_local/config.py` + `vram.py`.
- The few-step distilled + 13B "final" tier (better quality / faster) uses a different pipeline class
  (`LTXConditionPipeline` + the spatial upscaler) -- a follow-up engine pass; the base i2v proven here
  is the floor.
