# LTX-Video i2v benchmark -- e1525d0830ef

- GPU: **NVIDIA RTX 4000 SFF Ada Generation**
- **hard cap: 12.0GB** allocator (0.6138 of 19.55GB real total) -- overflow OOMs like a 12GB card
- keyframe: `/out/keyframe.png`  prompt: _a slow, smooth cinematic dolly-in, gentle parallax_  warm: True

| tier | model | engine | res | frames | steps | offload | peak alloc | peak reserved | fits cap | cold load | sec/clip | result |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| draft | `LTX-Video-0.9.7-distilled` | condition | 512x320 | 97 | 8 | model | - | - | - | - | None | OOM |
| standard | `LTX-Video` | base_i2v | 704x512 | 121 | 40 | model | 10.46GB | 10.49GB | yes | 305.6s | 142.2 | OK |
| final | `LTX-Video-0.9.8-13B-distilled` | condition | 768x512 | 121 | 10 | sequential | 3.23GB | 4.63GB | yes | 406.5s | 108.4 | OK |

Sample clips are written alongside this report; eyeball them for motion quality.
`peak reserved` is the allocator pool the cap governs; `fits cap` is reserved <= the cap.
A real card of the cap size also spends ~0.5-1GB on the CUDA context OUTSIDE this budget,
so a flat "runs on <cap>GB" claim for a new tier waits on a true-hardware confirmation run.
