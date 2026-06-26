"""The pure VRAM budgeter (no torch, no CUDA)."""
from vivijure_local.config import I2VConfig, Offload, QualityTier
from vivijure_local import vram


def _cfg(tier: QualityTier) -> I2VConfig:
    return I2VConfig.from_request({"quality": tier.value}, tier=tier)


def test_all_three_tiers_are_estimated_to_fit_the_16gb_floor():
    for tier in (QualityTier.DRAFT, QualityTier.STANDARD, QualityTier.FINAL):
        est = vram.estimate(_cfg(tier))
        assert est.fits, f"{tier} predicted not to fit 16GB: {est}"
        assert est.peak_gb <= est.budget_gb


def test_stronger_offload_lowers_the_resident_weight_cost():
    from dataclasses import replace

    base = _cfg(QualityTier.FINAL)
    none = vram.estimate(replace(base, offload=Offload.NONE))
    model = vram.estimate(replace(base, offload=Offload.MODEL_CPU_OFFLOAD))
    seq = vram.estimate(replace(base, offload=Offload.SEQUENTIAL_CPU_OFFLOAD))
    assert none.weights_gb > model.weights_gb > seq.weights_gb


def test_vae_tiling_discounts_the_activation_peak():
    from dataclasses import replace

    cfg = _cfg(QualityTier.STANDARD)
    tiled = vram.activations_gb(cfg)
    untiled = vram.activations_gb(replace(cfg, vae_tiling=False))
    assert tiled < untiled


def test_strongest_offload_picks_the_weakest_that_fits():
    # The 13B-fp8 final config is too heavy resident; the budgeter should not pick NONE for it.
    final = _cfg(QualityTier.FINAL)
    chosen = vram.strongest_offload(final)
    assert chosen in (Offload.MODEL_CPU_OFFLOAD, Offload.SEQUENTIAL_CPU_OFFLOAD)
    # The light 2B draft config fits resident (NONE), so the budgeter need not page it at all.
    draft = _cfg(QualityTier.DRAFT)
    assert vram.strongest_offload(draft) is Offload.NONE


def test_a_24gb_card_has_more_headroom_than_the_16gb_floor():
    cfg = _cfg(QualityTier.FINAL)
    floor = vram.estimate(cfg, card_gb=16.0)
    big = vram.estimate(cfg, card_gb=24.0)
    assert big.headroom_gb > floor.headroom_gb
