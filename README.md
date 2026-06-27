# vivijure-local-backend

The **local-consumer** render backend for Vivijure: image-to-video on a **single consumer GPU** (the
RTX 4060 Ti 16GB floor) running in your own homelab. The deliberate opposite of
[vivijure-backend](https://github.com/skyphusion-labs/vivijure-backend) (the RunPod datacenter engine,
Wan 2.2 on H200/B200).

**One studio, two honest doors.** The studio's `motion.backend` hook makes the clips engine pluggable.
The control plane is unchanged; the user picks the door: rent datacenter GPU, or run it on silicon they
already own. This backend is the second door -- no rent, no cloud GPU at all, reached over a Cloudflare
tunnel that terminates at the box.

```
control plane --> local-gpu module (CF Worker) --/run--> tunnel --> THIS backend (LTX-Video, RTX 4060 Ti 16GB)
```

## Run it on your own box (one command)

```sh
cp .env.example .env        # your R2 creds (+ optional LOCAL_BACKEND_TOKEN)
docker compose up -d        # first start caches the LTX weights, then serves :8000
curl localhost:8000/health  # {"ok":true,"engine":"ltx-video",...}
```

Then expose `:8000` over a Cloudflare tunnel and point your studio's `local-gpu` module at it. The full
homelabber walkthrough (prereqs, tunnel, honest trade-offs, troubleshooting) is
**[docs/HOMELABBER.md](docs/HOMELABBER.md)**; the studio-side wiring is
**[docs/INTEGRATION.md](docs/INTEGRATION.md)**.

Needs an NVIDIA GPU (16GB+) + the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html).

## What it runs

**LTX-Video**, selected over CogVideoX / SVD / AnimateDiff for the 16GB floor on fit + speed + license
(the dry comparison is [docs/i2v-model-selection.md](docs/i2v-model-selection.md)): the lightest real
i2v model, few-step distilled (fast on a consumer card), and the cleanest license for a freely-given
AGPL project. The three quality tiers map to LTX configs a 16GB card can honestly deliver -- `final` is
the card's honest ceiling, not datacenter parity.

| Tier | Model | Resolution | Frames | Offload |
|---|---|---|---|---|
| `draft` | LTX-Video (base) | 512x320 | 97 | model CPU offload + VAE tiling |
| `standard` | LTX-Video (base) | 704x512 | 121 (~5s) | model CPU offload + VAE tiling |
| `final` | LTX-Video (base) | 768x512 | 121 | model CPU offload + VAE tiling |

> VALIDATED on a 16GB Ada card ([docs/proof/RESULTS.md](docs/proof/RESULTS.md)): peak ~10.4GB (no OOM,
> ~6GB headroom), draft 38.6s/clip, standard 125.6s/clip. The few-step distilled + 13B "final" path
> (better quality/faster, via LTXConditionPipeline) is a follow-up; the base i2v above is proven.

## The job API (RunPod-compatible)

A long-running server (`src/vivijure_local/server.py`) the `local-gpu` module talks to exactly as
`own-gpu` talks to RunPod:

```
POST /run          { "input": { action: "i2v_clip", project, shot_id, prompt, keyframe_key?, config } } -> { "id" }
GET  /status/<id>  -> { id, status: IN_QUEUE|IN_PROGRESS|COMPLETED|FAILED, output?, error? }
POST /cancel/<id>  -> { ok: true }   (idempotent)
GET  /health       -> { ok: true, ... }
POST /run { "selftest": true } -> a no-GPU transport probe
```

The server owns an in-process serial job registry (a consumer card runs one i2v job at a time), the
RunPod-lifecycle stand-in for a box with no serverless platform.

## Develop (CPU: no GPU, no model weights)

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest                       # the full CPU suite (config, vram, frame math, jobs, server routing)
python -m py_compile src/vivijure_local/*.py
```

The pure logic is CPU-tested and green; the torch/diffusers generation body is deferred-imported and
validated on the card. The body raises a clear error rather than faking output if the GPU runtime is
absent -- a producer stage never ships a fake clip.

## The benchmark (proof gate)

`scripts/benchmark.py` runs the LTX i2v engine across the three tiers on the card, capturing fit (peak
VRAM / OOM), speed (sec/clip), and a real sample clip per tier, then writes a report. It is **ready to
fire** the instant the hardware is chosen; it does NOT run without a GPU (the spend gate). See
[docs/live-benchmark-plan.md](docs/live-benchmark-plan.md) for the costed plan.

## Security boundary

One credential: the shared-R2 key (read the keyframe, write the clip). Input is control-plane-trusted
(the module only reaches the box through the studio's service binding + your tunnel). An optional
`LOCAL_BACKEND_TOKEN` is defense in depth on the tunnel origin. The backend holds no studio secrets and
no submitter identity.

## License

**AGPL-3.0-only.** A labor of love, given freely: use it, learn from it, self-host it, build your own
creative visions on it. Run it as a network service and the AGPL has you share your changes back, so it
stays a commons. It is not for sale, and not to be resold as a SaaS.
