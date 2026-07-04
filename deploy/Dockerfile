# vivijure-local-12gb runtime image (the 12GB consumer door).
#
# torch/torchvision install from the CUDA index matched to the card's driver line, PINNED to the set
# validated on real silicon in the S8 re-pin proof (torch 2.5.1 + torchvision 0.20.1 from cu124, paired with
# diffusers 0.38.0 / transformers 4.57.6 in requirements.txt; see SECURITY.md). Built to run on the user's own box (a
# 4060 Ti is Ada / CUDA 12.x), NOT on RunPod. Run it via docker-compose.yml or `docker run --gpus all`.
FROM nvidia/cuda:12.4.1-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/models/hf

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# torch first, from the CUDA wheel index (Ada / cu124). Pinned to the proof-validated pair.
RUN pip3 install --index-url https://download.pytorch.org/whl/cu124 torch==2.5.1 torchvision==0.20.1

COPY requirements.txt /app/requirements.txt
RUN pip3 install -r /app/requirements.txt

COPY src /app/src
ENV PYTHONPATH=/app/src \
    HOST=0.0.0.0 \
    PORT=8000

EXPOSE 8000

# --- Interim out-of-band SSH (RunPod vendor pattern), BUILD-ARG GATED -------------------------------
# For the prod local-door flip pod-test only (skyphusion-labs/vivijure #383/#384): a disposable RunPod
# SECURE pod exposes the backend via an EPHEMERAL trycloudflare quick tunnel, and the operator must read
# that random URL + the auto-generated token off the pod. RunPod pods have no exec for a custom image,
# so we bake key-only sshd -- but GATED so it can NEVER ship by default.
#
# INCLUDE_SSH defaults to 0. The CI publish lane (.github/workflows/publish.yml) builds with NO
# build-args, so the shipped :latest / :X.Y.Z image contains NO sshd -- structurally, not by memory.
# Only a deliberate `--build-arg INCLUDE_SSH=1` build (tagged :pod-test-ssh) installs openssh-server;
# the entrypoint then writes the RunPod-injected PUBLIC_KEY to authorized_keys and starts key-only sshd.
# INTERIM: strip this whole block at the next release. See vivijure-local-12gb issue #34.
ARG INCLUDE_SSH=0
ENV INCLUDE_SSH=${INCLUDE_SSH}
RUN if [ "$INCLUDE_SSH" = "1" ]; then \
      apt-get update && apt-get install -y --no-install-recommends openssh-server \
      && rm -rf /var/lib/apt/lists/* && mkdir -p /run/sshd \
      && sed -i "s/^#\?PasswordAuthentication.*/PasswordAuthentication no/" /etc/ssh/sshd_config; \
    fi
COPY deploy/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

# Liveness: GET /health must 200. stdlib-only (no curl in the image). Mirrors the compose healthcheck
# so a bare `docker run` is self-checking too. Generous start-period: first boot pulls LTX weights.
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health',timeout=3).status==200 else 1)"

CMD ["python3", "-m", "vivijure_local.core.server"]
