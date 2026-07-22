# Changelog

All notable changes to vivijure-local-12gb are recorded here. This project follows SemVer-style
`0.MINOR.PATCH` while pre-1.0 (PATCH for fixes and backend tweaks, MINOR for features).

## Unreleased

## v1.0.4 -- 2026-07-22

PATCH. Cast-less keyframe preview fix (#153 / #122).

- **fix(preview):** cast-less bundles no longer crash with `image_embeds in added_cond_kwargs`; IP-Adapter loads only when a cast ref image is present (matches vivijure-backend keyframe path).
- **`__version__` -> 1.0.4**; `docker-compose.yml` pins `:1.0.4`.

## v1.0.3 -- 2026-07-22

PATCH. Local-GPU keyframes on the door (vivijure-local#153).

- **feat:** `action: preview` SDXL keyframes (RealVisXL + Hyper-SD, IP-Adapter, pretrained LoRAs).
- **fix(security):** strict `bundle_key` / LoRA key validation; hardened tar extract; safe image load; allowlisted model env overrides.
- **`__version__` -> 1.0.3**; `docker-compose.yml` pins `:1.0.3`.

## v1.0.2 -- 2026-07-22

PATCH. Honest failure surfaces for the LTX door (local-12gb#99 Defect 1).

- **fix(i2v):** wrap clip PUT in contextual `RuntimeError` + stderr (mirrors keyframe GET).
- **fix(jobs):** log every handled job failure to stderr so `docker logs` shows the real error.
- Defect 2 (dead R2 key) was an earlier bring-up fix; this release is the observability code path.

## v1.0.1 -- 2026-07-16

PATCH release so production pins a **semver** consumer image that inherits the re-baked `runtime-t1`
base (previously only visible as floating `runtime-t1*` tags newer than `1.0.0`).

- **Inherit re-baked runtime base** (`RUNTIME_REF` repin #105) + thin-release bake lane (#103).
- **deps:** pip-minor-patch group bump (#106).
- **ci(publish):** warm snapshot runner wiring (#107).
- **`__version__` -> 1.0.1**; `docker-compose.yml` pins `:1.0.1` (not `:latest` / not a SHA).

## v1.0.0 -- 2026-07-13

**First stable release of the LTX 12GB local-gpu door.** The mainstream self-host render path in the
Vivijure constellation (LTX-Video i2v on a single 12GB-class card), output-verified end-to-end for
Studio v1.0.0 and running live on propagandhi. `__version__` is bumped to `1.0.0`, so `/health` now
reports the release version (closing the v0.5.1 version-string lag where `__version__` stayed `0.5.0`).

Folds in the v0.5.1 storage fix that shipped without its own entry:

- **`R2_S3_ENDPOINT` override for S3-compatible object stores (#100).** When `R2_S3_ENDPOINT` is set the
  door talks to a non-CF endpoint (e.g. MinIO) and forces path-style addressing; unset, it derives the
  Cloudflare R2 endpoint from the account id as before. This is what lets a door share a local MinIO
  bucket with a self-hosted control panel (proven e2e: keyframe read + clip write against
  `minio-flatliners`).

Built on v0.5.0's subprocess-isolated render (#94, the `/status` stall root fix). The `v1.0.0` tag
builds the consumer image; re-pull `:latest` on the host to run it.

## v0.5.0 -- 2026-07-12

Root fix for the `/status` stall (#94, the LTX-door half of vivijure#719).

- **Render isolated in a worker subprocess (#94).** Byte-identical shared-core port of the 16GB door
  fix (vivijure-local-16gb#77 / #78). The door served HTTP from a `ThreadingHTTPServer` whose `/status`
  handler thread shared the GIL with the in-process render; each LTX sampler step holds the GIL in a
  single C-level torch call, so a poll landing in that window stalled and timed out on a HEALTHY render.
  The render now runs in a persistent worker SUBPROCESS (`core/render_worker.py`) driven by the HTTP
  process over a small newline-JSON IPC protocol (`core/worker_client.py`); the HTTP process blocks
  off-GIL, so `/status` stays sub-second at every percentile. The `/status`, `/cancel`, and `i2v_clip`
  contracts are byte-identical; the worker keeps the model warm (only the first job loads it); cancel is
  terminate + respawn (process death reclaims all CUDA/VRAM); a worker crash fails the job honestly
  instead of hanging; `apply_vram_cap` now runs in the worker (the process that loads the model). No API
  change (MINOR for the architecture change).
- The fix core is byte-identical to the 16GB door; its live proof on the standing-door card (`/status`
  p99 ~6166 ms -> 1.1 ms during a completed render) is recorded once, in the 16GB repo:
  `vivijure-local-16gb docs/proof/SUBPROCESS-S38.md`. This door shares that core verbatim, so the same
  isolation guarantee holds; the exact LTX per-step magnitude was not separately benched.

## v0.4.1 -- 2026-07-12

Feature: the door omits the duration grid on `/health` for the LTX door (#707).

- Companion to the 16GB door change. The door is the declaring source for its clip-length grid on
  `/health`; LTX scales resolution + frames per tier with no fixed grid, so this door declares NO
  `duration_grid` (its absence means "no declared constraint"). The byte-identical core reads
  `door.DURATION_GRID` via `getattr`; this door defines no such attribute, so `/health` omits the
  block. Hermetic test asserts the omission. No engine change.

## v0.4.0 -- 2026-07-12

Feature: `VIVIJURE_OFFLOAD` operator knob to pick the diffusers offload mode (#91).

- A big-VRAM operator can now run LTX RESIDENT on the card instead of being forced through per-step
  offload. Set `VIVIJURE_OFFLOAD=none` (whole model resident, fastest, needs a big card, roughly
  20GB+), `=model` (page whole pieces to CPU), or `=sequential` (per-layer paging: the low-VRAM
  fallback, what the 13B `final` tier uses to fit 12GB). When set it applies to EVERY tier.
- UNSET (the default) keeps each tier hardcoded offload byte-for-byte, so an existing install is
  UNCHANGED -- draft/standard still page whole pieces, final still pages per-layer for the 12GB fit.
- An invalid value FAILS LOUD at startup with the valid list, never a silent default (the honesty rule).
- The engine already had the resident path (`pipe.to("cuda")`); this wires the env override into
  `config.from_request` and validates it at boot (`server.validate_offload_or_exit`). Hermetic CPU
  tests cover the parse, the per-tier override, the unset==default guarantee, and the startup guard.
- Shares the byte-identical `core` change with the sibling 16GB door (16gb#74 / v0.3.0). The fastest
  offload mode that fits is card-dependent; on a 12GB card leave it unset, `none` is for the 20GB+
  operator.

## v0.3.1 -- 2026-07-11

Fix: `/cancel` now actually aborts a running render (#87, PR #88).

- The engine step callback swallowed ALL exceptions (`except Exception: pass`), including the
  `core.jobs.Cancelled` signal the job registry raises to abort a render between denoise steps. So
  `POST /cancel` returned `{ok: true}` while the denoise ran to completion and shipped a full clip --
  a silent no-op (and a silent-degrade violation). The callback now re-raises `Cancelled` and swallows
  only genuine progress-reporting errors, so a cancel aborts the denoise at the next step. Hermetic
  tests assert a `Cancelled` raised in the step callback aborts the denoise loop (RED->GREEN); proven
  live on the card (S36).

## v0.3.0 -- 2026-07-11

The 13B quality tier (#1, PR #83): `final` now renders through the 13B-distilled model.

- **`final` = LTX-Video-0.9.8-13B-distilled** via `LTXConditionPipeline` (LTXVideoCondition input),
  sequential (per-layer) CPU offload + VAE tiling: peak 4.63GB reserved under a HARD 12GB allocator
  cap on real silicon, 108.4s/clip warm at 768x512 -- higher quality AND faster than `standard`
  (10 distilled steps vs 40). Proof: docs/proof/BENCH-13B.md (+ raw JSON and real sample clips).
- **`draft` and `standard` stay the proven base 2B** (fast + fitting). The bench showed
  0.9.7-distilled is itself 13B-class (48 layers / 4096 dim), so a distilled draft would OOM or be
  slower than base; rejected per the honesty gate. A real 2B distilled draft is #84.
- The CONDITION engine path, `is_distilled` output flag, and an optional generate-low-then-upscale
  spatial upsampler ship wired + unit-tested (upsampler OFF pending a live bench). 80 hermetic tests.
- HONESTY: "fits a hard 12GB allocator cap" is the proven claim; a flat "runs on a 12GB card" for
  `final` stays PENDING a true-12GB confirmation run (CUDA context lives outside the cap).

## v0.2.1 -- 2026-07-10

Publish build fix; the render engine is unchanged.

- **Re-pin `av` to 17.1.0 (GHCR build fix).** Dependabot's 17.1.0 -> 18.0.0 bump broke the image
  build: av 18 ships no Python 3.10 wheels and the door image builds on py3.10. CI never builds the
  Docker image, so the bump passed green and the v0.2.0 publish was the first build to hit it.
  Dependabot now ignores `av` (bump only together with a base-image Python move).
- **`__version__` bumped to 0.2.1.**

## v0.2.0 -- 2026-07-10

From-scratch homelabber onboarding: a dependency preflight and one tested bare-OS install path. The
render engine is unchanged.

- **`preflight.sh`: a dependency preflight that checks, never installs (#70).** Run it before
  `docker compose up`. It checks the NVIDIA driver (present and >= the 550 floor, card visible), GPU
  VRAM against the door's floor, Docker (installed and daemon up), the compose plugin, that a
  `--gpus all` container can ACTUALLY see the GPU (the real NVIDIA Container Toolkit test, not just
  "package installed"), and free disk. Each failed check names the exact HOMELABBER.md step that fixes
  it and the script exits non-zero; it installs nothing (Conrad ruling: we do not auto-install across
  every package manager). Door-portable via `DRIVER_FLOOR` / `VRAM_FLOOR_MIB` / `DISK_FLOOR_GB` plus a `WARN_ON_VGPU` seam
  (default off here: LTX renders correctly on a vGPU slice; the 16GB CogVideoX door ships it on),
  mirroring the runtime `core/gpu_virt.py` per-door guard.
- **HOMELABBER.md is now one tested Ubuntu 24.04 LTS path (#70).** Added an "already have `nvidia-smi`
  working? skip ahead" branch, a "Confirm your box is ready (preflight)" section, retired the stale
  "we have not run a from-scratch driver install ourselves" caveat (the driver + Docker + Container
  Toolkit sequence is now the proven bring-up path), and scoped the docs to the one tested distro
  (other distros point at each project's official guide).
- **Shared `vivijure_local.core` re-synced with the 16GB door (#62).** Brought in the `gpu_virt`
  boot-warning seam so `core/` is byte-identical across the two doors again. It stays a silent no-op
  here by construction: the guard is door-gated on `door.VGPU_UNSUPPORTED`, which the vGPU-tolerant LTX
  door does not set.
- **`__version__` bumped to 0.2.0.**

## v0.1.3 -- 2026-07-05

Homelabber-facing hardening and docs honesty; the render engine is unchanged.

- **Compose no longer collides with another stack on the same host (#57).** The three services carried
  fixed `container_name`s (`vivijure-cloudflared`, `vivijure-ready`, `vivijure-local-12gb`), which are
  global per host: running this door next to a media stack that also names a container
  `vivijure-cloudflared`, or next to the sibling 16GB door, aborted one stack. Dropped the explicit
  names so Compose scopes them per project (`<project>-<service>-1`); inter-service DNS and
  `docker compose logs <service>` are unchanged.
- **Dockerfile no longer claims "NOT on RunPod" (#58).** A stale homelab-era comment; the pinned
  cu124 / torch 2.5.1 stack is portable to any CUDA 12.x card, homelab consumer OR datacenter
  secure-cloud pod.
- **HOMELABBER troubleshooting: moving a door to a new R2 account (#59).** Added the self-diagnosis for
  a `could not fetch keyframe ... (404)` after a door move: the door reads and writes against ITS OWN
  `.env` R2, so re-wire `R2_ACCOUNT_ID` / `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` / `R2_BUCKET` to
  the new studio's bucket.
- **INTEGRATION.md rewritten for an outside homelabber (#60).** The studio-wiring recipe dropped the
  internal-CI framing (ci.yml EXCLUDE, PR #382, stale studio versions) for the real path: set
  `INSTALL_LOCAL_GPU=1` plus `LOCAL_BACKEND_URL` and `LOCAL_BACKEND_TOKEN` in the studio's `deploy.env`
  and run `./deploy.sh`, with a by-hand fallback.
- **`__version__` bumped to 0.1.3.**

## v0.1.2 -- 2026-07-04

Ready-banner correctness after a restart, plus a bare-OS prerequisite-install guide. The render
engine is unchanged.

- **The ready banner now shows the CURRENT quick-tunnel URL after a restart (#52).**
  `announce` parsed the FIRST match in `/shared/cf.log`, but that log is on the persistent volume and
  cloudflared APPENDS a fresh URL on every (re)start, so after any restart (the documented
  `docker compose up -d` update path, a reboot, or a crash) the banner advertised the oldest, dead URL
  and the studio could not reach the box. It now takes the LAST match, and `init-shared` clears
  `cf.log` on start (`rm -f`, so the nonroot cloudflared keeps ownership under the sticky `/shared`).
  Verified live on a real box with a full restart cycle.
- **Bare-OS prerequisite-install guide in the quickstart (#51).** The docs previously only
  stated the requirements (NVIDIA driver 550+, Docker, NVIDIA Container Toolkit) and linked out; a
  novice on a fresh OS now gets the actual install commands.
- **`__version__` bumped to 0.1.2** so `/health` reports the shipped version (it was left at 0.1.0
  through v0.1.1).

## v0.1.1 -- 2026-07-04

Fix the default quick-tunnel bring-up (compose + ready-banner fix; the render engine is unchanged).

- **cloudflared no longer crash-loops on `docker compose up`.** The `cloudflared` service wrapped its
  tunnel startup in an inline `sh -c` script, but the `cloudflare/cloudflared` image is distroless (no
  shell) and its entrypoint is `cloudflared --no-autoupdate`, so the script was passed to cloudflared as
  arguments and never ran; the default quick tunnel never started and the `ready` banner never printed a
  Backend URL. The service now invokes cloudflared natively:
  `tunnel --url http://vivijure-local-12gb:8000 --logfile /shared/cf.log`. No shell, no entrypoint override, no
  dependence on the distroless image's contents.
- **A one-shot `init-shared` service makes the shared volume writable by cloudflared's nonroot UID.**
  `cloudflare/cloudflared` runs as nonroot (65532) and cannot create `/shared/cf.log` in the root-owned
  shared volume, so `--logfile` failed with permission-denied and the banner got no URL. `init-shared`
  (the app image, root) now runs to completion first (compose `service_completed_successfully`) and sets
  `/shared` to sticky-world-writable (`chmod 1777`, the `/tmp` model), so nonroot cloudflared can create
  its logfile while the sticky bit still protects root-owned files like the token. cloudflared itself
  stays nonroot.
- **The named tunnel is now a documented `docker-compose.override.yml`** (see HOMELABBER "A stable
  address") instead of an automatic `.env` switch, because a shell-free static command cannot branch on
  whether `TUNNEL_TOKEN` is set. The novice quick-tunnel path stays the tracked default.
- **The ready banner reports the real tunnel state.** It shows the actual quick-tunnel URL whenever one
  is live (regardless of whether `TUNNEL_TOKEN` is set), and prints the named-hostname line only when no
  quick URL appears and `TUNNEL_TOKEN` is set, with a one-line hint so a partial config self-diagnoses.
  Keeps "a degrade is never silent": the banner never claims a named hostname while a quick URL is live.

Honest history: the 2026-06-18 switch of `cloudflare/cloudflared:latest` to a distroless nonroot base
broke this compose tunnel service two ways at once -- it removed the shell the inline `sh -c` script
needed, AND it dropped cloudflared to a nonroot UID (65532) that cannot write the root-owned `/shared`.
Combined with the pre-existing entrypoint mismatch, the service was never functional via
`docker compose up`; any tunnel URL in earlier proofs came from a hand-run cloudflared out-of-band.
v0.1.1 addresses all three.

## v0.1.0 -- 2026-07-04

First public release of the LTX-Video local render door: the studio's image-to-video motion engine
running on the operator's own 12GB+ NVIDIA GPU, no cloud GPU and no per-render bill.

### What this image ships

- **LTX-Video i2v backend** rendering on your own card (12GB VRAM floor, proven), served over a
  token-gated HTTP API with a `/health` check.
- **One-command bring-up** (`docker compose up`): the render server, its own Cloudflare tunnel, and a
  copy-paste "ready" banner that prints the Backend URL + token for the studio's "Local (your GPU)" door.
- **Prebuilt image pulled from GHCR** (`ghcr.io/skyphusion-labs/vivijure-local-12gb`), so a novice
  pulls instead of cold-building torch layers. Source builders can still `docker compose up --build`.
- **Secure by default:** the tunnel is public, so the i2v endpoint hard-rejects any request without the
  token; the token auto-generates if the operator leaves it blank and is shown in the banner.
- **Built-in tunnel:** a zero-config TryCloudflare quick tunnel by default, or a stable named tunnel
  when `TUNNEL_TOKEN` is set.
- **Three honest quality tiers** (draft / standard / final) mapped to what a 12GB card actually runs,
  plus an optional `VIVIJURE_MAX_VRAM_GB` cap to share the card with other work.
- **Shared R2 bucket** for the render contract: reads the keyframe, writes the finished clip.
- **No SSH in the released image:** the interim out-of-band SSH is build-arg gated (`INCLUDE_SSH=0`
  default) and the CI publish lane passes no build args, so the shipped image carries no `sshd`.

### Notes

- LTX-Video weights (~10GB) download on the FIRST render into a persistent volume and are reused after,
  so the first render takes several extra minutes.
- `docker-compose.yml` pins `pull_policy: missing`, so the image never auto-updates. To move to a newer
  release, pull explicitly: `docker compose pull` then `docker compose up -d`.
