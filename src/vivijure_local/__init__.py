"""vivijure-local-12gb: the local-consumer render engine (the 12GB door).

The deliberate opposite of vivijure-backend (the RunPod datacenter engine). Runs LTX-Video image-to-
video on a single consumer GPU (a 12GB floor, proven) and speaks the SAME i2v_clip job contract, so
the studio's local-gpu module plugs it into the unchanged control plane. See docs/architecture.md.
"""

__version__ = "1.0.1"
