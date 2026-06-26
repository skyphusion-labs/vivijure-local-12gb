"""vivijure-local-backend: the local-consumer render engine (the 16GB door).

The deliberate opposite of vivijure-backend (the RunPod datacenter engine). Runs LTX-Video image-to-
video on a single consumer GPU (RTX 4060 Ti 16GB floor) and speaks the SAME i2v_clip job contract, so
the studio's local-gpu module plugs it into the unchanged control plane. See docs/architecture.md.
"""

__version__ = "0.1.0"
