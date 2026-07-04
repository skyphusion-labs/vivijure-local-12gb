# Wiring the local door into the studio

How the `vivijure-local-12gb` (this repo) plugs into the Vivijure studio through the `local-gpu`
motion.backend module. The control plane is UNCHANGED in code; wiring is binding + secrets + a
reachable backend. That backend runs on **any machine with a 12GB+ GPU and a cloudflared tunnel** --
a homelab box (the primary audience) or a disposable cloud pod (how the June plumbing proof itself
ran: a 16GB pod through a public TryCloudflare quick tunnel, `docs/RUN-LOG.md`). The homelab is the
audience example, not the architecture. The benchmark has proven out (`docs/proof/RESULTS.md`); wire
it and flip it on.

## The picture

```
studio control plane --(service binding MODULE_LOCAL_GPU)--> local-gpu module worker
   local-gpu --(POST /run i2v_clip, GET /status, POST /cancel)--> Cloudflare tunnel --> THIS backend (any machine with a 12GB+ GPU)
```

The `local-gpu` module (in the `vivijure` repo, `modules/local-gpu/`) is the bridge. It holds the
backend URL + the shared token and speaks the same `i2v_clip` wire body as the datacenter door. This
backend shares the studio's R2 bucket, so it reads the keyframe by key and writes the clip by key --
the module moves no bytes.

## The contract this backend exposes (what the module calls)

| Endpoint | Purpose |
|---|---|
| `POST /run` `{ "input": { action:"i2v_clip", project, shot_id, prompt, keyframe_key?, config } }` -> `{ "id" }` | submit a clip job |
| `GET /status/<id>` -> `{ id, status: IN_QUEUE\|IN_PROGRESS\|COMPLETED\|FAILED, output?, error? }` | poll |
| `POST /cancel/<id>` -> `{ ok: true }` | cancel (idempotent) |
| `GET /health` -> `{ ok: true, ... }` | liveness (tunnel + compose healthcheck) |
| `POST /run { "selftest": true }` -> `{ ok:true, selftest:true }` | no-GPU transport probe |

`config` carries `{ quality (draft\|standard\|final), num_frames, fps, seed?, flow_shift?, negative_prompt? }`.
Auth: the i2v routes **require** the token. The tunnel is public, so the backend refuses to serve i2v
open -- it returns **503** when `LOCAL_BACKEND_TOKEN` is unset (refuse to run open) and **401** when the
`Authorization: Bearer <token>` header is missing or wrong. The container **auto-generates** a strong
token when you leave it blank (the `ready` banner prints it); `/health` and the no-GPU selftest stay
open for liveness. The module sends the token as `Authorization: Bearer <token>`.

## Progress and status semantics (poll-only)

There is **no sub-step progress channel**. `GET /status/<id>` returns only the RunPod-compatible
`IN_QUEUE | IN_PROGRESS | COMPLETED | FAILED` envelope -- deliberately identical to the datacenter
(own-gpu) door, so the `local-gpu` module's poll loop is unchanged. There is no percentage and no
per-denoise-step event; the module polls until the status is terminal.

One consequence to plan for: **`IN_PROGRESS` covers the first-render weights load, not just the
denoise.** The first job on a freshly started (cold) box loads the model into VRAM before any denoise
step runs, and a box that has never pulled the model downloads several GB of weights first. During that
window `/status` reads `IN_PROGRESS` with no output for **several minutes** -- indistinguishable from a
hang from the poll side alone.

Two things make it legible:

- **Server logs.** The backend prints a one-time heads-up on the first job (`vivijure-local: first i2v
  job on this process -- the model weights load now ...`). An operator tailing the container logs sees
  the cold load is progressing, not stuck.
- **The warm model.** After the first job the model stays loaded for the life of the process, so every
  subsequent clip skips the load and goes straight to denoise.

If the studio wants a richer progress signal later (e.g. an R2 NDJSON event stream the planner tails),
that is an additive, cross-repo change -- it is intentionally NOT built here, because the door's job is
to match the datacenter contract, and the datacenter door is poll-only too.

## The flip checklist (studio side)

STATUS (studio v0.14.0, 2026-07-04): the `local-gpu` module is **IN** the studio CI deploy loop, the
core **binds `MODULE_LOCAL_GPU`**, and the door is **live** in the registry + the planner's
motion.backend selector (proven end to end through a live pod render, studio PRs `vivijure#383`
un-exclude + `#384` core binding). The sequence below is the **wiring recipe** -- how the door gets
stood up, retained for a self-hoster bringing up their OWN studio, not pending work on this one. Order
still matters: seed secrets before the deploy, and only flip once the backend endpoint is reachable.

1. **Seed the module secrets FIRST** into the account Cloudflare Secrets Store. This must precede the
   deploy: `local-gpu`'s `wrangler.toml` binds them by `secret_name`, and `wrangler deploy` FAILS if the
   store secret does not exist (that is exactly what broke studio v0.7.6). Same store + flow as the
   RunPod modules (studio `docs/DEPLOYMENT.md` "Module secrets via the Secrets Store"). A persistent
   studio binding needs a STABLE backend URL, so seed a named-tunnel hostname here; the default
   quick-tunnel URL is ephemeral (it changes on restart):

   ```sh
   # the STABLE (named-tunnel) hostname terminating at the reachable backend (no trailing slash)
   wrangler secrets-store secret create <STORE_ID> --name LOCAL_BACKEND_URL   --value "https://render.example"
   # the shared token the backend enforces (match the backend's .env LOCAL_BACKEND_TOKEN, or the
   # container-generated one from the `ready` banner)
   wrangler secrets-store secret create <STORE_ID> --name LOCAL_BACKEND_TOKEN --value "<openssl rand -hex 32>"
   ```

2. **Include the module in the studio CI deploy** (`.github/workflows/ci.yml` -- it must not sit in the
   `EXCLUDE` list). With the secrets seeded (step 1) its `wrangler deploy` succeeds, so
   `vivijure-module-local-gpu` deploys. (On skyphusion's own studio this meant removing the interim
   EXCLUDE that PR #382 added while the v0.7.6 secrets were still unseeded.)

3. **Bind it to the core.** Add a `[[services]]` binding to the core `wrangler.toml.example` so the
   registry discovers it (the registry scans env for `MODULE_*` service bindings). A `[[services]]`
   binding must point at a DEPLOYED module (else the core deploy dangles), so do this AFTER step 2:

   ```toml
   # Local consumer GPU (LTX-Video on a 12GB card). The local door.
   [[services]]
   binding = "MODULE_LOCAL_GPU"
   service = "vivijure-module-local-gpu"
   ```

4. **The backend must already be running + reachable** at `LOCAL_BACKEND_URL` (this repo, on any
   machine with the GPU -- homelab box or cloud pod -- via a cloudflared tunnel) BEFORE steps 1-3 make
   the door user-visible -- a picked door pointing at nothing fails every render. For a persistent
   binding use a STABLE (named-tunnel) URL; the default quick-tunnel URL is ephemeral. See the
   `docs/HOMELABBER.md` quickstart + `docker-compose.yml`.

5. **Verify a live local-door render** end to end (the door appears in the selector and produces a clip).

When this sequence completes the local door appears in the planner's motion.backend selector (Joan's
#379 selector renders it from the manifest's `ui.locality="local"` framing) and renders end to end --
which is exactly the state skyphusion's own studio has been in since v0.14.0.

A homelabber wiring their OWN studio does the same wiring by hand, minus the CI steps: run the backend
(`docs/HOMELABBER.md`), then paste the `ready` banner's Backend URL + token into the studio's
"Local (your GPU)" door. The named-tunnel upgrade is what turns that into a set-once address.

## Trust boundary (do not break)

The `local-gpu` module has NO public surface (`workers_dev=false`, no route): the studio service
binding IS its auth. The backend itself sits behind a Cloudflare tunnel and **requires**
`LOCAL_BACKEND_TOKEN` on every i2v request -- it refuses to serve open (503 unset / 401 wrong; see Auth
above), and the container auto-generates a token if you do not set one. Its port is only `expose`d on
the compose network (never published to the host), so it is reachable only through the tunnel. A
public, untokened render endpoint would be an unauthenticated GPU-spend / DoS trigger against whatever
box runs the backend.
