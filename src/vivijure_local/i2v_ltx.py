"""Image-to-video on a 12GB consumer card: LTX-Video.

The local door's engine. The keyframe is the still; this turns it into motion. LTX-Video takes the
keyframe as the first (conditioning) frame and the scene prompt as the motion description and produces
N frames. LTX was chosen over CogVideoX / SVD / AnimateDiff for the 12GB floor on fit + speed +
license (the full comparison is docs/i2v-model-selection.md): it is the lightest real i2v model, runs
few-step (distilled), and its Open Weights License is the cleanest match for a freely-given AGPL
project.

Clean-room: built from diffusers' LTXImageToVideoPipeline + export_to_video and the LTX model card's
own constraints (frames 8k+1, dims divisible by 32), not from any prior pipeline. The frame-count /
dimension math and the tier->engine mapping are PURE and CPU-tested; the generation body defers
torch/diffusers and is validated on the card (mirroring vivijure-backend's i2v.animate). Engine knobs
come from `config.I2VConfig`.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import I2VConfig, Offload

# LTX's VAE compresses time by 8, so a clip's frame count must be 8k+1 (e.g. 121 = 8*15+1, ~5s at
# 24 fps). The model card caps a clip at 257 frames; spatial dims must be divisible by 32.
TEMPORAL_STRIDE = 8
SPATIAL_MULTIPLE = 32
MAX_FRAMES = 257
DEFAULT_FPS = 24


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


# --------------------------------------------------------------------------- result

@dataclass
class I2VResult:
    """The outcome of animating one keyframe: where the clip landed, its frame count / fps / length,
    and whether the few-step distilled path produced it."""

    shot_id: str
    path: Path
    num_frames: int
    fps: int
    seconds: float
    distilled: bool


# --------------------------------------------------------------------------- animate (GPU, deferred)

def animate(shot_id: str, keyframe: Path, prompt: str, cfg: I2VConfig, out_path: Path, *, progress_cb=None) -> I2VResult:
    """Animate `keyframe` into a clip at `out_path` for one shot, on the local card.

    Heavy imports (torch / diffusers) are DEFERRED so this module stays CPU-importable and the pure
    helpers above test without a GPU; this body is validated on the card (the spend gate -- see
    docs/live-benchmark-plan.md). The offload mode from `cfg` is applied to the pipeline so the run
    fits the 12GB budget; `progress_cb(step, total)` is wired best-effort through diffusers' callback hook.

    VALIDATED on the card (docs/proof/RESULTS.md, diffusers 0.32.2): this exact call shape and the
    12GB offload wiring passed all three tiers under the 11GB cap. It raises if torch/diffusers is
    absent rather than pretending to render (a producer stage never fakes output)."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        import torch  # deferred: keep this module CPU-importable
        from diffusers import LTXImageToVideoPipeline
        from diffusers.utils import export_to_video, load_image
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            f"i2v_ltx.animate requires torch + diffusers (LTXImageToVideoPipeline): {e}. "
            "This is the GPU body; install the card runtime (requirements.txt) or run the pure helpers."
        ) from e

    width, height, num_frames = resolve_engine_dims(cfg)
    image = load_image(str(keyframe))

    dtype = torch.bfloat16
    pipe = LTXImageToVideoPipeline.from_pretrained(cfg.model, torch_dtype=dtype)
    _apply_offload(pipe, cfg)

    seed = cfg.seed if cfg.seed >= 0 else 0
    generator = torch.Generator(device="cpu").manual_seed(seed)
    step_callback = _step_callback(progress_cb, cfg.steps)

    result_frames = pipe(
        image=image,
        prompt=prompt,
        negative_prompt=cfg.negative_prompt or "worst quality, blurry, jittery, distorted",
        width=width,
        height=height,
        num_frames=num_frames,
        num_inference_steps=cfg.steps,
        guidance_scale=cfg.guidance_scale,
        generator=generator,
        **({"callback_on_step_end": step_callback} if step_callback else {}),
    ).frames[0]

    export_to_video(result_frames, str(out_path), fps=cfg.fps)
    return I2VResult(
        shot_id=shot_id or "shot", path=out_path, num_frames=num_frames, fps=cfg.fps,
        seconds=clip_seconds(num_frames, cfg.fps), distilled=False,
    )


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
        _try(pipe, "enable_sequential_cpu_offload")
    elif cfg.offload is Offload.MODEL_CPU_OFFLOAD:
        _try(pipe, "enable_model_cpu_offload")
    else:
        _try(pipe, "to", "cuda")


def _try(obj, name: str, *args) -> None:
    hook = getattr(obj, name, None)
    if callable(hook):
        try:
            hook(*args)
        except Exception:
            pass


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
