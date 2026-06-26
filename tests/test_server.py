"""The pure RunPod-compatible router + the i2v run_fn wired to a fake store (no GPU, no sockets)."""
import time
from pathlib import Path

from vivijure_local.jobs import JobRegistry, JobStatus
from vivijure_local.server import authorized, build_i2v_run_fn, route


def _reg():
    # A registry whose worker just echoes a pointer-result, so route() tests need no GPU.
    return JobRegistry(lambda payload, should_cancel: {"clip_key": "renders/p/clips/s_i2v.mp4", "shot_id": "s", "fps": 24, "num_frames": 121})


def test_health_needs_no_auth():
    code, body = route("GET", "/health", None, registry=_reg(), token=None, expected_token="secret")
    assert code == 200 and body["ok"] is True and body["engine"] == "ltx-video"


def test_auth_enforced_when_a_token_is_configured():
    assert authorized(None, "") is True          # open when no token configured
    assert authorized("secret", "secret") is True
    assert authorized("wrong", "secret") is False
    code, body = route("POST", "/run", {"input": {"action": "i2v_clip", "prompt": "p"}},
                       registry=_reg(), token="wrong", expected_token="secret")
    assert code == 401 and body["ok"] is False


def test_run_submits_a_job_and_returns_an_id():
    reg = _reg()
    code, body = route("POST", "/run",
                       {"input": {"action": "i2v_clip", "project": "p", "shot_id": "s", "prompt": "dolly in"}},
                       registry=reg, token=None, expected_token="")
    assert code == 200 and isinstance(body["id"], str)
    reg.shutdown()


def test_run_rejects_a_missing_prompt_as_a_400_not_a_silent_accept():
    code, body = route("POST", "/run", {"input": {"action": "i2v_clip", "project": "p", "shot_id": "s"}},
                       registry=_reg(), token=None, expected_token="")
    assert code == 400 and "prompt is required" in body["error"]


def test_run_rejects_an_unsupported_action():
    code, body = route("POST", "/run", {"input": {"action": "render"}},
                       registry=_reg(), token=None, expected_token="")
    assert code == 400 and "unsupported action" in body["error"]


def test_selftest_is_a_no_gpu_probe():
    code, body = route("POST", "/run", {"selftest": True}, registry=_reg(), token=None, expected_token="")
    assert code == 200 and body["selftest"] is True


def test_status_unknown_id_is_a_404_envelope_for_the_grace_path():
    code, body = route("GET", "/status/deadbeef", None, registry=_reg(), token=None, expected_token="")
    assert code == 404 and body["status"] == 404  # the shape the module's jobGone() detects (#141)


def test_status_reports_a_completed_job_with_its_pointer_output():
    reg = _reg()
    _, run_body = route("POST", "/run", {"input": {"action": "i2v_clip", "project": "p", "shot_id": "s", "prompt": "x"}},
                        registry=reg, token=None, expected_token="")
    jid = run_body["id"]
    deadline = time.time() + 3.0
    while time.time() < deadline:
        code, body = route("GET", f"/status/{jid}", None, registry=reg, token=None, expected_token="")
        if body.get("status") == "COMPLETED":
            break
        time.sleep(0.01)
    assert body["status"] == "COMPLETED" and body["output"]["clip_key"].endswith("_i2v.mp4")
    reg.shutdown()


def test_cancel_route_is_idempotent_ok():
    code, body = route("POST", "/cancel/whatever", None, registry=_reg(), token=None, expected_token="")
    assert code == 200 and body["ok"] is True


def test_i2v_run_fn_fetches_keyframe_animates_and_uploads_pointer(monkeypatch, tmp_path):
    # Fake store records calls; fake the engine so no torch is needed.
    calls = {}

    class FakeStore:
        def get_file(self, key, dest):
            calls["get"] = key
            Path(dest).write_bytes(b"png")
            return dest

        def put_file(self, src, key, content_type=None):
            calls["put"] = key
            return key

    import vivijure_local.i2v_ltx as eng
    from vivijure_local.i2v_ltx import I2VResult

    def fake_animate(shot_id, keyframe, prompt, cfg, out_path, *, progress_cb=None):
        Path(out_path).write_bytes(b"mp4")
        if progress_cb:
            progress_cb(1, cfg.steps)  # exercises the should_cancel hook path
        return I2VResult(shot_id=shot_id, path=Path(out_path), num_frames=121, fps=24, seconds=5.04, distilled=True)

    monkeypatch.setattr(eng, "animate", fake_animate)

    run = build_i2v_run_fn(FakeStore(), workdir=tmp_path)
    out = run({"action": "i2v_clip", "project": "My Film", "shot_id": "shot_02", "prompt": "slow dolly in",
               "config": {"quality": "draft"}}, lambda: False)

    assert calls["get"] == "renders/My_Film/keyframes/shot_02.png"  # the shared key convention
    assert calls["put"] == "renders/My_Film/clips/shot_02_i2v.mp4"
    assert out == {"clip_key": "renders/My_Film/clips/shot_02_i2v.mp4", "shot_id": "shot_02",
                   "fps": 24, "num_frames": 121, "seconds": 5.04, "distilled": True}
