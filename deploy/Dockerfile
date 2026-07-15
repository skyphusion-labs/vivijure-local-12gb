# vivijure-local-12gb RELEASE image (the LTX consumer door): FROM the pinned runtime base + our app code.
# Deliberately tiny to build: everything heavy (CUDA/torch, diffusers/transformers, the render deps) lives
# in the runtime base (deploy/runtime.Dockerfile, built by runtime-build.yml). So a src-only release
# re-pushes ONLY the COPY src layer below; the runtime layers are inherited by blob identity and dedup on
# GHCR. That is the publish-time + runner-cost win.
#
# RELEASE CONTRACT:
#   * src-only change        -> tag vX.Y.Z (this build; fast, ~app layer only).
#   * toolchain/deps/CUDA     -> bump runtime-t<N> + rebuild via runtime-build.yml -> repin RUNTIME_REF
#                                below (its Summary prints the exact line) -> tag vX.Y.Z.
#
# Source hackers: `docker compose up --build` builds THIS file, pulling the public runtime base FROM.
#
# RUNTIME BASE, pinned by TAG (and, once runtime-build.yml has run, by @sha256 digest for reproducible
# builds). runtime-build.yml auto-opens a PR that repins this line to tag@digest.
ARG RUNTIME_REF=ghcr.io/skyphusion-labs/vivijure-local-12gb:runtime-t1@sha256:9931f809d3b28313be8dec277344d5e4a47cfad98c6d685db2c303554e5ed517
FROM ${RUNTIME_REF}

WORKDIR /app

# Our package. This is the ONLY layer that changes on a src-only release, so it is the only re-push.
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
