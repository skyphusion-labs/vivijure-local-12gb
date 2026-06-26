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

It speaks the IDENTICAL `i2v_clip` job contract as the datacenter backend and shares the same R2
bucket, so the same control plane drives either door. See [docs/architecture.md](docs/architecture.md).

## What it runs

**LTX-Video**, selected over CogVideoX / SVD / AnimateDiff for the 16GB floor on fit + speed + license
(the dry comparison is [docs/i2v-model-selection.md](docs/i2v-model-selection.md)): the lightest real
i2v model, few-step distilled (fast on a consumer card), and the cleanest license for a freely-given
AGPL project. The three quality tiers map to LTX configs a 16GB card can honestly deliver -- `final` is
the card's honest ceiling, not datacenter parity.

| Tier | Model | Resolution | Frames | Offload |
|---|---|---|---|---|
| `draft` | LTX 2B distilled | 512x320 | 97 | model CPU offload + VAE tiling |
| `standard` | LTX 2B distilled | 704x480 | 121 (~5s) | model CPU offload + VAE tiling |
| `final` | LTX 13B fp8 distilled | 768x512 | 121 | sequential CPU offload + VAE tiling |

> These are conservative SCAFFOLD defaults. The exact 16GB ceilings + real wall-clock are finalized by
> the live benchmark ([docs/live-benchmark-plan.md](docs/live-benchmark-plan.md)), which is flagged for
> Conrad and NOT yet executed (the spend gate).

## The job API (RunPod-compatible)

A long-running server (`server.py`) the `local-gpu` module talks to exactly as `own-gpu` talks to
RunPod:

```
POST /run          { "input": { action: "i2v_clip", project, shot_id, prompt, keyframe_key?, config } } -> { "id" }
GET  /status/<id>  -> { id, status: IN_QUEUE|IN_PROGRESS|COMPLETED|FAILED, output?, error? }
POST /cancel/<id>  -> { ok: true }   (idempotent)
GET  /health       -> { ok: true, ... }
POST /run { "selftest": true } -> a no-GPU transport probe
```

The server owns an in-process serial job registry (a consumer card runs one i2v job at a time), the
RunPod-lifecycle stand-in for a box with no serverless platform.

## Quickstart (CPU dev: no GPU, no model weights)

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest                       # the full CPU suite (config, vram, frame math, jobs, server routing)
python -m py_compile src/vivijure_local/*.py
```

The pure logic is CPU-tested and green; the torch/diffusers generation body is deferred-imported and
validated on the card. The body raises a clear error rather than faking output if the GPU runtime is
absent -- a producer stage never ships a fake clip.

## Run on the box (GPU)

```bash
pip install -r requirements.txt    # + torch/torchvision from the CUDA index (see deploy/Dockerfile)
# R2 (shared vivijure bucket) creds + optional token in the env:
export R2_ACCOUNT_ID=... R2_ACCESS_KEY_ID=... R2_SECRET_ACCESS_KEY=... R2_BUCKET=vivijure
export LOCAL_BACKEND_TOKEN=...     # optional shared secret the local-gpu module sends
python -m vivijure_local.server    # serves on :8000
```

Then expose `:8000` via a Cloudflare tunnel and point the `local-gpu` module's `LOCAL_BACKEND_URL` at
the tunnel hostname.

## Security boundary

One credential: the shared-R2 key (read the keyframe, write the clip). Input is control-plane-trusted
(the module only reaches the box through the studio's service binding). An optional `LOCAL_BACKEND_TOKEN`
is defense in depth on the tunnel origin. The backend holds no studio secrets and no submitter identity.

## License

**AGPL-3.0-only.** A labor of love, given freely: use it, learn from it, self-host it, build your own
creative visions on it. Run it as a network service and the AGPL has you share your changes back, so it
stays a commons. It is not for sale, and not to be resold as a SaaS.
