"""The honest 12GB tier->engine mapping (no GPU)."""
import pytest

from vivijure_local.config import (
    OFFLOAD_ENV,
    Engine,
    I2VConfig,
    LTX_13B_DISTILLED,
    LTX_BASE,
    Offload,
    QualityTier,
    offload_override,
    parse_offload_override,
    tier_config,
)


def test_tier_parse_is_lenient_and_defaults_to_standard():
    assert QualityTier.parse("draft") is QualityTier.DRAFT
    assert QualityTier.parse("FINAL") is QualityTier.FINAL
    assert QualityTier.parse("nonsense") is QualityTier.STANDARD
    assert QualityTier.parse(None) is QualityTier.STANDARD


def test_the_three_tiers_map_to_distinct_honest_engine_configs():
    draft, std, final = (tier_config(t) for t in (QualityTier.DRAFT, QualityTier.STANDARD, QualityTier.FINAL))
    # draft + standard stay the PROVEN base 2B i2v (the safe default engine); final is the 13B-distilled
    # quality ceiling on the CONDITION pipeline (#1, docs/proof/BENCH-13B.md).
    assert draft.engine is Engine.BASE_I2V and draft.model == LTX_BASE
    assert std.engine is Engine.BASE_I2V and std.model == LTX_BASE
    assert final.engine is Engine.CONDITION and final.model == LTX_13B_DISTILLED
    # draft is the lightest/fastest; final is the card's honest quality ceiling (higher res).
    assert draft.width <= std.width <= final.width
    # base tiers run the full step ladder with CFG; the distilled 13B runs few-step, CFG-free.
    assert draft.steps < std.steps
    assert final.steps < std.steps and final.guidance_scale == 1.0
    assert draft.guidance_scale > 1.0 and std.guidance_scale > 1.0
    # 13B is far larger than base 2B, so final pages per-layer (sequential); base tiers fit at
    # model-cpu-offload. Tiling everywhere (the big 12GB saver).
    assert final.offload is Offload.SEQUENTIAL_CPU_OFFLOAD
    assert draft.offload is Offload.MODEL_CPU_OFFLOAD and std.offload is Offload.MODEL_CPU_OFFLOAD
    assert all(t.vae_tiling for t in (draft, std, final))
    # No tier ships the optional upsampler enabled (wired + unit-tested; pending a live upsampler bench).
    assert all(t.upsampler is None for t in (draft, std, final))


def test_from_request_uses_the_tier_baseline_including_engine():
    cfg = I2VConfig.from_request({"quality": "final"})
    base = tier_config(QualityTier.FINAL)
    assert cfg.tier is QualityTier.FINAL
    assert cfg.model == base.model
    assert cfg.steps == base.steps
    assert cfg.width == base.width and cfg.height == base.height
    # engine + upsampler are FIXED by the tier (the proven fit), carried onto the per-shot config.
    assert cfg.engine is base.engine and cfg.upsampler is base.upsampler


def test_engine_and_upsampler_are_not_caller_overridable():
    # A request cannot widen the engine/upsampler past the tier's proven fit (they are not knobs).
    cfg = I2VConfig.from_request(
        {"quality": "standard", "engine": "condition", "upsampler": "Lightricks/ltxv-spatial-upscaler-0.9.7"}
    )
    base = tier_config(QualityTier.STANDARD)
    assert cfg.engine is base.engine is Engine.BASE_I2V
    assert cfg.upsampler is None


def test_caller_can_narrow_but_never_widen_past_the_honest_ceiling():
    base = tier_config(QualityTier.STANDARD)
    # Ask for a huge resolution + frame count: clamped DOWN to the tier ceiling (the card's honest fit).
    cfg = I2VConfig.from_request(
        {"quality": "standard", "width": 4096, "height": 4096, "num_frames": 9999}
    )
    assert cfg.width == base.width and cfg.height == base.height
    assert cfg.num_frames == base.max_frames
    # A smaller request is honored (narrowing is fine).
    smaller = I2VConfig.from_request({"quality": "standard", "num_frames": 49})
    assert smaller.num_frames == 49


def test_fps_clamped_to_8_30_and_seed_and_negative_pass_through():
    assert I2VConfig.from_request({"fps": 999}).fps == 30
    assert I2VConfig.from_request({"fps": 1}).fps == 8
    assert I2VConfig.from_request({"seed": 42}).seed == 42
    assert I2VConfig.from_request({"seed": -1}).seed == -1
    assert I2VConfig.from_request({"negative_prompt": "blurry"}).negative_prompt == "blurry"


def test_bad_numeric_values_fall_back_to_defaults_not_crash():
    cfg = I2VConfig.from_request({"fps": "abc", "seed": None, "num_frames": "x", "flow_shift": True})
    assert cfg.fps == 24 and cfg.seed == -1 and cfg.flow_shift == 5.0


# --- VIVIJURE_OFFLOAD operator override (12gb#91) ---------------------------------------------------

def test_parse_offload_override_unset_or_blank_is_none():
    # Unset / blank / whitespace all mean "no override" -> each tier keeps its hardcoded default.
    assert parse_offload_override(None) is None
    assert parse_offload_override("") is None
    assert parse_offload_override("   ") is None


def test_parse_offload_override_maps_each_valid_mode():
    assert parse_offload_override("none") is Offload.NONE
    assert parse_offload_override("model") is Offload.MODEL_CPU_OFFLOAD
    assert parse_offload_override("sequential") is Offload.SEQUENTIAL_CPU_OFFLOAD
    # case-insensitive + surrounding whitespace tolerated (operator ergonomics)
    assert parse_offload_override("  NONE  ") is Offload.NONE
    assert parse_offload_override("Model") is Offload.MODEL_CPU_OFFLOAD


def test_parse_offload_override_invalid_raises_loud():
    # A fat-fingered value must FAIL LOUD, never silently default (the honesty rule).
    with pytest.raises(ValueError) as ei:
        parse_offload_override("resident")
    msg = str(ei.value)
    assert "VIVIJURE_OFFLOAD" in msg
    assert "none" in msg and "model" in msg and "sequential" in msg  # lists the valid modes


def test_offload_override_reads_the_env(monkeypatch):
    monkeypatch.delenv(OFFLOAD_ENV, raising=False)
    assert offload_override() is None
    monkeypatch.setenv(OFFLOAD_ENV, "none")
    assert offload_override() is Offload.NONE


def test_from_request_keeps_tier_default_when_unset(monkeypatch):
    # Byte-for-byte: with no override, every tier resolves to its hardcoded offload.
    monkeypatch.delenv(OFFLOAD_ENV, raising=False)
    for t in (QualityTier.DRAFT, QualityTier.STANDARD, QualityTier.FINAL):
        cfg = I2VConfig.from_request({"quality": t.value})
        assert cfg.offload is tier_config(t).offload


def test_from_request_applies_override_to_every_tier(monkeypatch):
    # A set override replaces the per-tier default for ALL tiers (the big-VRAM operator opt-in).
    monkeypatch.setenv(OFFLOAD_ENV, "none")
    for t in (QualityTier.DRAFT, QualityTier.STANDARD, QualityTier.FINAL):
        assert I2VConfig.from_request({"quality": t.value}).offload is Offload.NONE
    monkeypatch.setenv(OFFLOAD_ENV, "sequential")
    assert I2VConfig.from_request({"quality": "draft"}).offload is Offload.SEQUENTIAL_CPU_OFFLOAD
