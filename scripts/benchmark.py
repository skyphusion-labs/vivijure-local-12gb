#!/usr/bin/env python3
"""The PROOF GATE: a live LTX-Video i2v benchmark on real hardware, under a HARD VRAM allocator cap.

Drives the engine (`i2v_ltx.animate`) directly -- no R2, no job server -- so the benchmark measures the
MODEL on the CARD, nothing else. It captures, per quality tier (draft / standard / final):
  - FIT:   peak VRAM (`torch.cuda.max_memory_allocated` + `max_memory_reserved`) and whether it holds
           under the cap. With `--cap-gb`, an overflow raises CUDA OOM exactly as a real card of that
           size would (see THE CAP below), so a tier that does not fit FAILS honestly instead of quietly
           spilling into spare VRAM the target card would not have.
  - SPEED: wall-clock seconds per clip at the tier ceiling. With `--warm`, a warm (pipeline-resident)
           clip is timed separately from the one-time cold model load.
  - QUALITY: a real sample .mp4 per tier to eyeball.
Writes <out>/benchmark-<host>.md + <out>/benchmark-<host>.json + the sample clips.

THE CAP (`--cap-gb N`). torch's `set_per_process_memory_fraction(N / total)` bounds THIS process's
allocator to N GB of the real device total (read from the card, printed in the report). On the free
verify lane (propagandhi, RTX 4000 SFF Ada, ~20GB) `--cap-gb 12` => fraction 12/20 = 0.60, so the
allocator may claim at most 12GB and an over-budget pipeline OOMs like a 12GB card. HONEST CAVEAT: a
real 12GB card also loses ~0.5-1GB to the CUDA context (which lives OUTSIDE the per-process fraction),
so a 12GB allocator cap is the OPTIMISTIC edge of a real 12GB card's usable budget; the earlier proof
(docs/proof/RESULTS.md) used an 11GB cap for exactly that headroom. A flat "runs on 12GB" claim for a
new tier therefore waits on a true-12GB-card confirmation run; this harness proves fit under a hard 12GB
ALLOCATOR cap, which is what the docs say.

Usage (on the GPU box, after `pip install -r requirements.txt`):
  python scripts/benchmark.py --cap-gb 12 --warm --out docs/proof/bench-12gb
  python scripts/benchmark.py --cap-gb 12 --tiers draft,final --out docs/proof/bench-12gb
  # no --keyframe: a synthetic test keyframe is generated so the run is self-contained.
"""
from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from pathlib import Path

# Make `vivijure_local` importable when run from the repo root (src/ layout).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from vivijure_local.config import I2VConfig, QualityTier  # noqa: E402
from vivijure_local import i2v_ltx  # noqa: E402
from vivijure_local.core import vram  # noqa: E402


def synth_keyframe(path: Path, width: int = 768, height: int = 512) -> Path:
    """Write a synthetic keyframe so the benchmark is self-contained when no real one is given."""
    from PIL import Image, ImageDraw  # Pillow ships in the runtime image

    img = Image.new("RGB", (width, height), (18, 22, 30))
    d = ImageDraw.Draw(img)
    for y in range(height):  # a vertical gradient so i2v has real structure to move
        d.line([(0, y), (width, y)], fill=(20 + y % 60, 30 + (y * 2) % 80, 60 + (y * 3) % 120))
    d.ellipse([width * 0.35, height * 0.3, width * 0.65, height * 0.7], fill=(220, 180, 90))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path


def gpu_name() -> str:
    try:
        import torch

        return torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu (no CUDA)"
    except Exception:
        return "unknown (torch unavailable)"


def apply_cap(cap_gb: float | None) -> dict:
    """Pin this process to `cap_gb` of the REAL device total via the pure vram.* math (the same helper
    the server uses), BEFORE any model loads. Returns the cap facts for the report (or an empty dict
    when uncapped)."""
    if not cap_gb or cap_gb <= 0:
        return {}
    import torch

    device_index = torch.cuda.current_device()
    total_gb = torch.cuda.get_device_properties(device_index).total_memory / (1024 ** 3)
    fraction = vram.vram_fraction(cap_gb, total_gb)
    if fraction is None:
        return {}
    torch.cuda.set_per_process_memory_fraction(fraction, device_index)
    facts = {"cap_gb": cap_gb, "applied_fraction": round(fraction, 4), "device_total_gb": round(total_gb, 2)}
    print(f"VRAM capped to {cap_gb}GB ({fraction:.4f} of {total_gb:.1f}GB) -- overflow OOMs like a {cap_gb:.0f}GB card",
          flush=True)
    return facts


def run_tier(tier: QualityTier, keyframe: Path, out_dir: Path, prompt: str, *, cap_gb: float | None, warm: bool) -> dict:
    """Benchmark one tier: build the cfg, (optionally) warm the pipeline, then time ONE clip with the
    CUDA peak counter reset immediately before it -- so the reported peak is the steady-state render
    peak, not the one-time weights load. Captures peak alloc + reserved, OOM, sec/clip, cold load, and a
    sample clip."""
    import torch

    cfg = I2VConfig.from_request({"quality": tier.value}, tier=tier)
    w, h, n = i2v_ltx.resolve_engine_dims(cfg)
    record: dict = {
        "tier": tier.value, "model": cfg.model, "engine": cfg.engine.value, "width": w, "height": h,
        "frames": n, "steps": cfg.steps, "offload": cfg.offload.value, "vae_tiling": cfg.vae_tiling,
        "upsampler": cfg.upsampler, "distilled": i2v_ltx.is_distilled(cfg),
    }
    out_clip = out_dir / f"sample_{tier.value}.mp4"
    ceiling = cap_gb if cap_gb else 16.0

    cold_seconds: float | None = None
    t0: float | None = None
    try:
        if warm:
            # Cold pass: pays the one-time model load; times the loaded-plus-render wall so cold load is
            # reported honestly, then the timed pass below measures a warm (resident-pipeline) clip.
            t_cold = time.time()
            i2v_ltx.animate(f"bench_{tier.value}_cold", keyframe, prompt, cfg, out_dir / f"_warmup_{tier.value}.mp4")
            cold_seconds = round(time.time() - t_cold, 1)

        torch.cuda.reset_peak_memory_stats()
        t0 = time.time()
        result = i2v_ltx.animate(f"bench_{tier.value}", keyframe, prompt, cfg, out_clip)
        elapsed = round(time.time() - t0, 1)
        peak_alloc = round(torch.cuda.max_memory_allocated() / 1e9, 2)
        peak_reserved = round(torch.cuda.max_memory_reserved() / 1e9, 2)
        record.update({
            "ok": True, "oom": False, "seconds_per_clip": elapsed, "cold_load_seconds": cold_seconds,
            "measured_peak_alloc_gb": peak_alloc, "measured_peak_reserved_gb": peak_reserved,
            "fit_ceiling_gb": ceiling, "fits_cap": peak_reserved <= ceiling,
            "clip_seconds": result.seconds, "sample": str(out_clip),
        })
    except RuntimeError as e:
        elapsed = round(time.time() - t0, 1) if t0 is not None else None
        msg = str(e)
        oom = "out of memory" in msg.lower() or "CUDA out of memory" in msg
        record.update({"ok": False, "oom": oom, "seconds_per_clip": elapsed, "cold_load_seconds": cold_seconds,
                       "fit_ceiling_gb": ceiling, "fits_cap": False, "error": msg[:300]})
    finally:
        # Drop this tier's resident pipeline before the next tier so the cap is measured per tier, not
        # against a card already holding the previous model.
        try:
            i2v_ltx._evict_pipe(cfg, torch)
        except Exception:
            pass
    return record


def main() -> int:
    ap = argparse.ArgumentParser(description="LTX-Video i2v benchmark under a hard VRAM allocator cap (the proof gate)")
    ap.add_argument("--keyframe", type=Path, default=None, help="real keyframe PNG (else synthesized)")
    ap.add_argument("--out", type=Path, default=Path("results"), help="output dir for clips + report")
    ap.add_argument("--tiers", default="draft,standard,final", help="comma list of tiers to run")
    ap.add_argument("--cap-gb", type=float, default=None, help="hard per-process VRAM allocator cap in GB (e.g. 12)")
    ap.add_argument("--warm", action="store_true", help="time a warm clip separately from the cold model load")
    ap.add_argument("--prompt", default="a slow, smooth cinematic dolly-in, gentle parallax", help="motion prompt")
    args = ap.parse_args()

    try:
        import torch  # noqa: F401
    except Exception:
        print("ERROR: torch/diffusers not installed -- this is the GPU proof gate, run it on the card "
              "(pip install -r requirements.txt). Pure helpers are covered by the CPU test suite.",
              file=sys.stderr)
        return 2

    import torch
    if not torch.cuda.is_available():
        print("ERROR: no CUDA device visible. Run on the GPU box (check the NVIDIA Container Toolkit "
              "/ --gpus all if in Docker).", file=sys.stderr)
        return 2

    cap_facts = apply_cap(args.cap_gb)

    args.out.mkdir(parents=True, exist_ok=True)
    keyframe = args.keyframe or synth_keyframe(args.out / "keyframe.png")
    tiers = [QualityTier.parse(t) for t in args.tiers.split(",") if t.strip()]

    host = platform.node() or "host"
    meta = {"host": host, "gpu": gpu_name(), "keyframe": str(keyframe), "prompt": args.prompt, "warm": args.warm}
    meta.update(cap_facts)
    print(f"Benchmarking on {meta['gpu']} (host {host}); keyframe={keyframe}", flush=True)

    results = []
    for tier in tiers:
        print(f"\n--- tier {tier.value} ---", flush=True)
        rec = run_tier(tier, keyframe, args.out, args.prompt, cap_gb=args.cap_gb, warm=args.warm)
        results.append(rec)
        if rec.get("ok"):
            print(f"  OK  peak_reserved={rec['measured_peak_reserved_gb']}GB  {rec['seconds_per_clip']}s/clip  "
                  f"fits<= {rec['fit_ceiling_gb']}GB={rec['fits_cap']}  -> {rec['sample']}", flush=True)
        else:
            print(f"  FAIL  oom={rec['oom']}  {rec.get('error','')[:160]}", flush=True)

    report = {"meta": meta, "results": results}
    (args.out / f"benchmark-{host}.json").write_text(json.dumps(report, indent=2))
    _write_markdown(args.out / f"benchmark-{host}.md", report)
    print(f"\nWrote {args.out}/benchmark-{host}.md + .json + sample clips.", flush=True)
    return 0


def _write_markdown(path: Path, report: dict) -> None:
    m = report["meta"]
    cap = m.get("cap_gb")
    cap_line = (f"- **hard cap: {cap}GB** allocator "
                f"({m.get('applied_fraction')} of {m.get('device_total_gb')}GB real total) -- overflow OOMs like a {cap:.0f}GB card"
                if cap else "- cap: none (full card)")
    lines = [
        f"# LTX-Video i2v benchmark -- {m['host']}",
        "",
        f"- GPU: **{m['gpu']}**",
        cap_line,
        f"- keyframe: `{m['keyframe']}`  prompt: _{m['prompt']}_  warm: {m.get('warm')}",
        "",
        "| tier | model | engine | res | frames | steps | offload | peak alloc | peak reserved | fits cap | cold load | sec/clip | result |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in report["results"]:
        res = f"{r['width']}x{r['height']}"
        alloc = f"{r.get('measured_peak_alloc_gb','-')}GB" if r.get("ok") else "-"
        reserved = f"{r.get('measured_peak_reserved_gb','-')}GB" if r.get("ok") else "-"
        fits = ("yes" if r.get("fits_cap") else "NO") if r.get("ok") else "-"
        cold = r.get("cold_load_seconds")
        cold_s = f"{cold}s" if cold is not None else "-"
        spc = r.get("seconds_per_clip", "-")
        outcome = "OK" if r.get("ok") else ("OOM" if r.get("oom") else "FAIL")
        lines.append(
            f"| {r['tier']} | `{Path(r['model']).name}` | {r.get('engine','-')} | {res} | {r['frames']} | {r['steps']} | "
            f"{r['offload']} | {alloc} | {reserved} | {fits} | {cold_s} | {spc} | {outcome} |"
        )
    lines += ["", "Sample clips are written alongside this report; eyeball them for motion quality.",
              "`peak reserved` is the allocator pool the cap governs; `fits cap` is reserved <= the cap.",
              "A real card of the cap size also spends ~0.5-1GB on the CUDA context OUTSIDE this budget,",
              "so a flat \"runs on <cap>GB\" claim for a new tier waits on a true-hardware confirmation run."]
    path.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
