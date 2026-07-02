"""Pure frame/dimension math for the LTX engine (no torch, no GPU)."""
from vivijure_local import i2v_ltx
from vivijure_local.config import I2VConfig, QualityTier


def test_snap_frames_rounds_up_to_8k_plus_1():
    assert i2v_ltx.snap_frames(1) == 1            # 8*0+1
    assert i2v_ltx.snap_frames(9) == 9            # 8*1+1 exact
    assert i2v_ltx.snap_frames(10) == 17          # round up to 8*2+1
    assert i2v_ltx.snap_frames(120) == 121        # 8*15+1
    assert (i2v_ltx.snap_frames(120) - 1) % i2v_ltx.TEMPORAL_STRIDE == 0


def test_snap_frames_clamps_below_the_ceiling_and_stays_valid():
    n = i2v_ltx.snap_frames(10_000)
    assert n <= i2v_ltx.MAX_FRAMES
    assert (n - 1) % i2v_ltx.TEMPORAL_STRIDE == 0  # still 8k+1 after the clamp


def test_snap_dim_rounds_down_to_multiple_of_32_with_a_floor():
    assert i2v_ltx.snap_dim(704) == 704
    assert i2v_ltx.snap_dim(700) == 672           # rounds DOWN (a clamped ceiling stays a ceiling)
    assert i2v_ltx.snap_dim(10) == 32             # floor


def test_frames_for_derives_from_seconds_and_caps():
    assert i2v_ltx.frames_for(5, 24) == 121       # 120 -> 8*15+1
    assert i2v_ltx.frames_for(None, 24) <= i2v_ltx.MAX_FRAMES
    assert i2v_ltx.frames_for(0, 24) <= i2v_ltx.MAX_FRAMES


def test_clip_seconds_is_frames_over_fps():
    assert i2v_ltx.clip_seconds(121, 24) == round(121 / 24, 3)


def test_resolve_engine_dims_snaps_both_axes_and_frames():
    cfg = I2VConfig.from_request({"quality": "standard"}, tier=QualityTier.STANDARD)
    w, h, n = i2v_ltx.resolve_engine_dims(cfg)
    assert w % 32 == 0 and h % 32 == 0
    assert (n - 1) % i2v_ltx.TEMPORAL_STRIDE == 0


def test_animate_raises_without_torch_rather_than_faking_output():
    # A producer stage never fakes a clip; with no torch/diffusers present the body must raise.
    import pytest

    cfg = I2VConfig.from_request({"quality": "draft"}, tier=QualityTier.DRAFT)
    try:
        import torch  # noqa: F401
        import diffusers  # noqa: F401
    except Exception:
        with pytest.raises(RuntimeError):
            i2v_ltx.animate("shot_01", __file__, "a slow dolly in", cfg, "/tmp/none.mp4")
    else:
        pytest.skip("torch+diffusers present; the deferred-import guard cannot be exercised here")


# --------------------------------------------------------------------------- offload-failure logging

def test_try_returns_true_on_clean_hook_and_false_on_absent():
    calls = []

    class Obj:
        def hook(self, *a):
            calls.append(a)

    assert i2v_ltx._try(Obj(), "hook") is True
    assert calls == [()]
    assert i2v_ltx._try(Obj(), "nope") is False   # absent hook -> quietly False


def test_try_logs_loudly_and_returns_false_when_a_present_hook_raises(capsys):
    class Boom:
        def hook(self):
            raise RuntimeError("no cuda here")

    assert i2v_ltx._try(Boom(), "hook") is False
    err = capsys.readouterr().err
    assert "hook" in err and "VRAM" in err        # the swallowed failure is now surfaced


def test_apply_offload_warns_when_the_strategy_does_not_apply(capsys):
    cfg = I2VConfig.from_request({"quality": "draft"}, tier=QualityTier.DRAFT)

    class BarePipe:  # no offload hooks at all (a wrong/old diffusers build)
        pass

    i2v_ltx._apply_offload(BarePipe(), cfg)
    err = capsys.readouterr().err
    assert "did not apply" in err                 # offload is the fit; silence would mask an OOM
