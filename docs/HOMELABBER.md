# Run Vivijure on your own GPU (the homelabber run-story)

You have a GPU. Why rent one to make films? This is the **local door**: the Vivijure motion engine
(image-to-video) running on **your own card**, in your own homelab, for free after the hardware. You
point your Vivijure studio at it and the "Local (your GPU)" door lights up.

This is the honest opposite of the datacenter door (RunPod, top-end cards by the second). The trade is
real and we are upfront about it:

| | Local door (this) | Datacenter door (RunPod) |
|---|---|---|
| Cost | **Free after hardware** (your power) | Pay per render second |
| Hardware | your own 16GB+ card (RTX 4060 Ti floor) | H200 / B200 class |
| Ceiling | ~768x512, ~5s clips, LTX-Video | higher res / longer, Wan 2.2 |
| Setup | one container on your box | nothing (it's hosted) |

If you want maximum fidelity and don't mind paying, use the datacenter door. If you want to own your
pipeline end to end and render for the cost of electricity, this is for you.

## What you need

- An NVIDIA GPU with **16GB VRAM or more** (the RTX 4060 Ti 16GB is the design floor; bigger cards get
  headroom). 8GB is not enough for a quality render.
- **Docker** + the **NVIDIA Container Toolkit** (lets the container see your GPU):
  https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html
- Your Vivijure studio's **R2 bucket** credentials (a per-function key scoped to that bucket).
- That's it for the quick start: the tunnel is BUNDLED (TryCloudflare quick tunnel -- no Cloudflare
  account, no domain). For a stable hostname later, a free Cloudflare account + a named-tunnel token.

## Stand it up (one command after config)

```sh
git clone https://github.com/skyphusion-labs/vivijure-local-backend
cd vivijure-local-backend
cp .env.example .env          # fill in your R2 creds (LOCAL_BACKEND_TOKEN auto-generates if blank)
docker compose up -d          # starts the backend + a Cloudflare tunnel; first boot caches LTX weights
docker compose logs cloudflared             # -> https://<random>.trycloudflare.com   (your LOCAL_BACKEND_URL)
docker compose logs vivijure-local-backend  # -> the auto-generated LOCAL_BACKEND_TOKEN (if you left it blank)
```

The first boot downloads the LTX-Video weights once into a named volume (`vivijure_models`), so
restarts and upgrades are instant. The backend is NOT published on the LAN -- the bundled tunnel is
the only way in, and it hard-rejects any i2v request without your `LOCAL_BACKEND_TOKEN`.

## Point your studio at it

1. Take the `https://<random>.trycloudflare.com` URL from `docker compose logs cloudflared` -- that is
   your `LOCAL_BACKEND_URL`. (For a stable hostname, set `TUNNEL_TOKEN` in `.env` for a named tunnel.)
2. In your studio, seed the `local-gpu` module secrets and bind it (full steps in
   [INTEGRATION.md](./INTEGRATION.md)): `LOCAL_BACKEND_URL` = the tunnel URL,
   `LOCAL_BACKEND_TOKEN` = the value you set (or the one the backend logged).
3. The "Local (your GPU)" door now appears in the planner's backend picker. Pick it and render.

## Quality tiers (what your card honestly delivers)

The studio's three tiers map to LTX configs your 16GB card can actually run. `final` here is YOUR
card's honest ceiling, not datacenter parity:

| Tier | Model | Resolution | Length | Speed feel |
|---|---|---|---|---|
| draft | LTX-Video (base) | 512x320 | ~4s | fastest preview (~39s/clip) |
| standard | LTX-Video (base) | 704x512 | ~5s | the everyday tier (~2min/clip) |
| final | LTX-Video (base) | 768x512 | ~5s | best base quality (slower) |

(VALIDATED on a 16GB Ada card -- see `docs/proof/RESULTS.md`: peak ~10.4GB, no OOM, ~6GB headroom;
draft ~39s/clip, standard ~2min/clip. Real sample clips are in `docs/proof/`.)

## Troubleshooting

- **`/health` never goes green / "no CUDA device":** the container can't see the GPU. Install the
  NVIDIA Container Toolkit and confirm `docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi`
  works.
- **A render fails with out-of-memory:** drop to a lower tier (`final` -> `standard` -> `draft`); the
  backend already uses CPU offload + VAE tiling to fit 16GB, but a marginal card may need the lighter
  tier. Bigger cards (24GB+) have headroom for the top tier.
- **First render is slow:** that is the one-time weight pull populating the cache volume; subsequent
  renders are fast.
- **Studio can't reach it:** check the tunnel is up (`curl` your tunnel hostname `/health`) and that
  `LOCAL_BACKEND_TOKEN` matches between your `.env` and the seeded module secret.

## License

**AGPL-3.0-only.** Yours to run, learn from, and build on. Run it as a network service and the AGPL has
you share your changes back, so it stays a commons. Not for sale, not to be resold as a SaaS.
