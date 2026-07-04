# Changelog

All notable changes to vivijure-local-12gb are recorded here. This project follows SemVer-style
`0.MINOR.PATCH` while pre-1.0 (PATCH for fixes and backend tweaks, MINOR for features).

## v0.1.1 -- 2026-07-04

Fix the default quick-tunnel bring-up (compose + ready-banner fix; the render engine is unchanged).

- **cloudflared no longer crash-loops on `docker compose up`.** The `cloudflared` service wrapped its
  tunnel startup in an inline `sh -c` script, but the `cloudflare/cloudflared` image is distroless (no
  shell) and its entrypoint is `cloudflared --no-autoupdate`, so the script was passed to cloudflared as
  arguments and never ran; the default quick tunnel never started and the `ready` banner never printed a
  Backend URL. The service now invokes cloudflared natively:
  `tunnel --url http://vivijure-local-12gb:8000 --logfile /shared/cf.log`. No shell, no entrypoint override, no
  dependence on the distroless image's contents.
- **The backend pre-creates the tunnel logfile for cloudflared's nonroot UID.** `cloudflare/cloudflared`
  runs as nonroot (65532) and cannot create `/shared/cf.log` in the root-owned shared volume, so
  `--logfile` failed with permission-denied and the banner got no URL. The backend entrypoint (root) now
  pre-creates `/shared/cf.log` owned by 65532; `/shared` stays root-owned and the token stays root-only.
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
