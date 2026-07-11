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
# --------------------------------------------------------------------------- pipeline cache (process-lifetime)

def _fake_torch(cuda_available: bool, emptied: list | None = None):
    class _Cuda:
        @staticmethod
        def is_available():
            return cuda_available

        @staticmethod
        def empty_cache():
            if emptied is not None:
                emptied.append(True)

    class _Torch:
        bfloat16 = "bf16"
        cuda = _Cuda

    return _Torch


class _FakeCls:
    """A stand-in pipeline class: from_pretrained records each build and returns a bare object (so
    _apply_offload's best-effort hooks are all no-ops), no torch/diffusers needed."""

    def __init__(self):
        self.builds = []

    def from_pretrained(self, model, torch_dtype=None):
        self.builds.append((model, torch_dtype))
        return object()


def test_get_pipe_builds_once_and_reuses_per_key():
    i2v_ltx._PIPE_CACHE.clear()
    cfg = I2VConfig.from_request({"quality": "draft"}, tier=QualityTier.DRAFT)
    cls = _FakeCls()
    torch = _fake_torch(cuda_available=False)

    p1 = i2v_ltx._get_pipe(cfg, cls, torch)
    p2 = i2v_ltx._get_pipe(cfg, cls, torch)

    assert p1 is p2                  # the warm box reuses the resident pipe
    assert len(cls.builds) == 1      # from_pretrained ran exactly once (the ~30s weights read)
    i2v_ltx._PIPE_CACHE.clear()


def test_pipe_cache_key_separates_offload_and_tiling():
    import dataclasses

    from vivijure_local.config import Offload

    cfg = I2VConfig.from_request({"quality": "draft"}, tier=QualityTier.DRAFT)
    seq = dataclasses.replace(cfg, offload=Offload.SEQUENTIAL_CPU_OFFLOAD)
    no_tile = dataclasses.replace(cfg, vae_tiling=False)

    assert i2v_ltx._pipe_cache_key(cfg) != i2v_ltx._pipe_cache_key(seq)
    assert i2v_ltx._pipe_cache_key(cfg) != i2v_ltx._pipe_cache_key(no_tile)


def test_evict_pipe_drops_the_entry_and_frees_vram():
    i2v_ltx._PIPE_CACHE.clear()
    cfg = I2VConfig.from_request({"quality": "draft"}, tier=QualityTier.DRAFT)
    emptied: list = []
    torch = _fake_torch(cuda_available=True, emptied=emptied)

    i2v_ltx._get_pipe(cfg, _FakeCls(), torch)
    assert i2v_ltx._PIPE_CACHE                 # cached after a build

    i2v_ltx._evict_pipe(cfg, torch)
    assert not i2v_ltx._PIPE_CACHE             # entry gone -> next job rebuilds fresh
    assert emptied == [True]                   # VRAM freed explicitly, not left to GC timing


def test_evict_pipe_skips_empty_cache_when_no_cuda():
    i2v_ltx._PIPE_CACHE.clear()
    cfg = I2VConfig.from_request({"quality": "draft"}, tier=QualityTier.DRAFT)
    emptied: list = []
    torch = _fake_torch(cuda_available=False, emptied=emptied)

    i2v_ltx._evict_pipe(cfg, torch)            # CPU box: no CUDA to empty
    assert emptied == []


# --------------------------------------------------------------------------- engine dispatch (#1)

def _fake_diffusers(monkeypatch, *, cond_pipe=None, upsampler=None):
    """Inject a fake `diffusers` module so the deferred engine imports resolve on a CPU box (no torch).
    Exposes LTXConditionPipeline / LTXVideoCondition (+ LTXLatentUpsamplePipeline when given)."""
    import sys
    import types

    class _FakeCond:
        def __init__(self, image=None, frame_index=0, video=None, strength=1.0):
            self.image, self.frame_index = image, frame_index

    class _CondPipeCls:
        @staticmethod
        def from_pretrained(model, torch_dtype=None, **kw):
            return cond_pipe

    fake = types.ModuleType("diffusers")
    fake.LTXVideoCondition = _FakeCond
    fake.LTXConditionPipeline = _CondPipeCls
    if upsampler is not None:
        class _UpCls:
            @staticmethod
            def from_pretrained(model, vae=None, torch_dtype=None, **kw):
                upsampler.model, upsampler.vae = model, vae
                return upsampler
        fake.LTXLatentUpsamplePipeline = _UpCls
    monkeypatch.setitem(sys.modules, "diffusers", fake)
    return fake


class _RecordingPipe:
    """A callable stand-in pipeline: records each call's kwargs and returns an object whose `.frames`
    is a per-call sentinel list (so `.frames` as latents and `.frames[0]` as a clip both work)."""

    def __init__(self):
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        idx = len(self.calls) - 1

        class _R:
            frames = [f"frames-{idx}"]

        return _R()


def test_is_distilled_true_for_distilled_tiers_only():
    draft = I2VConfig.from_request({}, tier=QualityTier.DRAFT)
    std = I2VConfig.from_request({}, tier=QualityTier.STANDARD)
    final = I2VConfig.from_request({}, tier=QualityTier.FINAL)
    assert i2v_ltx.is_distilled(final) is True        # only final is a distilled variant (13B-distilled)
    assert i2v_ltx.is_distilled(draft) is False       # draft + standard are the base 2B i2v
    assert i2v_ltx.is_distilled(std) is False


def test_build_conditions_wraps_keyframe_as_a_frame0_condition(monkeypatch):
    _fake_diffusers(monkeypatch)
    conds = i2v_ltx._build_conditions("KEYFRAME")
    assert len(conds) == 1
    assert conds[0].image == "KEYFRAME"
    assert conds[0].frame_index == 0                 # the keyframe conditions frame 0, not `image=`


def test_run_base_drives_the_image_pipeline_with_image_kwarg():
    i2v_ltx._PIPE_CACHE.clear()
    cfg = I2VConfig.from_request({}, tier=QualityTier.STANDARD)   # BASE_I2V
    pipe = _RecordingPipe()

    class _Cls:
        @staticmethod
        def from_pretrained(model, torch_dtype=None):
            return pipe

    torch = _fake_torch(cuda_available=False)
    frames = i2v_ltx._run_base(cfg, "IMG", "a dolly in", 704, 512, 121, "GEN", None, torch, _Cls)
    assert frames == "frames-0"
    call = pipe.calls[0]
    assert call["image"] == "IMG" and "conditions" not in call   # base path uses image=, not conditions
    assert call["width"] == 704 and call["height"] == 512 and call["num_frames"] == 121
    i2v_ltx._PIPE_CACHE.clear()


def test_run_condition_drives_the_condition_pipeline_with_conditions_not_image(monkeypatch):
    i2v_ltx._PIPE_CACHE.clear()
    cfg = I2VConfig.from_request({}, tier=QualityTier.FINAL)      # CONDITION (13B), upsampler None
    pipe = _RecordingPipe()
    _fake_diffusers(monkeypatch, cond_pipe=pipe)
    torch = _fake_torch(cuda_available=False)

    frames = i2v_ltx._run_condition(cfg, "IMG", "a dolly in", 768, 512, 121, "GEN", None, torch)
    assert frames == "frames-0"
    call = pipe.calls[0]
    assert "conditions" in call and "image" not in call          # condition path, not image=
    assert call["conditions"][0].image == "IMG"                  # keyframe wrapped as the condition
    assert call["width"] == 768 and call["height"] == 512 and call["num_frames"] == 121
    i2v_ltx._PIPE_CACHE.clear()


def test_run_condition_upsampled_runs_generate_low_then_upscale(monkeypatch):
    import dataclasses

    i2v_ltx._PIPE_CACHE.clear()
    i2v_ltx._UPSAMPLER_CACHE.clear()
    base = I2VConfig.from_request({}, tier=QualityTier.FINAL)     # CONDITION
    cfg = dataclasses.replace(base, upsampler="Lightricks/ltxv-spatial-upscaler-0.9.7")
    pipe = _RecordingPipe()
    up = _RecordingPipe()
    _fake_diffusers(monkeypatch, cond_pipe=pipe, upsampler=up)
    torch = _fake_torch(cuda_available=False)

    frames = i2v_ltx._run_condition(cfg, "IMG", "a dolly in", 768, 512, 121, "GEN", None, torch)

    # Stage 1: low-res latent generate (output_type latent, downscaled dims).
    assert pipe.calls[0]["output_type"] == "latent"
    assert pipe.calls[0]["width"] < 768 and pipe.calls[0]["height"] < 512
    low_w, low_h = pipe.calls[0]["width"], pipe.calls[0]["height"]
    # Stage 2: the upsampler takes the stage-1 latents.
    assert up.calls[0]["latents"] == ["frames-0"] and up.calls[0]["output_type"] == "latent"
    # Stage 3: refine + decode at 2x the downscaled resolution, feeding the upscaled latents.
    assert pipe.calls[1]["output_type"] == "pil"
    assert "latents" in pipe.calls[1] and "denoise_strength" in pipe.calls[1]
    assert pipe.calls[1]["width"] == i2v_ltx.snap_dim(low_w * 2)
    assert pipe.calls[1]["height"] == i2v_ltx.snap_dim(low_h * 2)
    assert frames == "frames-1"                                   # the decoded clip is the refine pass output
    i2v_ltx._PIPE_CACHE.clear()
    i2v_ltx._UPSAMPLER_CACHE.clear()


def test_pipe_cache_key_separates_engine_class():
    import dataclasses

    from vivijure_local.config import Engine

    cfg = I2VConfig.from_request({}, tier=QualityTier.STANDARD)   # BASE_I2V
    cond = dataclasses.replace(cfg, engine=Engine.CONDITION)
    # Same model + offload but a different engine class must not share a cached pipe.
    assert i2v_ltx._pipe_cache_key(cfg) != i2v_ltx._pipe_cache_key(cond)


def test_evict_pipe_also_drops_the_upsampler_entry():
    import dataclasses

    i2v_ltx._PIPE_CACHE.clear()
    i2v_ltx._UPSAMPLER_CACHE.clear()
    base = I2VConfig.from_request({}, tier=QualityTier.FINAL)
    cfg = dataclasses.replace(base, upsampler="Lightricks/ltxv-spatial-upscaler-0.9.7")
    i2v_ltx._PIPE_CACHE[i2v_ltx._pipe_cache_key(cfg)] = object()
    i2v_ltx._UPSAMPLER_CACHE[(cfg.upsampler, cfg.offload.value)] = object()
    torch = _fake_torch(cuda_available=False)

    i2v_ltx._evict_pipe(cfg, torch)
    assert not i2v_ltx._PIPE_CACHE and not i2v_ltx._UPSAMPLER_CACHE   # both cleared on a poisoned render
