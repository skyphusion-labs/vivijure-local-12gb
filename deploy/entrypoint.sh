#!/bin/sh
# Container entrypoint. Normally a transparent passthrough to the server CMD (below). It exists ONLY to
# host the interim, build-arg-gated out-of-band SSH described in deploy/Dockerfile: when the image was
# built with --build-arg INCLUDE_SSH=1 (the flagged pod-test image, NOT the shipped/CI image) AND RunPod
# injected a PUBLIC_KEY, it enables key-only sshd so an operator can read the ephemeral tunnel URL +
# auto-generated token off a disposable RunPod pod. In every normal build INCLUDE_SSH=0, so this is a
# no-op and the container just runs its CMD. Interim; see vivijure-local-12gb issue #34 (strip at the
# next release once a designed-in retrieval exists).
set -e
if [ "${INCLUDE_SSH:-0}" = "1" ] && [ -n "${PUBLIC_KEY:-}" ] && command -v sshd >/dev/null 2>&1; then
  mkdir -p /root/.ssh && chmod 700 /root/.ssh
  printf "%s\n" "$PUBLIC_KEY" >> /root/.ssh/authorized_keys
  chmod 600 /root/.ssh/authorized_keys
  /usr/sbin/sshd && echo "entrypoint: key-only sshd started (INCLUDE_SSH=1)"
fi
exec "$@"
