# Wiring the local door into the studio

How the `vivijure-local-backend` (this repo) plugs into the Vivijure studio through the `local-gpu`
motion.backend module. The control plane is UNCHANGED in code; wiring is binding + secrets + the
homelabber's running backend. Stage all of this now; flip it on the instant the benchmark proves out.

## The picture

```
studio control plane --(service binding MODULE_LOCAL_GPU)--> local-gpu module worker
   local-gpu --(POST /run i2v_clip, GET /status, POST /cancel)--> Cloudflare tunnel --> THIS backend (your 16GB GPU)
```

The `local-gpu` module (in the `vivijure` repo, `modules/local-gpu/`) is the bridge. It holds the
backend URL + optional token and speaks the same `i2v_clip` wire body as the datacenter door. This
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
Auth: if `LOCAL_BACKEND_TOKEN` is set, the module sends it as `Authorization: Bearer <token>` and the
server enforces it; unset = open (trusted-LAN tunnel only).

## The flip checklist (studio side)

1. **Module deploys automatically.** The studio CI (`.github/workflows/ci.yml`) is deploy-by-default:
   every `modules/*/wrangler.toml` deploys unless its dir is in the explicit `EXCLUDE`. `local-gpu` is
   NOT excluded, so merging its PR (#380) deploys `vivijure-module-local-gpu`. **No EXCLUDE edit
   needed** (it was never excluded).

2. **Bind it to the core.** Add a service binding to the core `wrangler.toml.example` so the registry
   discovers `local-gpu` (the registry scans env for `MODULE_*` service bindings). A `[[services]]`
   binding must point at a DEPLOYED module (else the next core deploy fails on a dangling binding), so
   add this AFTER step 1 is merged:

   ```toml
   # Local consumer GPU (LTX-Video on the homelabber's own 16GB card). The local door.
   [[services]]
   binding = "MODULE_LOCAL_GPU"
   service = "vivijure-module-local-gpu"
   ```

3. **Seed the module secrets** into the account Cloudflare Secrets Store (same store + flow as the
   RunPod modules; see the studio `docs/DEPLOYMENT.md` "Module secrets via the Secrets Store"). The
   `local-gpu` `wrangler.toml` binds these by `secret_name`:

   ```sh
   # the tunnel hostname terminating at the homelab box (no trailing slash)
   wrangler secrets-store secret create <STORE_ID> --name LOCAL_BACKEND_URL   --value "https://render.myhomelab.example"
   # the shared secret the backend checks (optional; match the backend's .env LOCAL_BACKEND_TOKEN)
   wrangler secrets-store secret create <STORE_ID> --name LOCAL_BACKEND_TOKEN --value "<openssl rand -hex 32>"
   ```

4. **The homelabber runs the backend** (this repo) and exposes it via a Cloudflare tunnel; the tunnel
   hostname is what `LOCAL_BACKEND_URL` points at. See the repo README run-story + `docker-compose.yml`.

Once 1-4 are in place the local door appears in the planner's motion.backend selector (Joan's #379
selector renders it from the manifest's `ui.locality="local"` framing) and renders end to end.

## Trust boundary (do not break)

The `local-gpu` module has NO public surface (`workers_dev=false`, no route): the studio service
binding IS its auth. The backend itself sits behind a Cloudflare tunnel; keep its published port on
`127.0.0.1` (the compose default) so it is reachable only through the tunnel, and set
`LOCAL_BACKEND_TOKEN` for defense in depth. A public render endpoint is an unauthenticated GPU-spend /
DoS trigger against the homelab box.
