#!/usr/bin/env bash
# Build everything an egress-less x86_64 Debian 13 VM needs to run the SusVibes SWE-agent suite,
# on a machine with internet and Docker (works on Apple Silicon via amd64 emulation).
#
#   bash build_bundle.sh <out_dir> <vm_dpkg_status>
#
# <vm_dpkg_status> is a copy of the VM's /var/lib/dpkg/status. apt resolves the Debian packages
# against it, so the bundle holds exactly the .debs that VM is missing, at versions that fit it.
# Install on the VM with install_bundle.sh (copied into the bundle). Task images ship separately
# (ship_images.py).
set -euo pipefail

OUT="$(mkdir -p "$1" && cd "$1" && pwd)"
STATUS="$(cd "$(dirname "$2")" && pwd)/$(basename "$2")"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUSVIBES="$(cd "$HERE/../../.." && pwd)"
SWEAGENT="$(cd "$SUSVIBES/../SWE-agent" && pwd)"
BRANCH="$(git -C "$SUSVIBES" rev-parse --abbrev-ref HEAD)"
DEBIAN_IMAGE="debian:trixie"
CONDA_IMAGE="condaforge/miniforge3:latest"
PLATFORM="linux/amd64"

log() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
mkdir -p "$OUT/debs" "$OUT/envs" "$OUT/repos"

log "Debian packages (docker.io, iptables, git, tmux) resolved against the VM's dpkg status"
docker run --rm --platform "$PLATFORM" -v "$OUT/debs:/out" -v "$STATUS:/vm-status:ro" "$DEBIAN_IMAGE" bash -euc '
    apt-get update -q
    apt-get install -y -q --download-only --no-install-recommends \
        -o Dir::State::status=/vm-status -o Dir::Cache::archives=/out \
        docker.io iptables git tmux
    rm -rf /out/partial /out/lock'
ls "$OUT/debs" | wc -l | xargs echo "debs:"

log "Miniforge installer"
[ -f "$OUT/Miniforge3-Linux-x86_64.sh" ] || curl -fsSL -o "$OUT/Miniforge3-Linux-x86_64.sh" \
    "https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh"

log "Conda envs (sv, sweagent1.1.0), packed with conda-pack"
docker run --rm --platform "$PLATFORM" -v "$OUT/envs:/out" \
    -v "$SUSVIBES:/src/susvibes:ro" -v "$SWEAGENT:/src/SWE-agent:ro" "$CONDA_IMAGE" bash -euc '
    # requirements.txt pins psutil==5.9.0, which has no cp311 wheel and builds from source.
    apt-get update -q && apt-get install -y -q gcc >/dev/null
    conda install -y -q -n base conda-pack
    conda create -y -q -n sv python=3.11
    conda run -n sv pip install -q -r /src/susvibes/requirements.txt pytest setuptools wheel
    conda create -y -q -n sweagent1.1.0 python=3.11
    # SWE-agent itself is installed editable on the VM; here only its dependencies.
    conda run -n sweagent1.1.0 pip install -q setuptools wheel
    # Built from a writable copy: pip writes egg-info into the source tree.
    mkdir -p /tmp/swe && tar -C /src/SWE-agent --exclude=.venv --exclude=.git -cf - . | tar -C /tmp/swe -xf -
    conda run -n sweagent1.1.0 pip install -q /tmp/swe
    conda run -n sweagent1.1.0 pip uninstall -q -y sweagent
    for env in sv sweagent1.1.0; do
        # requirements.txt pins pip/setuptools/wheel, so pip replaces the conda copies; pack what pip installed.
        conda-pack -q -n "$env" -o "/out/$env.tar.gz" --force --ignore-missing-files
    done'

log "Repositories"
git -C "$SUSVIBES" bundle create "$OUT/repos/susvibes.bundle" "$BRANCH"
echo "$BRANCH" > "$OUT/repos/susvibes.branch"
git -C "$SWEAGENT" archive --format=tar.gz -o "$OUT/repos/SWE-agent-v1.1.0.tar.gz" v1.1.0
cp "$HERE/install_bundle.sh" "$OUT/"

log "Done"
du -sh "$OUT"/* | sort -h
