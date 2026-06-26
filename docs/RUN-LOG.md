# Run-log: vivijure local-consumer render backend (the second door)

Author: Rollins (`skyphusion-rollins`). Isolated background build session.
Brief from Mackaye: build a LOCAL, consumer-GPU render engine, the deliberate opposite of the
existing RunPod datacenter backend. "One studio, two honest doors": the module-host hook contract
makes backends pluggable; the control plane is unchanged; the USER picks local-consumer vs
RunPod-datacenter. Conform to the EXISTING hook contract, do not change it.

Hardware floor (Conrad DECIDED): 16 GB / RTX 4060 Ti. 16GB is the design MINIMUM and quality target,
NOT the 8GB variant. Bigger cards (24GB+) just get headroom.

SPEND GATE (hard): no paid GPU for live benchmarking without flagging Conrad first. Research +
scaffold + dry fit analysis need ZERO spend; do all of that now.

---

## Recon findings (the contract is law; I conform)

Read cold before writing any code:

- `vivijure/src/modules/types.ts` -- the SACRED contract, `vivijure-module/2` (`/1` accepted
  transitionally). The hook I serve is `motion.backend` (pick_one): `MotionBackendInput { shot_id,
  keyframe_url, keyframe_key?, prompt, seconds }` -> `MotionBackendOutput { shot_id, clip_key, fps,
  frames }`. Async pattern: `/invoke` -> `{ ok, pending, poll }`, then `/poll` until terminal,
  `/cancel` for in-flight (the GC discipline, #327/#328).
- `vivijure/src/modules/conformance.ts` -- `checkHookOutput("motion.backend", o)` requires
  `shot_id` (str), `clip_key` (str), `fps` (num), `frames` (num). My output must pass this.
- `vivijure/docs/module-api.md` + `module-authoring.md` -- a module is a standalone CF Worker, the
  4-file template (wrangler.toml, src/contract.ts vendored, src/<logic>.ts, src/index.ts). Reached
  over a `MODULE_<NAME>` service binding OR HTTP. HARD RULE: no public surface (workers_dev=false,
  no route); the service binding IS the auth. "Where it does the work (its own GPU, a cloud
  provider, a CPU container) is the module's business." <- this is the seam a local backend uses.
- `vivijure-backend` -- the datacenter engine (Python, RunPod serverless). i2v is Wan 2.2 A14B, a
  two-expert (14B + 14B) MoE, runs on H200/B200. WILL NOT FIT 16GB. The RunPod handler
  (`harness/handler.py`) dispatches on `action`: render / i2v_clip / finish_clip, pointer-only
  returns, shared-R2 transport (backend reads keyframe by key, writes clip by key).
- `vivijure/modules/own-gpu/` -- THE reference module for me. A `motion.backend` worker that submits
  `i2v_clip` to a RunPod endpoint and polls `/status/{id}` with the #141 GC-grace window. CRUCIAL:
  "own-gpu" still runs on a RunPod endpoint the user PROVISIONS -- it is "own keys, own endpoint,"
  NOT local silicon. So the brief's homelab-4060Ti door is genuinely new. I mirror own-gpu's bridge
  (`buildI2vBody`, `readOutput`, `encodePoll`/`decodePoll`, `runpodJobGone`, `classifyGoneState`)
  and add `/cancel` (mirrored from `modules/keyframe/`, the one cancelable reference).

## Integration constraint found (and honored), #124 tier-drift guard

`tests/quality-tier-drift.test.ts`: the core INJECTS `quality` into every motion.backend module, and
`validateConfig` SILENTLY DROPS an injected enum value not in the field's `values`. So my module's
`quality` enum MUST be exactly `draft/standard/final` (the core `QUALITY_TIERS`), or the chosen tier
silently drops and the shot renders at the schema default. Resolution: keep the SAME tier vocabulary
(the core owns it), map each tier to LTX engine configs a 16GB card can HONESTLY deliver. "final" on
local = the card's honest ceiling, NOT datacenter parity. This is the backend's own config->engine
mapping (same as Wan's tier->steps), not a contract bend. Documented in `config.py` + the model-
selection doc. My own test asserts the enum stays == draft/standard/final independently.

## Honesty check (the clip-vs-audio-shoehorn discipline): does this fit the contract?

YES, cleanly. `motion.backend` IS keyframe->clip i2v; LTX-Video on a 4060 Ti fits it with no bend.
The only tension was tier honesty (above), resolved without touching the contract. No STOP needed.

---

## Architecture decided: two honest doors, one contract

```
DATACENTER door (exists):
  control plane --service binding--> own-gpu / alibaba-wan (CF worker) --/run--> RunPod --> vivijure-backend (Wan 2.2, H200/B200)

LOCAL-CONSUMER door (this build):
  control plane --service binding--> local-gpu (CF worker, NEW) --/run--> local box (tunnel) --> vivijure-local-backend (LTX-Video, RTX 4060 Ti 16GB)
```

Two halves, mirroring the real datacenter split (CF worker in `vivijure/modules/`, engine in its own
repo):
1. `vivijure/modules/local-gpu/` -- a `motion.backend` CF worker, near-clone of own-gpu, but its
   base URL is the user's box (`LOCAL_BACKEND_URL` secret + optional `LOCAL_BACKEND_TOKEN`), and it
   is `cancelable: true`. Conformant + fully unit-testable now (no GPU, no spend). MY primary lane.
2. `vivijure-local-backend/` -- the consumer engine. To keep the CF bridge a near-clone of own-gpu,
   the local server exposes a RunPod-COMPATIBLE job API: `POST /run` -> `{id}`, `GET /status/{id}`,
   `POST /cancel/{id}`, plus `GET /health` and the `{"selftest": true}` harness. Long-running (not
   serverless): it owns an in-process async job registry. Engine = LTX-Video scoped to 16GB.

The control plane is UNCHANGED: it just discovers another `motion.backend` module. Binding/tunnel
wiring is Strummer's lane; I deliver the backend + the module + the conformance test.

## Scope of this scaffold

- CF module worker (`local-gpu`): COMPLETE + conformant + unit-tested (my lane, zero spend).
- Python engine: real scaffold. Pure logic (VRAM budget, frame math, tier->engine config, job
  registry, server routing) is CPU-tested. The torch/diffusers generation body is DEFERRED and
  pod-validated, exactly as vivijure-backend defers its torch body (`i2v.animate`).
- Docs: model-selection (dry comparison + recommendation), architecture, live-benchmark plan.

---

## Progress

- [x] Read contract, conformance, module-api, authoring, vivijure-backend i2v + handler, own-gpu,
      keyframe (/cancel), tier-drift guard. (recon complete)
- [x] Research the 4 i2v candidates on 16GB (VRAM/license/speed/quality). LTX-Video recommended.
- [x] Build CF module worker `local-gpu` (contract.ts, i2v.ts, index.ts, wrangler.toml, README, test).
- [x] Build Python engine scaffold (contract, config, vram, i2v_ltx, jobs, server, r2) + CPU tests.
- [x] Write i2v-model-selection.md (dry table + recommendation) from research.
- [x] Write architecture.md + live-benchmark-plan.md.
- [x] Verify: vitest green (local-gpu 17/17 + tier-drift 5/5; full suite 1074 pass). typecheck green.
- [x] Verify: pytest green (33/33 CPU tests). py_compile clean.
- [ ] Module PR -> vivijure under rollins. Backend committed locally (repo-creation pending topology OK).

## RESULT (this session)

Deliverable 1 (dry i2v comparison + recommendation): docs/i2v-model-selection.md. LTX-Video wins
fit+speed+license on the 16GB floor; CogVideoX-5B-I2V is the quality-but-slow runner-up (a future
second local module). Sources cited.

Deliverable 2 (scaffold wired to the EXISTING contract, control plane unchanged):
  - `vivijure/modules/local-gpu/` -- a conformant, cancelable motion.backend CF worker. 17/17 unit +
    conformance tests pass; the core tier-drift guard still passes; full vivijure suite + typecheck green.
  - `vivijure-local-backend/` -- LTX engine + RunPod-compatible local job server, 16GB-scoped, honest
    tiers. 33/33 CPU tests pass. GPU body deferred + pod-validated behind the spend gate.

Deliverable 3 (costed live-benchmark plan, NOT executed): docs/live-benchmark-plan.md. ~2-4 GPU-hours,
~$1-5 on RunPod RTX 2000 Ada 16GB (the closest Ada/16GB/fp8 proxy), or $0 on Conrad's own 4060 Ti.
Flagged for Conrad; no paid GPU spun up.

## Decisions flagged for Conrad / Mackaye

1. REPO TOPOLOGY (lead call): I scaffolded the engine as a NEW repo `vivijure-local-backend` and the
   CF worker under `vivijure/modules/local-gpu/`, mirroring the datacenter split. Alternative was a
   "consumer profile" inside vivijure-backend (less duplication, since keyframe/LoRA/finish/assemble
   already fit 16GB per the brief). I went new-repo for a clean second door; Mackaye to confirm.
2. SPEND GATE: a live LTX benchmark on real 16GB silicon is the ONLY thing that can finalize the
   tier->engine numbers (resolution/frames/steps ceilings). NOT executed. Costed plan in
   docs/live-benchmark-plan.md, flagged for Conrad.
</invoke>

---

## Final status (session close)

- Module PR (the local-consumer door, vivijure repo): https://github.com/skyphusion-labs/vivijure/pull/380
  (branch rollins/local-gpu-motion-backend, under skyphusion-rollins). Full vivijure suite green
  (1074 pass), typecheck clean, #124 tier-drift guard intact.
- Backend engine: committed locally on `main` in /home/rollins/dev/vivijure-local-backend (33/33 CPU
  tests green, py_compile clean). NOT pushed to a remote yet: repo topology (new repo vs a consumer
  profile inside vivijure-backend) is a flagged LEAD decision -- I did not create an org repo
  unilaterally. To publish once Mackaye/Conrad confirm topology:
    sudo -u rollins env -u GITHUB_TOKEN gh repo create skyphusion-labs/vivijure-local-backend \
      --private --source /home/rollins/dev/vivijure-local-backend --remote origin --push
- Spend gate: HONORED. No paid GPU spun up. The live LTX benchmark is costed + flagged in
  docs/live-benchmark-plan.md (~2-4 GPU-hrs, ~$1-5 on RunPod RTX 2000 Ada 16GB, or $0 on Conrad's own
  4060 Ti).
