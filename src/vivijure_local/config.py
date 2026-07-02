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

These numbers are SCAFFOLD DEFAULTS. The resolution / frame / step ceilings that genuinely fit 12GB
can only be finalized by a live benchmark on real silicon (docs/live-benchmark-plan.md); until then
they are conservative and tunable via env. Nothing here trains or generates; this module is pure +
CPU-importable (no torch), exactly like vivijure-backend's config.py.
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
    SEQUENTIAL_CPU_OFFLOAD = "sequential"  # per-layer paging (slowest, smallest footprint; the 12GB fallback)


# LTX model variants we target on a 12GB card. The 2B-distilled is the lightest real i2v and the
# default; the 13B-fp8-distilled is the quality ceiling that still fits Ada 16GB (fp8 is an Ada
# feature -- the 4060 Ti is Ada). See docs/i2v-model-selection.md for the comparison that chose LTX.
# Benchmark-VALIDATED under an 11GB VRAM cap, the honest 12GB budget (docs/proof/RESULTS.md): the
# base LTX i2v via LTXImageToVideoPipeline peaks at ~9.78GB reserved with model-cpu-offload + VAE tiling.
# This is the proven path. The few-step distilled + 13B variants load via a DIFFERENT pipeline class
# (LTXConditionPipeline + the spatial upscaler) and are a quality FOLLOW-UP, not wired yet.
LTX_BASE = "Lightricks/LTX-Video"


@dataclass(frozen=True)
class TierConfig:
    """The engine knobs one quality tier maps to on a 12GB card. The animate() body reads these; the
    frame-count is derived per shot (config.py never fixes a film's length)."""

    model: str
    steps: int             # base LTX i2v: ~25-50 denoise steps (validated on the card)
    guidance_scale: float  # base LTX i2v sampling ~3.0 (the model card default)
    width: int             # must be divisible by 32 (LTX constraint), enforced in i2v_ltx.snap_dim
    height: int
    max_frames: int        # ceiling for this tier (snapped to 8k+1 by i2v_ltx.snap_frames)
    offload: Offload
    vae_tiling: bool       # decode the VAE in tiles to bound peak decode VRAM (the big 12GB saver)


# The honest 12GB ladder, VALIDATED on the shipped container under an 11GB VRAM cap
# (docs/proof/RESULTS.md): ALL THREE tiers pass, peak ~9.78GB reserved with model-cpu-offload + VAE
# tiling. Same base model per tier; the tiers differ by resolution + steps (speed vs fidelity). The
# heavier 13B path is a documented follow-up.
_TIERS: dict[QualityTier, TierConfig] = {
    # Fast preview: measured 48.6s/clip, peak 9.76GB reserved under the 11GB cap (docs/proof).
    QualityTier.DRAFT: TierConfig(
        model=LTX_BASE, steps=25, guidance_scale=3.0,
        width=512, height=320, max_frames=97, offload=Offload.MODEL_CPU_OFFLOAD, vae_tiling=True,
    ),
    # The comfortable middle: measured 132.0s/clip, peak 9.78GB reserved under the 11GB cap (docs/proof).
    QualityTier.STANDARD: TierConfig(
        model=LTX_BASE, steps=40, guidance_scale=3.0,
        width=704, height=512, max_frames=121, offload=Offload.MODEL_CPU_OFFLOAD, vae_tiling=True,
    ),
    # The card's HONEST ceiling: the base model at a higher resolution + more steps. Measured
    # 171.6s/clip, peak 9.78GB reserved under the 11GB cap (docs/proof). NOT datacenter parity; the
    # 13B path is the future quality tier.
    QualityTier.FINAL: TierConfig(
        model=LTX_BASE, steps=50, guidance_scale=3.0,
        width=768, height=512, max_frames=121, offload=Offload.MODEL_CPU_OFFLOAD, vae_tiling=True,
    ),
}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


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

    @classmethod
    def from_request(cls, cfg: dict, *, tier: QualityTier | None = None) -> "I2VConfig":
        """Build from the i2v_clip job's `config` dict. The tier baseline is the source of truth; the
        caller may narrow (never widen) it. width/height/num_frames default to the tier; an explicit
        value is clamped to the tier ceiling so a caller can never push the card past its honest fit.
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
        return cls(
            tier=t, model=base.model, steps=base.steps, guidance_scale=base.guidance_scale,
            width=width, height=height, num_frames=num_frames, fps=fps, seed=seed,
            flow_shift=flow_shift, offload=base.offload, vae_tiling=base.vae_tiling,
            negative_prompt=str(cfg.get("negative_prompt") or ""),
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
