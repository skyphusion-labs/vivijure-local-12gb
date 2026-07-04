# Changelog

All notable changes to vivijure-local-12gb are recorded here. This project follows SemVer-style
`0.MINOR.PATCH` while pre-1.0 (PATCH for fixes and backend tweaks, MINOR for features).

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
