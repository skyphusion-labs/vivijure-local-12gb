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
