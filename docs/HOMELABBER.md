# Make films on your own GPU

Vivijure's motion engine (image-to-video), running on **your** graphics card. No cloud, no per-render
bill, no account to sign up for. One command and you're rendering.

## Quickstart (you'll be rendering in minutes)

You need: an NVIDIA GPU with **16GB+ VRAM** (RTX 4060 Ti 16GB or better), **Docker**, and the
[NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
(one install so the container can see your GPU). That's it.

```sh
git clone https://github.com/skyphusion-labs/vivijure-local-backend
cd vivijure-local-backend
docker compose up
```

That's the whole setup. The stack starts your render backend, opens its own secure tunnel, downloads
the model once, and then prints a banner like this:

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
| Hardware | your own 16GB+ card | H200 / B200 class |
| Ceiling | ~768x512, ~5s clips (LTX-Video) | higher res / longer (Wan 2.2) |
| Setup | one command on your box | nothing (it's hosted) |

If you want maximum fidelity and don't mind paying, use the datacenter door. If you want to own your
pipeline and render for the cost of electricity, you're in the right place.

### Quality tiers (what your card honestly delivers)

The studio's three tiers map to settings your 16GB card can actually run. `final` here is YOUR card's
honest ceiling, not datacenter parity. Measured on a 16GB Ada card (`docs/proof/RESULTS.md`):

| Tier | Resolution | Length | Speed feel |
|---|---|---|---|
| draft | 512x320 | ~4s | fastest preview (~39s/clip) |
| standard | 704x512 | ~5s | the everyday tier (~2min/clip) |
| final | 768x512 | ~5s | best base quality (slower) |

### A stable address (named tunnel)

The quickstart uses a free quick tunnel: zero setup, but the URL changes each restart. When you want a
**stable hostname** (so you set it in the studio once and forget it), create a free Cloudflare named
tunnel and put its token in `.env` as `TUNNEL_TOKEN` -- the stack uses it automatically. A stable
`LOCAL_BACKEND_TOKEN` (instead of the auto-generated one) goes in `.env` the same way.

### Troubleshooting

- **"no CUDA device" / the backend never goes LIVE:** the container can't see your GPU. Install the
  NVIDIA Container Toolkit and confirm `docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi`
  works, then `docker compose up` again.
- **A render fails with out-of-memory:** drop to a lower tier (`final` -> `standard` -> `draft`). The
  backend already uses CPU offload + VAE tiling to fit 16GB; a marginal card may need the lighter tier.
  24GB+ cards have headroom for the top tier.
- **First render is slow:** that's the one-time model download populating the cache; later renders are fast.
- **Studio can't reach it:** re-check the Backend URL + token from the banner
  (`docker compose logs ready`) match what you pasted into the studio.

### What's next

This backend runs the base LTX-Video i2v today. Coming: a higher-quality 13B tier (the 6GB of VRAM
headroom on a 16GB card makes room for it), and the rest of the studio's module system to grow into.

## License

**AGPL-3.0-only.** Yours to run, learn from, and build on. Run it as a network service and the AGPL has
you share your changes back, so it stays a commons. Not for sale, not to be resold as a SaaS.
