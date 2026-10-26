#!/usr/bin/env bash
# Rehearse install_bundle.sh on this machine: a Debian 13 x86_64 container with networking off
# installs the bundle's envs and repos and runs the offline tests, the parts most likely to break.
# (Debian packages and Docker are skipped; the container has no systemd.)
#
#   bash rehearse_install.sh <bundle_dir>
set -euo pipefail
BUNDLE="$(cd "$1" && pwd)"
IMAGE=susvibes-rehearse:trixie
docker build -q --platform linux/amd64 -t "$IMAGE" - >/dev/null <<'DOCKERFILE'
FROM debian:trixie
RUN apt-get update && apt-get install -y -q git sudo ca-certificates && rm -rf /var/lib/apt/lists/*
RUN useradd -m -s /bin/bash rehearse && echo "rehearse ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/rehearse
USER rehearse
DOCKERFILE
docker run --rm --platform linux/amd64 --network none -e SV_SKIP_SYSTEM=1 -e USER=rehearse \
    -v "$BUNDLE:/bundle:ro" "$IMAGE" bash -c 'cp -r /bundle ~/bundle && bash ~/bundle/install_bundle.sh'
