# Every knob, in plain English

This is the full list of every setting in `vivijure-local-12gb`. You should never have to open the
compose file or the source code to learn what a setting does. If a knob exists, it is on this page,
with what it is, why it is there, and an example.

There are three groups:

1. **The `.env` settings you fill in** -- the ones you touch. All optional except the R2 keys.
2. **Built-in settings** -- set for you inside `docker-compose.yml`. You do not need to change these,
   but they are listed so nothing is hidden.
3. **Per-clip settings** -- sent by the Studio with each render request, not set by you.

Plus the **ports**, the **volumes**, and the **quality tiers** at the end.

---

## 1. The `.env` settings you fill in

Copy `.env.example` to `.env` and fill these in. Only the R2 keys are required.

### `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`
- **What it is:** your Cloudflare R2 storage keys.
- **Why:** this backend shares one storage bucket with your Studio. It reads the starting picture (the
  "keyframe") from that bucket and writes the finished clip back to it. That is the only way bytes move,
  so these keys are the one credential the backend needs.
- **Required?** Yes, for real renders. Without them the container starts, then tells you in plain words
  which values are missing.
- **Where to get them:** Cloudflare dashboard -> R2 -> Manage R2 API Tokens. Scope the token to your
  bucket only (read the keyframes, write the clips). Do not reuse an admin key.
- **Example:**
  ```
  R2_ACCOUNT_ID=1a2b3c4d5e6f7890abcdef1234567890
  R2_ACCESS_KEY_ID=6b1f...
  R2_SECRET_ACCESS_KEY=9e2c...
  ```

### `R2_BUCKET`
- **What it is:** the name of that shared bucket.
- **Why:** it must match the bucket your Studio uses. Almost everyone leaves it at the default.
- **Required?** No.
- **Default:** `vivijure`
- **Example:** `R2_BUCKET=vivijure`

### `LOCAL_BACKEND_TOKEN`
- **What it is:** a secret password that every render request must carry.
- **Why:** your backend is reachable over a public web address (the tunnel, below). The password stops a
  stranger from using your graphics card. The render endpoint refuses to work without it.
- **Required?** No. If you leave it blank, the container makes a strong random one for you at startup and
  prints it in the ready banner. Set your own only if you want the same password to survive a restart.
- **Default:** auto-generated (a fresh random one each restart).
- **Example:** `LOCAL_BACKEND_TOKEN=` (blank, recommended for a first run) or a fixed value from
  `openssl rand -hex 32`.

### `TUNNEL_TOKEN`
- **What it is:** a Cloudflare "named tunnel" token, for a web address that never changes.
- **Why:** by default the backend gets a free throwaway web address (a TryCloudflare quick tunnel) that
  changes every time you restart. That is perfect for a first try. If you run this backend all the time,
  a named tunnel gives you one fixed address you can paste into the Studio once and forget.
- **Required?** No.
- **Default:** blank, which means a zero-setup quick tunnel.
- **Example:** `TUNNEL_TOKEN=` (blank, recommended for a first run) or your named-tunnel token.

### `VIVIJURE_MAX_VRAM_GB`
- **What it is:** a cap, in gigabytes, on how much graphics memory (VRAM) this backend is allowed to use.
- **Why:** if you share the card with something else (your desktop display, a game, another AI model),
  this stops the backend from grabbing the whole card. It pins the render to that fraction of the card at
  startup.
- **Required?** No.
- **Default:** blank, which means use the whole card.
- **Example:** on a 12GB card, `VIVIJURE_MAX_VRAM_GB=11` leaves about 1GB for everything else. A number
  as big as or bigger than your card is the same as leaving it blank.

### `VIVIJURE_OFFLOAD`
- **What it is:** how the render trades speed for graphics memory (VRAM). Three modes:
  `none` (keep the whole model resident on the GPU: fastest, no per-step shuffling, but needs a big
  card), `model` (page whole pieces of the model to system RAM between uses), `sequential` (page
  piece-by-piece: slowest, smallest footprint, what the heavy `final` (13B) tier uses to fit 12GB).
- **Why:** on a big card (roughly 20GB or more) the per-tier default shuffles the model on and off the
  GPU even though the card could hold more of it. Setting `none` runs resident and skips that
  shuffling, so each clip renders faster; `model` is a middle ground for a mid-size card.
- **Required?** No.
- **Default:** blank, which keeps each quality tier own safe setting (draft/standard page whole pieces,
  final pages piece-by-piece for the 12GB fit). Nothing changes unless you set this.
- **Applies to:** every tier at once (draft, standard, final).
- **Example:** `VIVIJURE_OFFLOAD=none` on a 20GB+ card renders resident (faster). On a 12GB card leave
  it blank -- forcing `none` there runs out of memory, especially on the 13B `final` tier.
- **Bad value:** the backend refuses to start and tells you the valid modes, rather than quietly using
  the default. Fix the value (or unset it) and start again.

---

## 2. Built-in settings (set for you in `docker-compose.yml`)

You do not set these. They live in the compose file so the pieces find each other. Listed here so
nothing about the running system is a mystery.

### `HOST`
- **What it is:** the network address the render server listens on inside its container.
- **Default:** `0.0.0.0` (all addresses inside the private container network).
- **Why fixed:** the tunnel container reaches the server over the private compose network; this must
  stay open inside that network. It is not exposed to your real network (see Ports).

### `PORT`
- **What it is:** the port the render server listens on inside the container.
- **Default:** `8000`.
- **Why fixed:** the tunnel and the healthcheck both point at `8000`. Changing it means changing them too.

### `HF_HOME`
- **What it is:** where the model files (the LTX-Video weights) are cached inside the container.
- **Default:** `/models/hf`.
- **Why fixed:** it points at the `vivijure_models` volume (below) so the multi-gigabyte weights are
  downloaded once (during your FIRST render) and reused after, instead of re-downloading each time.

### `ANNOUNCE_BACKEND`
- **What it is:** the internal address the "ready" banner service checks for health before it prints your
  connect details.
- **Default:** `http://vivijure-local-12gb:8000`.
- **Why fixed:** it is the container name plus the internal port; the banner waits for this to answer
  before it tells you the backend is ready.

---

## 3. Per-clip settings (sent by the Studio, not by you)

Each render the Studio sends carries a small `config`. You do not edit these; the Studio and the
`local-gpu` module fill them in. They are documented so you know exactly what the backend accepts and
how it protects your card. Any value is clamped to what the chosen tier can honestly fit -- a request can
narrow a tier but never push the card past its limit.

| Setting | What it is | Default | Notes |
|---|---|---|---|
| `quality` | Which tier to render: `draft`, `standard`, or `final`. | `standard` | Picks the resolution / frames / steps from the tier table below. An unknown value falls back to `standard`. |
| `num_frames` | How many frames (clip length). | the tier's ceiling | Capped at the tier ceiling, then snapped to LTX's frame stride (8k+1). |
| `fps` | Playback speed, frames per second. | `24` | Clamped to the 8--30 range. |
| `seed` | The random seed, for repeatable output. | `-1` (random) | Same seed + same inputs = the same clip. |
| `flow_shift` | An LTX sampling knob that trades motion smoothness against sharpness. | `5.0` | Advanced; the Studio rarely changes it. |
| `negative_prompt` | Text describing what you do NOT want in the clip. | empty | Optional. |
| `width` / `height` | Clip size in pixels. | the tier's size | Clamped down to the tier size; never widened past the honest fit. |

---

## Ports

| Port | Where | Open to your network? | Why |
|---|---|---|---|
| `8000` | inside the container only (`expose`, not `ports`) | **No** | The render server listens here. Nothing is published to your computer's real network. The only way in is the Cloudflare tunnel, which reaches `8000` over the private compose network. This is deliberate: a graphics-card render endpoint left open to the internet is an invitation to run up your electricity bill. |

If you ever DO want a local port for testing on the same machine, add a `ports:` entry mapped to
`127.0.0.1` only. The default ships with none, which is the safe choice.

## Volumes (saved data)

| Volume | Mounted at | Holds | Why it persists |
|---|---|---|---|
| `vivijure_models` | `/models` | the downloaded LTX-Video model weights (~10GB, pulled during your first render) | So the big download happens once, not again. |
| `vivijure_runtime` | `/shared` | the generated token and the tunnel's log line | So the "ready" banner can read the token and the tunnel web address and print them for you. |

To start completely fresh (re-download models, new token), remove these with
`docker compose down -v`.

## The quality tiers

The Studio's tier names map to LTX settings a 12GB card can honestly deliver. `final` is the card's
honest ceiling, not datacenter quality. All tiers use LTX-Video with model-CPU-offload plus VAE tiling,
which is what keeps the peak memory flat, so higher tiers cost time, not memory. These numbers were
measured on the real shipped container at a 12GB budget (see `docs/proof/RESULTS.md`).

| Tier | Resolution | Frames | Steps | Peak VRAM (11GB cap) | sec/clip |
|---|---|---|---|---|---|
| `draft` | 512x320 | 97 | 25 | ~9.76 GB | 48.6s |
| `standard` | 704x512 | 121 (~5s) | 40 | ~9.78 GB | 132.0s |
| `final` | 768x512 | 121 (~5s) | 50 | ~9.78 GB | 171.6s |

---

*Every setting in this repo is on this page. If you find one that is not, that is a documentation bug --
please open an issue.*
