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
single-worker serial queue: extra submits wait IN_QUEUE. The render itself does NOT run in the HTTP
process; it runs in a persistent worker subprocess (see the next section), so cancel is clean: a queued
job is simply dropped, and a running job is cancelled by terminating the worker and respawning a fresh
one, which reclaims all CUDA/VRAM cleanly (sub-second). A box restart loses the in-memory job; the
module grace window then treats the resulting 404 as a real loss and fails the shot honestly, rather
than polling a dead job forever.

## The render runs in a worker subprocess (off the HTTP GIL)

The HTTP server and the GPU render live in TWO processes, not one. The door serves HTTP from a
`ThreadingHTTPServer`; if the render ran in that same process, every LTX sampler step would hold the
Python GIL in a single C-level torch call, so a `/status` poll landing in that window stalled and the
caller (cloudflared -> the module fetch) timed out on a HEALTHY render (vivijure#719 / 12gb#94). The
root fix (v0.5.0) moves the render into a persistent worker SUBPROCESS:

```
python3 -m vivijure_local.core.server         <- HTTP process: owns the job registry, answers /status
        |  newline-JSON IPC over stdin/stdout
python3 -m vivijure_local.core.render_worker   <- render process: model resident, runs the denoise
```

- `core/render_worker.py` is the child: it runs the SAME render body (fetch the keyframe from R2,
  animate, upload the clip, return the pointer) the door always ran, but in its own process. It keeps
  ONE model warm for its whole lifetime, so only its first job pays the load.
- `core/worker_client.py` is the parent (HTTP) side: it drives the worker over a small newline-JSON
  protocol and BLOCKS on a queue read while the worker renders. That blocking read RELEASES the GIL, so
  `/status` (a lock-free registry read in the HTTP process) stays sub-second at every percentile.

This subprocess core is BYTE-IDENTICAL to the sibling 16gb (CogVideoX) door, where it was live-proven on
the standing-door card (`/status` p99 ~6166 ms -> 1.1 ms during a completed render); the proof is
recorded once, in `vivijure-local-16gb docs/proof/SUBPROCESS-S38.md`. This door shares that core
verbatim, so the same isolation guarantee holds; the exact LTX per-step magnitude was not separately
benched.

The subprocess boundary also makes two behaviors honest for free:

- **Cancel = terminate + respawn.** Killing the worker process reclaims all CUDA/VRAM cleanly, unlike an
  in-process cooperative cancel that would leave the pipeline resident.
- **A worker crash fails the job honestly.** Worker death (an OOM SIGKILL, a segfault, a cold-start
  import failure) is detected as an EOF from the worker and surfaces as a real job failure, never a hang.

Two stdout-hygiene rules keep the protocol clean: the worker reserves fd 1 for the JSON protocol and
redirects everything else (torch / tqdm / stray prints) to stderr BEFORE any heavy import, and the
parent logs-and-skips any stdout line it cannot decode.

## Module layout (mirrors vivijure-backend)

| Module | Role |
|---|---|
| `core/contract.py` | the i2v_clip job I/O + the shared R2 key conventions (identical to the datacenter backend's) |
| `config.py` | the honest 12GB tier->engine mapping (`draft`/`standard`/`final` -> LTX configs) |
| `door.py` | the per-door identity + engine binding (`SERVICE`, `ENGINE`, `WEIGHTS_NOTE`, `animate`) -- the only seam `core` reads |
| `core/vram.py` | a pure, conservative VRAM budgeter: does a config fit 12GB, and which offload it needs |
| `i2v_ltx.py` | the LTX engine: pure frame/dimension math (8k+1, /32) + the deferred-torch `animate` body |
| `core/jobs.py` | the in-process async job registry (the RunPod-lifecycle stand-in) |
| `core/server.py` | the RunPod-compatible HTTP server (pure `route()` + a stdlib http shell); its i2v run_fn drives the render worker over IPC instead of rendering in-process |
| `core/worker_client.py` | the parent-side IPC client: spawns + drives the render worker, blocks off-GIL while it renders, detects worker death |
| `core/render_worker.py` | the render worker subprocess: the deferred-torch render body, model kept warm, isolated from the HTTP GIL |
| `core/r2.py` | minimal shared-bucket object I/O (the one credential the backend holds) |

## Shared core with the sibling door (vivijure_local.core -- extracted)

This door and its sibling (`vivijure-local-12gb`, LTX-Video / `vivijure-local-16gb`, CogVideoX) are
~90% the same code. That shared surface now lives in the `vivijure_local.core` package, kept
BYTE-IDENTICAL across both repos: `r2`, `contract`, `jobs`, the pure `vram` math, the `announce` ready
banner, and the RunPod-compatible `server` scaffold. Each door keeps ONLY its own `config.py` tier
table, its engine module (`i2v_ltx` / `i2v_cogvideox`), and a tiny `door.py` identity + binding
(`SERVICE`, `ENGINE`, `WEIGHTS_NOTE`, `animate`) that the core reads through the stable `..door` /
`..config` seam. That is the honest per-model part; everything else is shared.

DONE (S6, extraction): the `core/` package replaced the duplicated top-level modules. The two copies are
proven identical in each PR by `diff -r` of the two `core/` trees (only `door.py` + `config.py` + the
engine module differ between the repos, as they should). A change to any core file MUST be mirrored to
the sibling door in the same change and the byte-identical invariant re-checked.

Promoting `core` to a TRUE single source (its own repo, or a git submodule both doors vendor) is now a
trivial later lift -- the package is already self-contained and seam-clean. It was deliberately NOT done
now: a new repo would trigger the full new-repo governance standard mid-sprint, and the vendored-copy
form (like the studio's `src/modules/types.ts` contract) already closes the drift with a mechanical
`diff` gate. This closes out the earlier new-repo-vs-vendored question: vendored, for now.

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
