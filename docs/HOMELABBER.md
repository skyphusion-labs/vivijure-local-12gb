# Make films on your own GPU

Vivijure's motion engine (image-to-video), running on **your** graphics card. No cloud GPU, no
per-render bill. One setup step (your studio's R2 storage credentials), one command, and you're
rendering.

## Quickstart (you'll be rendering in minutes)

You need: an NVIDIA GPU with **12GB+ VRAM** (RTX 3060 12GB, RTX 4070 / 4070 Ti, or better), an
NVIDIA driver **550 or newer**, **Docker**, the
[NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
(one install so the container can see your GPU), and about **25GB of free disk** (container image +
model weights). That's it.

ONE setup step before you start: your Vivijure studio's Cloudflare R2 credentials (this backend shares
that bucket -- it reads the keyframe and writes the finished clip there). Get them from the Cloudflare
dashboard -> R2 -> Manage R2 API Tokens, scoped to your bucket.

```sh
git clone https://github.com/skyphusion-labs/vivijure-local-12gb
cd vivijure-local-12gb
cp .env.example .env
# edit .env: set R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY (R2_BUCKET defaults to "vivijure")
docker compose up
```

`docker compose up` PULLS the prebuilt image from GHCR, so there is no long local build -- you go
straight to rendering. (Prefer to build from source? `docker compose up --build`.)

Updating: `docker compose up` PULLS the image once, then `pull_policy: missing` means it never
re-pulls on its own -- no surprise auto-updates. To move to a newer release, pull it explicitly with
`docker compose pull`, then `docker compose up -d`.

(Forgot the R2 creds? The backend prints a plain message telling you exactly what to set -- not a stack
trace -- and you just run `docker compose up` again.)

That's the whole setup. The stack starts your render backend, opens its own secure tunnel, and
prints a banner like this:

```
================================================================
  Vivijure local backend is LIVE

  Backend URL:    https://quiet-meadow-1234.trycloudflare.com
  Backend token:  3f9a... (your unique token)

  -> Paste these into your Vivijure studio's "Local (your GPU)" door
================================================================
```

**Copy those two values into your Vivijure studio's "Local (your GPU)" door, pick it, and render.**
A real clip comes back from your own card. That's it -- you just made a film on your own GPU.

One honest heads-up: your **first render** also downloads the LTX-Video weights (~10GB, one time), so
it takes several extra minutes. Later renders skip the download.

(No tunnel to configure, no account, no networking to understand. The tunnel is built in and invisible;
the URL + token in the banner are all you touch.)

---

## Go deeper (optional -- when you're curious)

Everything below is opt-in. You already have the thing working; this is room to grow.

### What you just did, and the honest trade-off

You ran the **local door** -- rendering on hardware you own. The studio also has a **datacenter door**
(rented top-end GPUs by the second). Both make films; the trade is real and we're upfront about it:

| | Local door (you) | Datacenter door |
|---|---|---|
| Cost | **Free after hardware** (your power) | Pay per render second |
| Hardware | your own 12GB+ card | H200 / B200 class |
| Ceiling | ~768x512, ~5s clips (LTX-Video) | higher res / longer (Wan 2.2) |
| Setup | one command on your box | nothing (it's hosted) |

If you want maximum fidelity and don't mind paying, use the datacenter door. If you want to own your
pipeline and render for the cost of electricity, you're in the right place.

### Quality tiers (what your card honestly delivers)

The studio's three tiers map to settings your 12GB card can actually run. `final` here is YOUR card's
honest ceiling, not datacenter parity. Measured on the shipped container under an 11GB VRAM cap, the
honest 12GB budget (`docs/proof/RESULTS.md`):

| Tier | Resolution | Length | Speed feel |
|---|---|---|---|
| draft | 512x320 | ~4s | fastest preview (~49s/clip) |
| standard | 704x512 | ~5s | the everyday tier (~2.2min/clip) |
| final | 768x512 | ~5s | best base quality (~2.9min/clip) |

### A stable address (named tunnel)

The quickstart uses a free quick tunnel: zero setup, but the URL changes each restart. When you want a
**stable hostname** (so you set it in the studio once and forget it), create a free Cloudflare named
tunnel and switch the tunnel to it with a small `docker-compose.override.yml` next to the compose file
(Docker Compose merges an override file automatically, so you never edit the tracked `docker-compose.yml`):

```yaml
services:
  cloudflared:
    command: ["tunnel", "run"]
```

Then put the named tunnel's token in `.env` as `TUNNEL_TOKEN` (cloudflared reads it automatically for
`tunnel run`). A stable `LOCAL_BACKEND_TOKEN` (instead of the auto-generated one) goes in `.env` the
same way.

### Sharing your GPU (cap the VRAM)

If this card is also driving your display, running another model, or you just want to leave headroom
for other work, you can bound how much VRAM vivijure is allowed to take. Set `VIVIJURE_MAX_VRAM_GB` in
`.env` to the maximum in GB, and the backend pins itself to that slice of the card at startup -- it can
never grab the whole thing.

```sh
# on a 12GB card, keep vivijure under 11GB and leave ~1GB for everything else
VIVIJURE_MAX_VRAM_GB=11
```

Leave it blank to use the whole card (the default). A value at or above your card's real size is the
same as leaving it blank. Note the cap is a ceiling, not a discount: if you set it below what a tier
actually needs, that tier will OOM -- drop to a lower tier (`final` -> `standard` -> `draft`) or raise
the cap. The startup log prints the applied cap, e.g. `VRAM capped to 11.0GB (0.917 of 12.0GB)`.

### Troubleshooting

- **"no CUDA device" / the backend never goes LIVE:** the container can't see your GPU. Install the
  NVIDIA Container Toolkit and confirm `docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi`
  works, then `docker compose up` again.
- **A render fails with out-of-memory:** drop to a lower tier (`final` -> `standard` -> `draft`). The
  backend already uses CPU offload + VAE tiling to fit 12GB; a marginal card may need the lighter tier.
  24GB+ cards have headroom for the top tier.
- **First render is slow:** that's the one-time model download (~10GB) populating the cache; expect
  several extra minutes on the first render only. Later renders are fast.
- **Studio can't reach it:** re-check the Backend URL + token from the banner
  (`docker compose logs ready`) match what you pasted into the studio.

### What's next

This backend runs the base LTX-Video i2v today. Coming: a higher-quality 13B tier (which 16GB+ cards
have the VRAM headroom for), and the rest of the studio's module system to grow into.

## License

**AGPL-3.0-only.** Yours to run, learn from, and build on. Run it as a network service and the AGPL has
you share your changes back, so it stays a commons. Not for sale, not to be resold as a SaaS.
