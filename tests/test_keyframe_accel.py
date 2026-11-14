"""CPU tests for the Hyper-SD capability field (local-12gb#129). diffusers/torch are mocked."""
from __future__ import annotations

import re
import sys
import types
from pathlib import Path

import pytest

from vivijure_local import preview_sdxl
from vivijure_local.core.server import route

ROOT = Path(__file__).resolve().parent.parent


class _FakePipe:
    fail = False

    def load_lora_weights(self, *a, **k):
        if _FakePipe.fail:
            raise ValueError("PEFT backend is required for this method.")

    def set_adapters(self, *a, **k):
        pass

    def enable_model_cpu_offload(self):
        pass

    def to(self, *a):
        pass


@pytest.fixture
def fake_stack(monkeypatch):
    torch = types.ModuleType("torch")
    torch.bfloat16 = torch.float32 = "dt"
    torch.cuda = types.SimpleNamespace(is_available=lambda: False, empty_cache=lambda: None)
    diff = types.ModuleType("diffusers")
    diff.StableDiffusionXLPipeline = types.SimpleNamespace(from_pretrained=lambda *a, **k: _FakePipe())
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "diffusers", diff)
    preview_sdxl.unload_preview()
    _FakePipe.fail = False
    yield
    preview_sdxl.unload_preview()


def _health():
    reg = None
    code, body = route("GET", "/health", None, registry=reg, token=None, expected_token="t")
    assert code == 200
    return body


def test_health_unknown_before_any_few_step_pipe(fake_stack):
    assert _health()["keyframe_accel"] == "unknown"


def test_health_reports_full_step_when_lora_load_raises(fake_stack):
    _FakePipe.fail = True
    preview_sdxl._get_pipe(few_step=True)  # must NOT raise: the fallback is kept
    assert _health()["keyframe_accel"] == "full-step"


def test_health_reports_hyper_sd_when_lora_loads(fake_stack):
    preview_sdxl._get_pipe(few_step=True)
    assert _health()["keyframe_accel"] == "hyper-sd"


def test_unload_resets_state(fake_stack):
    _FakePipe.fail = True
    preview_sdxl._get_pipe(few_step=True)
    preview_sdxl.unload_preview()
    assert _health()["keyframe_accel"] == "unknown"


def test_fallback_log_is_loud(fake_stack, capsys):
    _FakePipe.fail = True
    preview_sdxl._get_pipe(few_step=True)
    out = capsys.readouterr().out
    assert "WARNING Hyper-SD unavailable" in out and "FULL-STEP" in out


def _distill_weight(pipe):
    """Weight of the distill adapter after the last set_adapters call (None if never set)."""
    w = None
    for names, weights in pipe.adapter_calls:
        if "distill" in names:
            w = weights[names.index("distill")]
    return w


class _RecPipe(_FakePipe):
    def __init__(self):
        self.loaded = []
        self.adapter_calls = []

    def load_lora_weights(self, *a, **k):
        super().load_lora_weights(*a, **k)
        self.loaded.append(k.get("adapter_name"))

    def set_adapters(self, names, adapter_weights=None, **k):
        self.adapter_calls.append((list(names), list(adapter_weights or [])))


@pytest.fixture
def rec_stack(fake_stack, monkeypatch):
    sys.modules["diffusers"].StableDiffusionXLPipeline = types.SimpleNamespace(
        from_pretrained=lambda *a, **k: _RecPipe()
    )
    yield


def test_final_first_then_draft_loads_distill_and_reports_hyper_sd(rec_stack):
    # local-12gb#190: a first final job must not strand later draft/standard jobs without Hyper-SD.
    pipe = preview_sdxl._get_pipe(few_step=False)
    assert pipe.loaded == ["distill"]
    assert _distill_weight(pipe) == 0.0  # final: adapter present but inert
    assert _health()["keyframe_accel"] == "hyper-sd"
    again = preview_sdxl._get_pipe(few_step=True)
    assert again is pipe  # one pipe resident, not two (12GB card)
    assert _distill_weight(pipe) == 1.0
    assert pipe.loaded == ["distill"]  # loaded once


def test_draft_first_then_final_runs_without_distill_active(rec_stack):
    pipe = preview_sdxl._get_pipe(few_step=True)
    assert _distill_weight(pipe) == 1.0
    assert _health()["keyframe_accel"] == "hyper-sd"
    assert preview_sdxl._get_pipe(few_step=False) is pipe
    assert _distill_weight(pipe) == 0.0
    assert preview_sdxl._get_pipe(few_step=True) is pipe
    assert _distill_weight(pipe) == 1.0


def test_char_lora_bind_keeps_distill_at_the_job_weight(rec_stack, tmp_path):
    pipe = preview_sdxl._get_pipe(few_step=False)
    f = tmp_path / "c.safetensors"
    f.write_bytes(b"x")
    preview_sdxl._bind_pretrained_loras(pipe, {"a": f})
    names, weights = pipe.adapter_calls[-1]
    assert names == ["distill", "char_a"] and weights == [0.0, 0.6]


def test_lora_failure_on_final_first_build_falls_back_with_warning(rec_stack, capsys):
    _FakePipe.fail = True
    pipe = preview_sdxl._get_pipe(few_step=False)  # must NOT raise
    assert _health()["keyframe_accel"] == "full-step"
    assert "WARNING Hyper-SD unavailable" in capsys.readouterr().out
    assert preview_sdxl._get_pipe(few_step=True) is pipe
    assert pipe.adapter_calls == []  # nothing to switch


def _pin(text: str) -> str:
    m = re.search(r"peft==([0-9][0-9A-Za-z.]*)", text)
    assert m, "no peft== pin found"
    return m.group(1)


def test_dockerfile_and_requirements_pin_the_same_peft():
    # v1.0.4 shipped with NO peft; main pins it in two places that must not drift.
    assert _pin((ROOT / "requirements.txt").read_text()) == _pin((ROOT / "deploy" / "Dockerfile").read_text())


def test_peft_pin_meets_diffusers_floor():
    # diffusers 0.38.0 utils/constants.py MIN_PEFT_VERSION = "0.6.0" gates USE_PEFT_BACKEND.
    v = tuple(int(x) for x in _pin((ROOT / "requirements.txt").read_text()).split(".")[:2])
    assert v >= (0, 6)
