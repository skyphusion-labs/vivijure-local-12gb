"""Image-to-video on a 12GB consumer card: LTX-Video.

The local door's engine. The keyframe is the still; this turns it into motion. LTX-Video takes the
keyframe as the first (conditioning) frame and the scene prompt as the motion description and produces
N frames. LTX was chosen over CogVideoX / SVD / AnimateDiff for the 12GB floor on fit + speed +
license (the full comparison is docs/i2v-model-selection.md): it is the lightest real i2v model, runs
few-step (distilled), and its Open Weights License is the cleanest match for a freely-given AGPL
project.

Two engine paths, dispatched per tier by `config.Engine` (#1):
  - BASE_I2V (`standard`): the PROVEN `LTXImageToVideoPipeline` with an `image=` keyframe, validated
    across all three tiers under an 11GB cap (docs/proof/RESULTS.md). The safe default.
  - CONDITION (`draft`, `final`): `LTXConditionPipeline` with the keyframe passed as an
    `LTXVideoCondition` at frame 0 (NOT `image=`) -- the class the few-step distilled (0.9.7-distilled)
    and 13B-distilled variants require, optionally paired with the spatial latent upsampler for
    generate-low-then-upscale.

Clean-room: built from diffusers' LTX pipelines + export_to_video and the LTX model card's own
constraints (frames 8k+1, dims divisible by 32), not from any prior pipeline. The frame-count /
dimension math and the tier->engine mapping are PURE and CPU-tested; the generation body defers
torch/diffusers and is validated on the card (mirroring vivijure-backend's i2v.animate). Engine knobs
come from `config.I2VConfig`.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from .config import Engine, I2VConfig, Offload

# LTX's VAE compresses time by 8, so a clip's frame count must be 8k+1 (e.g. 121 = 8*15+1, ~5s at
# 24 fps). The model card caps a clip at 257 frames; spatial dims must be divisible by 32.
TEMPORAL_STRIDE = 8
SPATIAL_MULTIPLE = 32
MAX_FRAMES = 257
DEFAULT_FPS = 24

# The default negative prompt when the request carries none (one place, both engine paths).
DEFAULT_NEGATIVE = "worst quality, blurry, jittery, distorted"

# generate-low-then-upscale: generate at this fraction of the target, then the spatial upsampler
# doubles it in latent space (the documented LTX 2x flow). 2/3 * 2 = ~1.33x the base tier resolution.
UPSAMPLE_DOWNSCALE = 2.0 / 3.0
UPSAMPLE_DENOISE_STRENGTH = 0.4   # short refine pass after upscale (adds detail without a full re-gen)
UPSAMPLE_REFINE_STEPS = 8


# --------------------------------------------------------------------------- pure helpers

def snap_frames(n: int, max_frames: int = MAX_FRAMES) -> int:
    """Snap a frame count to the nearest valid 8k+1 the LTX temporal VAE accepts (rounding UP so a clip
    never comes out shorter than asked), clamped to [1, max_frames].

    snap-then-clamp so the result is always 8k+1 even after the ceiling applies: if rounding up would
    exceed max_frames, step down to the largest 8k+1 <= max_frames."""
    n = max(1, int(n))
    rem = (n - 1) % TEMPORAL_STRIDE
    snapped = n if rem == 0 else n + (TEMPORAL_STRIDE - rem)
    if snapped <= max_frames:
        return snapped
    prev = max_frames - ((max_frames - 1) % TEMPORAL_STRIDE)
    return max(1, prev)


def snap_dim(px: int) -> int:
    """Snap a spatial dimension DOWN to a multiple of 32 (never up, so a clamped tier ceiling stays a
    ceiling), with a floor of 32."""
    return max(SPATIAL_MULTIPLE, (int(px) // SPATIAL_MULTIPLE) * SPATIAL_MULTIPLE)


def frames_for(target_seconds: float | None, fps: int = DEFAULT_FPS, *, max_frames: int = MAX_FRAMES) -> int:
    """Frame count for a target duration at `fps`: snap to 8k+1 and cap at the ceiling. Falls back to
    the ceiling when no target is given."""
    if not target_seconds or target_seconds <= 0:
        return snap_frames(max_frames, max_frames)
    return snap_frames(round(target_seconds * fps), max_frames)


def clip_seconds(num_frames: int, fps: int = DEFAULT_FPS) -> float:
    """The realized clip length. i2v fixes the first frame to the keyframe, so N frames play as N/fps
    seconds."""
    return round(num_frames / max(1, fps), 3)


def resolve_engine_dims(cfg: I2VConfig) -> tuple[int, int, int]:
    """The (width, height, num_frames) actually fed to the pipeline: tier dims snapped to /32 and the
    frame count snapped to 8k+1 under the tier ceiling. Pure, so the server can report the realized
    shape before any GPU work."""
    return snap_dim(cfg.width), snap_dim(cfg.height), snap_frames(cfg.num_frames)


def is_distilled(cfg: I2VConfig) -> bool:
    """Whether this tier's weights are a distilled (few-step) variant. Honest: the base i2v is False;
    the 0.9.7-distilled + 13B-distilled variants are True. Reported in I2VResult so the caller/UI can
    tell which path produced the clip (a distilled clip is faster but lower fidelity than the base)."""
    return "distilled" in cfg.model.lower()


# --------------------------------------------------------------------------- result

@dataclass
class I2VResult:
    """The outcome of animating one keyframe: where the clip landed, its frame count / fps / length,
    and whether a few-step distilled variant produced it."""

    shot_id: str
    path: Path
    num_frames: int
    fps: int
    seconds: float
    distilled: bool


# --------------------------------------------------------------------------- pipeline cache (process-lifetime)

# One offload-configured pipeline per (model, engine, offload, vae_tiling), built once per process and
# reused. The datacenter backend scales to zero between jobs; the local box is always-on and serial (ONE
# job at a time -- a 12GB card cannot fit two i2v pipelines), so the honest optimisation is the opposite:
# keep the resident pipeline warm instead of re-reading the full weights (~30s + a full disk/VRAM reload)
# every clip. The registry is single-worker serial (jobs.py), so no lock is needed. The optional spatial
# upsampler (generate-low-then-upscale) is cached separately, sharing the main pipe's VAE.
_PIPE_CACHE: dict = {}
_UPSAMPLER_CACHE: dict = {}


def _pipe_cache_key(cfg: I2VConfig):
    """The cache identity: two jobs share a pipeline only if the model, engine class, AND the offload
    wiring match. Offload hooks mutate the pipeline in place at build, so a pipe configured for one
    offload/tiling mode must not be reused under another; the engine (base vs condition) is a different
    diffusers class entirely, so it is part of the key too."""
    return (cfg.model, cfg.engine.value, cfg.offload.value, bool(cfg.vae_tiling))


def _get_pipe(cfg: I2VConfig, pipeline_cls, torch):
    """Return the process-cached, offload-configured pipeline for `cfg`, building it once on a miss.

    The heavy from_pretrained + `_apply_offload` runs ONCE per key; subsequent jobs reuse the fully
    configured resident pipeline. Offload is applied only at build (re-applying per job is redundant and
    can double-wrap the diffusers hooks), so the cache stores the ready-to-run pipe."""
    key = _pipe_cache_key(cfg)
    pipe = _PIPE_CACHE.get(key)
    if pipe is None:
        pipe = pipeline_cls.from_pretrained(cfg.model, torch_dtype=torch.bfloat16)
        _apply_offload(pipe, cfg)
        _PIPE_CACHE[key] = pipe
    return pipe


def _get_upsampler(cfg: I2VConfig, main_pipe, torch):
    """Return the process-cached spatial latent upsampler for `cfg.upsampler`, sharing the main pipe's
    VAE (so the ~GB VAE is not loaded twice) and configured with the same offload strategy. Built once
    per (upsampler_model, offload) on a miss. Only reached when a tier sets `upsampler`."""
    key = (cfg.upsampler, cfg.offload.value)
    pipe = _UPSAMPLER_CACHE.get(key)
    if pipe is None:
        from diffusers import LTXLatentUpsamplePipeline
        vae = getattr(main_pipe, "vae", None)
        pipe = LTXLatentUpsamplePipeline.from_pretrained(cfg.upsampler, vae=vae, torch_dtype=torch.bfloat16)
        _apply_offload(pipe, cfg)
        _UPSAMPLER_CACHE[key] = pipe
    return pipe


def _evict_pipe(cfg: I2VConfig, torch) -> None:
    """Drop the cached pipeline (and any upsampler) for `cfg` after a failed render and release its VRAM.
    A failed generate can leave the pipeline or the CUDA allocator in a bad state; the honest recovery is
    to rebuild fresh on the next job rather than reuse a poisoned pipe. The free is EXPLICIT here, not
    GC-timing-dependent -- the 12GB budget runs on thin headroom (docs/proof/RESULTS.md), too thin to
    wait on the collector."""
    _PIPE_CACHE.pop(_pipe_cache_key(cfg), None)
    if cfg.upsampler:
        _UPSAMPLER_CACHE.pop((cfg.upsampler, cfg.offload.value), None)
    try:
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


# --------------------------------------------------------------------------- animate (GPU, deferred)

def animate(shot_id: str, keyframe: Path, prompt: str, cfg: I2VConfig, out_path: Path, *, progress_cb=None) -> I2VResult:
    """Animate `keyframe` into a clip at `out_path` for one shot, on the local card.

    Heavy imports (torch / diffusers) are DEFERRED so this module stays CPU-importable and the pure
    helpers above test without a GPU; this body is validated on the card (the spend gate -- see
    docs/live-benchmark-plan.md). The tier's `engine` selects the pipeline: BASE_I2V drives
    `LTXImageToVideoPipeline` (image=), CONDITION drives `LTXConditionPipeline` (an LTXVideoCondition),
    optionally with the spatial upsampler when the tier sets `upsampler`. The offload-configured
    pipeline is process-cached (`_get_pipe`) so only the FIRST job on a warm box pays the weights load;
    `progress_cb(step, total)` is wired best-effort through diffusers' callback hook. On a failed
    generate the pipeline is evicted and its VRAM freed (`_evict_pipe`) so a poisoned pipe never carries
    into the next job.

    It raises if torch/diffusers is absent rather than pretending to render (a producer stage never
    fakes output)."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        import torch  # deferred: keep this module CPU-importable
        from diffusers import LTXImageToVideoPipeline
        from diffusers.utils import export_to_video, load_image
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            f"i2v_ltx.animate requires torch + diffusers (LTX pipelines): {e}. "
            "This is the GPU body; install the card runtime (requirements.txt) or run the pure helpers."
        ) from e

    width, height, num_frames = resolve_engine_dims(cfg)
    image = load_image(str(keyframe))

    seed = cfg.seed if cfg.seed >= 0 else 0
    generator = torch.Generator(device="cpu").manual_seed(seed)
    step_callback = _step_callback(progress_cb, cfg.steps)

    try:
        if cfg.engine is Engine.CONDITION:
            result_frames = _run_condition(cfg, image, prompt, width, height, num_frames, generator, step_callback, torch)
        else:
            result_frames = _run_base(cfg, image, prompt, width, height, num_frames, generator, step_callback, torch, LTXImageToVideoPipeline)
    except Exception:
        _evict_pipe(cfg, torch)  # a cancel or an OOM must not leave a poisoned pipe cached
        raise

    export_to_video(result_frames, str(out_path), fps=cfg.fps)
    return I2VResult(
        shot_id=shot_id or "shot", path=out_path, num_frames=num_frames, fps=cfg.fps,
        seconds=clip_seconds(num_frames, cfg.fps), distilled=is_distilled(cfg),
    )


def _run_base(cfg, image, prompt, width, height, num_frames, generator, step_callback, torch, pipeline_cls):
    """The PROVEN base i2v path: LTXImageToVideoPipeline with the keyframe as `image=`. Unchanged from
    the validated call shape (docs/proof/RESULTS.md)."""
    pipe = _get_pipe(cfg, pipeline_cls, torch)
    return pipe(
        image=image,
        prompt=prompt,
        negative_prompt=cfg.negative_prompt or DEFAULT_NEGATIVE,
        width=width,
        height=height,
        num_frames=num_frames,
        num_inference_steps=cfg.steps,
        guidance_scale=cfg.guidance_scale,
        generator=generator,
        **({"callback_on_step_end": step_callback} if step_callback else {}),
    ).frames[0]


def _run_condition(cfg, image, prompt, width, height, num_frames, generator, step_callback, torch):
    """The distilled / 13B path: LTXConditionPipeline with the keyframe as an LTXVideoCondition at frame
    0 (NOT `image=`). When the tier sets `upsampler`, run the generate-low-then-upscale flow; otherwise a
    single-pass generate at the tier resolution."""
    from diffusers import LTXConditionPipeline

    conditions = _build_conditions(image)
    pipe = _get_pipe(cfg, LTXConditionPipeline, torch)
    common = dict(
        conditions=conditions,
        prompt=prompt,
        negative_prompt=cfg.negative_prompt or DEFAULT_NEGATIVE,
        num_frames=num_frames,
        num_inference_steps=cfg.steps,
        guidance_scale=cfg.guidance_scale,
        generator=generator,
    )
    if step_callback:
        common["callback_on_step_end"] = step_callback

    if cfg.upsampler:
        return _run_condition_upsampled(cfg, pipe, common, width, height, generator, torch)
    return pipe(width=width, height=height, **common).frames[0]


def _run_condition_upsampled(cfg, pipe, common, width, height, generator, torch):
    """generate-low-then-upscale (the documented LTX 2x flow): generate latents at UPSAMPLE_DOWNSCALE of
    the target, upscale 2x in latent space with the spatial upsampler (sharing the VAE), then a short
    refine denoise pass at the upscaled resolution before decode. Produces sharper detail than a direct
    generate at the same VRAM budget. OFF unless the tier sets `upsampler`."""
    up = _get_upsampler(cfg, pipe, torch)
    low_w, low_h = snap_dim(int(width * UPSAMPLE_DOWNSCALE)), snap_dim(int(height * UPSAMPLE_DOWNSCALE))

    # Part 1: generate at the smaller resolution, returning latents (no VAE decode yet).
    latents = pipe(width=low_w, height=low_h, output_type="latent", **common).frames

    # Part 2: upscale the latents 2x with the spatial upsampler.
    up_latents = up(latents=latents, output_type="latent").frames
    up_w, up_h = snap_dim(low_w * 2), snap_dim(low_h * 2)

    # Part 3: a short refine pass at the upscaled resolution, then decode to frames.
    refine = dict(common)
    refine["num_inference_steps"] = UPSAMPLE_REFINE_STEPS
    return pipe(
        width=up_w, height=up_h, latents=up_latents,
        denoise_strength=UPSAMPLE_DENOISE_STRENGTH, output_type="pil", **refine,
    ).frames[0]


def _build_conditions(image):
    """Wrap the keyframe as an LTXVideoCondition at frame 0 -- the conditioning input LTXConditionPipeline
    expects in place of `image=`. Import is location-tolerant across diffusers builds (top-level export
    vs the pipeline module)."""
    cls = _condition_cls()
    return [cls(image=image, frame_index=0)]


def _condition_cls():
    """Resolve the LTXVideoCondition class, tolerating the two diffusers import locations."""
    try:
        from diffusers import LTXVideoCondition
        return LTXVideoCondition
    except Exception:
        from diffusers.pipelines.ltx.pipeline_ltx_condition import LTXVideoCondition
        return LTXVideoCondition


def _apply_offload(pipe, cfg: I2VConfig) -> None:
    """Apply the config's VRAM strategy to the pipeline so the run fits the 12GB budget. Best-effort per step: a
    diffusers build that lacks a hook runs without it rather than failing the render."""
    if cfg.vae_tiling:
        for fn in ("enable_vae_tiling", "enable_tiling"):
            vae = getattr(pipe, "vae", None)
            target = pipe if fn == "enable_vae_tiling" else vae
            hook = getattr(target, fn, None) if target is not None else None
            if callable(hook):
                try:
                    hook()
                    break
                except Exception:
                    pass
    if cfg.offload is Offload.SEQUENTIAL_CPU_OFFLOAD:
        applied = _try(pipe, "enable_sequential_cpu_offload")
    elif cfg.offload is Offload.MODEL_CPU_OFFLOAD:
        applied = _try(pipe, "enable_model_cpu_offload")
    else:
        applied = _try(pipe, "to", "cuda")
    if not applied:
        _log(f"offload strategy {cfg.offload.value!r} did not apply (hook absent or raised) -- the run may OOM on a consumer card")


def _log(msg: str) -> None:
    """Operator-facing log to stderr (the box tails its own logs; stdout stays clean for the server)."""
    print(f"vivijure-local: {msg}", file=sys.stderr, flush=True)


def _try(obj, name: str, *args) -> bool:
    """Call obj.name(*args) when present. Returns True if it ran cleanly. A present hook that RAISES is
    logged loudly -- offload IS the consumer-VRAM fit, so a swallowed failure would surface only as an
    OOM blamed on the model; an ABSENT hook returns False quietly (an expected diffusers-build
    difference, reported once by the caller when it means no offload applied at all)."""
    hook = getattr(obj, name, None)
    if not callable(hook):
        return False
    try:
        hook(*args)
        return True
    except Exception as e:  # noqa: BLE001
        _log(f"offload/vram hook {name!r} raised: {e} -- VRAM headroom at risk")
        return False


def _step_callback(progress_cb, total: int):
    """Wrap a `(step, total)` callback in diffusers' callback_on_step_end signature. Returns None when
    there is no callback (zero overhead). Best-effort: a progress failure never breaks the denoise."""
    if progress_cb is None:
        return None

    def on_step_end(pipe, step_index, timestep, callback_kwargs):
        try:
            progress_cb(step_index + 1, total)
        except Exception:
            pass
        return callback_kwargs

    return on_step_end
