"""Consumer-scoped render config for the local backend (the 12GB door).

This is the honest counterpart to vivijure-backend's `config.py`. The datacenter backend maps the
quality tiers (draft / standard / final) onto Wan 2.2 A14B step counts and datacenter GPU classes
(RTX PRO 6000 / H200 / B200). THIS backend maps the SAME tier vocabulary onto LTX-Video engine
configs a single 12GB consumer card can ACTUALLY run -- so "final" here is the card's honest
ceiling, NOT datacenter parity.

Why the tiers keep the same names: the control plane owns the tier set (QUALITY_TIERS) and INJECTS
the chosen tier into every motion.backend module as `quality`. `validateConfig` silently DROPS an
injected value not in the module's enum, so the local-gpu module's enum stays draft/standard/final
(see vivijure/tests/quality-tier-drift.test.ts, #124). The HONESTY is in the engine mapping below and
in `docs/i2v-model-selection.md`, not in renaming the tiers.

The tier->engine dispatch (#1): each tier declares WHICH diffusers pipeline it drives (`engine`) and
which LTX weights (`model`). `draft` + `standard` stay the PROVEN base 2B i2v (LTXImageToVideoPipeline,
the safe default validated in docs/proof/RESULTS.md); `final` drives the 13B-distilled variant through
LTXConditionPipeline (an LTXVideoCondition input, NOT `image=`), the quality ceiling PROVEN to fit a
hard 12GB allocator cap via sequential offload (docs/proof/BENCH-13B.md). Nothing here trains or
generates; this module is pure + CPU-importable (no torch), exactly like vivijure-backend's config.py.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum


class QualityTier(str, Enum):
    """The control-plane tier vocabulary. Parsed leniently; unknown -> STANDARD (the safe middle)."""

    DRAFT = "draft"
    STANDARD = "standard"
    FINAL = "final"

    @classmethod
    def parse(cls, v: object) -> "QualityTier":
        try:
            return cls(str(v).strip().lower())
        except Exception:
            return cls.STANDARD


class Offload(str, Enum):
    """How aggressively the diffusers pipeline trades speed for VRAM headroom. Ordered weakest ->
    strongest. The stronger the offload, the more the card fits but the slower the run (sequential
    shuttles each layer on/off the GPU per step)."""

    NONE = "none"                      # everything resident on the GPU (fastest; only the lightest config)
    MODEL_CPU_OFFLOAD = "model"        # whole submodules paged to CPU between uses (diffusers enable_model_cpu_offload)
    SEQUENTIAL_CPU_OFFLOAD = "sequential"  # per-layer paging (slowest, smallest footprint; the 13B / 12GB fit)


class Engine(str, Enum):
    """Which diffusers pipeline class a tier drives. The dispatch in `i2v_ltx.animate` reads this.

    BASE_I2V is the PROVEN path: `LTXImageToVideoPipeline` with an `image=` keyframe (validated across
    all three tiers under an 11GB cap, docs/proof/RESULTS.md) -- the safe default. CONDITION is
    `LTXConditionPipeline`, which the 13B-distilled variant requires: the keyframe is passed as an
    `LTXVideoCondition` (frame 0), NOT `image=`. Keeping this as a tier field means a tier that does not
    fit under the 12GB cap can fall back to BASE_I2V without touching the engine."""

    BASE_I2V = "base_i2v"    # LTXImageToVideoPipeline (image=), the proven default
    CONDITION = "condition"  # LTXConditionPipeline (LTXVideoCondition), the 13B-distilled path


# LTX model variants. The base is the 2B i2v, PROVEN across all tiers (docs/proof/RESULTS.md). The 13B
# distilled variant is the `final` quality ceiling and loads through the CONDITION pipeline. See
# docs/i2v-model-selection.md for the comparison and docs/proof/BENCH-13B.md for the measured 12GB fit.
#
# NOTE (measured, #1): `Lightricks/LTX-Video-0.9.7-distilled` is itself a 13B-class model (48 layers,
# 4096 inner dim), NOT a light 2B. It was tested as a fast `draft` and OOMed a 12GB card at
# model-cpu-offload; via sequential offload it is slower than the base draft, so it does not serve as a
# fast preview. A genuinely-faster distilled draft would need a 2B distilled model (e.g. 0.9.6-distilled)
# -- a parked follow-up, not wired here. Details in docs/proof/BENCH-13B.md.
LTX_BASE = "Lightricks/LTX-Video"                                 # base 2B i2v (draft + standard) -- proven, safe default
LTX_13B_DISTILLED = "Lightricks/LTX-Video-0.9.8-13B-distilled"   # 13B distilled (final) -- quality ceiling, proven to fit 12GB
LTX_SPATIAL_UPSAMPLER = "Lightricks/ltxv-spatial-upscaler-0.9.7"  # optional generate-low-then-upscale latent upsampler (wired, off)


@dataclass(frozen=True)
class TierConfig:
    """The engine knobs one quality tier maps to on a 12GB card. The animate() body reads these; the
    frame-count is derived per shot (config.py never fixes a film's length)."""

    model: str
    steps: int             # base LTX i2v ~25-50 denoise steps; the 13B-distilled variant runs few-step (~10)
    guidance_scale: float  # base LTX i2v ~3.0 (model-card default); the distilled 13B runs CFG-free (~1.0)
    width: int             # must be divisible by 32 (LTX constraint), enforced in i2v_ltx.snap_dim
    height: int
    max_frames: int        # ceiling for this tier (snapped to 8k+1 by i2v_ltx.snap_frames)
    offload: Offload
    vae_tiling: bool       # decode the VAE in tiles to bound peak decode VRAM (the big 12GB saver)
    engine: Engine         # which diffusers pipeline class this tier drives (base i2v vs condition)
    upsampler: str | None  # optional spatial latent upsampler for generate-low-then-upscale (None = off)


# The 12GB tier ladder, all VALIDATED on real silicon. `draft` + `standard` are the PROVEN base 2B i2v
# (docs/proof/RESULTS.md); `final` is the 13B-distilled variant via the CONDITION pipeline, PROVEN under
# a hard 12GB allocator cap (docs/proof/BENCH-13B.md: peak reserved 4.63GB, 108.4s/clip). Same base
# model for draft/standard (they differ by resolution + steps); final trades the engine for quality.
_TIERS: dict[QualityTier, TierConfig] = {
    # Fast preview: the PROVEN base 2B i2v at low res / 25 steps (measured 48.6s/clip, peak ~9.76GB under
    # the 11GB cap; docs/proof/RESULTS.md). The base engine is the fast draft path. A 13B distilled draft
    # was tested and REJECTED (0.9.7-distilled is a 13B model, OOMs at model-offload / slower than base
    # via sequential offload -- docs/proof/BENCH-13B.md), so draft stays base for speed.
    QualityTier.DRAFT: TierConfig(
        model=LTX_BASE, steps=25, guidance_scale=3.0,
        width=512, height=320, max_frames=97, offload=Offload.MODEL_CPU_OFFLOAD, vae_tiling=True,
        engine=Engine.BASE_I2V, upsampler=None,
    ),
    # The comfortable middle: the PROVEN base i2v (704x512 / 121f / 40 steps, measured 132.0s/clip, peak
    # 9.78GB under the 11GB cap in docs/proof/RESULTS.md; re-measured 10.49GB / 142.2s under the hard 12GB
    # cap in docs/proof/BENCH-13B.md). The safe default engine.
    QualityTier.STANDARD: TierConfig(
        model=LTX_BASE, steps=40, guidance_scale=3.0,
        width=704, height=512, max_frames=121, offload=Offload.MODEL_CPU_OFFLOAD, vae_tiling=True,
        engine=Engine.BASE_I2V, upsampler=None,
    ),
    # The card's HONEST quality ceiling: the 13B-distilled variant via the CONDITION pipeline. 13B
    # weights are far larger than the base 2B, so this tier pages per-layer (SEQUENTIAL cpu offload) +
    # VAE tiling to hold the budget. PROVEN under a hard 12GB allocator cap on a 20GB card: peak reserved
    # 4.63GB (7.4GB headroom), 108.4s/clip warm -- fewer steps (distilled) make it FASTER than standard
    # while higher quality (docs/proof/BENCH-13B.md). A true-12GB-card confirmation run is still pending.
    QualityTier.FINAL: TierConfig(
        model=LTX_13B_DISTILLED, steps=10, guidance_scale=1.0,
        width=768, height=512, max_frames=121, offload=Offload.SEQUENTIAL_CPU_OFFLOAD, vae_tiling=True,
        engine=Engine.CONDITION, upsampler=None,
    ),
}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


# Operator override for the diffusers offload mode (12gb#91). UNSET (the default) keeps each tier
# hardcoded, consumer-card-safe strategy byte-for-byte -- no behavior change. A big-VRAM operator can set
# VIVIJURE_OFFLOAD=none to run the model RESIDENT (no per-step CPU paging, faster) or =sequential for the
# low-VRAM fallback; when set it applies to EVERY tier. An INVALID value is a LOUD startup failure
# (server.validate_offload_or_exit), never a silent default -- a fat-fingered knob must surface at boot,
# not as a slow or OOM run later.
OFFLOAD_ENV = "VIVIJURE_OFFLOAD"


def parse_offload_override(raw: object) -> "Offload | None":
    """Parse a VIVIJURE_OFFLOAD value to an Offload override, or None when unset/blank (keep the tier
    default). Pure + CPU-only. Raises ValueError on a non-empty value that is not a valid mode, so the
    operator learns at startup instead of silently getting the per-tier default."""
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if not s:
        return None
    try:
        return Offload(s)
    except ValueError:
        valid = ", ".join(o.value for o in Offload)
        raise ValueError(
            f"{OFFLOAD_ENV}={raw!r} is not a valid offload mode; use one of: {valid} "
            "(or leave it unset to keep each tier default)"
        ) from None


def offload_override() -> "Offload | None":
    """The active VIVIJURE_OFFLOAD override read from the environment (None when unset). Raises
    ValueError on an invalid value; the server validates it loudly at startup."""
    return parse_offload_override(os.environ.get(OFFLOAD_ENV))


@dataclass(frozen=True)
class I2VConfig:
    """The per-shot i2v config the server hands the engine: a tier baseline with the caller's clamped
    overrides layered on. Mirrors the wire body the local-gpu module sends (quality / num_frames / fps
    / seed / flow_shift / negative_prompt), so the field names match end to end (no remap layer)."""

    tier: QualityTier
    model: str
    steps: int
    guidance_scale: float
    width: int
    height: int
    num_frames: int
    fps: int
    seed: int
    flow_shift: float
    offload: Offload
    vae_tiling: bool
    negative_prompt: str
    engine: Engine
    upsampler: str | None

    @classmethod
    def from_request(cls, cfg: dict, *, tier: QualityTier | None = None) -> "I2VConfig":
        """Build from the i2v_clip job's `config` dict. The tier baseline is the source of truth; the
        caller may narrow (never widen) it. width/height/num_frames default to the tier; an explicit
        value is clamped to the tier ceiling so a caller can never push the card past its honest fit.
        The engine + upsampler are FIXED by the tier (not caller-overridable): they are the proven fit,
        not a knob a request may widen.
        """
        cfg = cfg or {}
        t = tier or QualityTier.parse(cfg.get("quality"))
        base = _TIERS[t]
        # Frame count: caller's request, capped at the tier ceiling (the card's honest limit), then
        # snapped to LTX's 8k+1 stride by the engine. Default to the tier ceiling when unset.
        req_frames = _coerce_int(cfg.get("num_frames"), base.max_frames)
        num_frames = min(max(1, req_frames), base.max_frames)
        # Resolution: clamp to the tier ceiling on each axis (never widen past the honest fit).
        width = min(base.width, _coerce_int(cfg.get("width"), base.width) or base.width)
        height = min(base.height, _coerce_int(cfg.get("height"), base.height) or base.height)
        seed = _coerce_int(cfg.get("seed"), -1)
        fps = min(30, max(8, _coerce_int(cfg.get("fps"), 24)))
        flow_shift = _coerce_float(cfg.get("flow_shift"), 5.0)
        # Operator offload override (VIVIJURE_OFFLOAD): when set it replaces this tier default for
        # EVERY tier; unset keeps the per-tier default byte-for-byte (12gb#91).
        override = offload_override()
        offload = override if override is not None else base.offload
        return cls(
            tier=t, model=base.model, steps=base.steps, guidance_scale=base.guidance_scale,
            width=width, height=height, num_frames=num_frames, fps=fps, seed=seed,
            flow_shift=flow_shift, offload=offload, vae_tiling=base.vae_tiling,
            negative_prompt=str(cfg.get("negative_prompt") or ""),
            engine=base.engine, upsampler=base.upsampler,
        )


def tier_config(tier: QualityTier) -> TierConfig:
    """The engine baseline for a tier (a copy-safe frozen dataclass)."""
    return _TIERS[tier]


def _coerce_int(v: object, default: int) -> int:
    try:
        if v is None or isinstance(v, bool):
            return default
        return int(v)
    except (TypeError, ValueError):
        return default


def _coerce_float(v: object, default: float) -> float:
    try:
        if v is None or isinstance(v, bool):
            return default
        return float(v)
    except (TypeError, ValueError):
        return default
