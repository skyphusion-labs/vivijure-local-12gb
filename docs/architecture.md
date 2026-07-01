# Architecture: the local-consumer render backend

> The 12GB door. How this backend fits the Vivijure studio without changing the control plane, and how
> it is the deliberate opposite of the RunPod datacenter backend.

## One studio, two honest doors

Vivijure is a host, not a monolith: the control plane owns project / storyboard / cast / bundle / the
render spine + a module registry, and every capability beyond that is an opt-in module behind a typed
hook contract. The `motion.backend` hook (keyframe + motion prompt -> shot clip, pick-one) is the seam.
The control plane invokes the hook; it does not know who answers. So two backends can serve the same
hook, and the USER picks the door:

```
                         vivijure control plane (UNCHANGED)
                                     |  motion.backend hook (pick one)
            +------------------------+--------------------------+
            |                                                   |
   DATACENTER door                                      LOCAL-CONSUMER door (this repo)
   module: alibaba-wan / own-gpu                        module: local-gpu
            |  POST /run (i2v_clip)                              |  POST /run (i2v_clip)  [same wire body]
            v                                                   v
   RunPod serverless                                   Cloudflare tunnel -> homelab box
            |                                                   |
   vivijure-backend                                    vivijure-local-12gb (this repo)
   Wan 2.2 A14B MoE (H200 / B200)                      LTX-Video (12GB consumer GPU)
```

The two backends speak the IDENTICAL `i2v_clip` job contract (`{ action, project, shot_id, prompt,
keyframe_key?, config }`) and write the clip to the SAME shared R2 bucket, returning a pointer-only
result. That sameness IS the swappability: the same control plane, the same per-shot `buildI2vBody`,
drive either door. The only difference is the box behind the endpoint and the engine on it.

## Why "local-consumer" is genuinely different from "own-gpu"

The existing `own-gpu` module is "bring your own keys" -- but it still runs on a **RunPod endpoint the
user provisions**. It is own-keys, not own-silicon. This backend is the real homelab door: the work
happens on a consumer card the user already owns, reached over a Cloudflare tunnel. No rent, no cloud
GPU at all. That is the point of the 12GB floor: the deliberate opposite of the datacenter backend.

## The two halves

| Half | Where | What |
|---|---|---|
| `local-gpu` module worker | `vivijure/modules/local-gpu/` (a CF Worker) | the contract bridge: serves `/module.json` `/invoke` `/poll` `/cancel`; submits `i2v_clip` to the box, polls `/status`, surfaces the clip_key. A near-clone of `own-gpu` + `/cancel`. |
| `vivijure-local-12gb` | this repo (runs on the box) | the engine: a long-running server exposing a RunPod-compatible job API, an in-process async job registry, and the LTX-Video i2v engine scoped to a 12GB card. |

## Why a RunPod-compatible job API on the local box

The datacenter backend is RunPod serverless: RunPod owns the `/run` + `/status` + `/cancel` lifecycle
and the queue. The local box has no such platform, so this backend provides that lifecycle itself
(`server.py` + `jobs.py`). Exposing the SAME endpoints + the SAME status envelope (IN_QUEUE /
IN_PROGRESS / COMPLETED / FAILED) means the `local-gpu` module's poll loop is a near-clone of
`own-gpu`'s -- minimum new surface, maximum reuse of the proven #141 grace-window discipline.

A consumer card runs ONE i2v job at a time (a 12GB card cannot fit two pipelines), so the registry is a
single-worker serial queue: extra submits wait IN_QUEUE. Cancel is best-effort + cooperative -- a
queued job is dropped; a running job is flagged so the engine's progress callback aborts between
denoise steps (a torch step is not externally interruptible). A box restart loses the in-memory job;
the module's grace window then treats the resulting 404 as a real loss and fails the shot honestly,
rather than polling a dead job forever.

## Module layout (mirrors vivijure-backend)

| Module | Role |
|---|---|
| `contract.py` | the i2v_clip job I/O + the shared R2 key conventions (identical to the datacenter backend's) |
| `config.py` | the honest 12GB tier->engine mapping (`draft`/`standard`/`final` -> LTX configs) |
| `vram.py` | a pure, conservative VRAM budgeter: does a config fit 12GB, and which offload it needs |
| `i2v_ltx.py` | the LTX engine: pure frame/dimension math (8k+1, /32) + the deferred-torch `animate` body |
| `jobs.py` | the in-process async job registry (the RunPod-lifecycle stand-in) |
| `server.py` | the RunPod-compatible HTTP server (pure `route()` + a stdlib http shell) + the i2v run_fn |
| `r2.py` | minimal shared-bucket object I/O (the one credential the backend holds) |

## The CPU / GPU split (testing)

Exactly like vivijure-backend: everything CPU-testable is pure and unit-tested (config, vram, frame
math, the job registry, the server router, the run_fn with a fake store) -- no torch, no GPU, no spend.
The torch/diffusers generation body is deferred-imported and validated on the card (the spend gate, see
`live-benchmark-plan.md`). The body raises a clear error rather than faking a clip if the runtime is
absent: a producer stage never ships fake output.

## What stays in the control plane (unchanged)

Nothing in `vivijure/src/` changes. The studio discovers `local-gpu` from a `MODULE_LOCAL_GPU` service
binding, reads its manifest, indexes it under `motion.backend`, and renders its stage from its
`config_schema` -- the same path every other module uses. Wiring the binding + the tunnel is infra
(Strummer's lane); this repo + the module are the backend + the contract surface.
