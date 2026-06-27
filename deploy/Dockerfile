# vivijure-local-backend runtime image (the 16GB consumer door).
#
# SCAFFOLD image. torch/torchvision install from the CUDA index matched to the card's driver line; the
# exact validated pin set is fixed during the card benchmark (docs/live-benchmark-plan.md), mirroring
# how vivijure-backend pins its pod-validated set. Built to run on the user's own box (a 4060 Ti is Ada
# / CUDA 12.x), NOT on RunPod. Run it via docker-compose.yml (one command) or `docker run --gpus all`.
FROM nvidia/cuda:12.4.1-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/models/hf

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# torch first, from the CUDA wheel index (Ada / cu124). Pin the exact version at benchmark time.
RUN pip3 install --index-url https://download.pytorch.org/whl/cu124 torch torchvision

COPY requirements.txt /app/requirements.txt
RUN pip3 install -r /app/requirements.txt

COPY src /app/src
ENV PYTHONPATH=/app/src \
    HOST=0.0.0.0 \
    PORT=8000

EXPOSE 8000

# Liveness: GET /health must 200. stdlib-only (no curl in the image). Mirrors the compose healthcheck
# so a bare `docker run` is self-checking too. Generous start-period: first boot pulls LTX weights.
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health',timeout=3).status==200 else 1)"

CMD ["python3", "-m", "vivijure_local.server"]
