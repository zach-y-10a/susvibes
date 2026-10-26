#!/usr/bin/env bash
# Install a build_bundle.sh bundle on the VM, without network. Idempotent.
#
#   bash install_bundle.sh [bundle_dir]     # default: the directory this script is in
#
# Creates $WORK/susvibes (git clone of the bundled branch) and $WORK/SWE-agent (v1.1.0 + patch),
# conda envs sv and sweagent1.1.0 under $CONDA_DIR, and Docker.
set -euo pipefail

BUNDLE="$(cd "${1:-$(dirname "${BASH_SOURCE[0]}")}" && pwd)"
WORK="${WORK:-$HOME/susvibes-work}"
CONDA_DIR="${CONDA_DIR:-$HOME/miniforge3}"
PATCH_REL="evaluation_harness/swe_agent/patches/sweagent-v1.1.0-optional-sampling.patch"

log() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

log "Debian packages"
if ! command -v docker >/dev/null || ! command -v git >/dev/null; then
    # The bundle holds exactly what these packages need without recommends (build_bundle.sh).
    sudo apt-get install -y -q --no-download --no-install-recommends "$BUNDLE"/debs/*.deb
fi
sudo systemctl enable --now docker
id -nG "$USER" | grep -qw docker || sudo usermod -aG docker "$USER"

log "Miniforge"
[ -x "$CONDA_DIR/bin/conda" ] || bash "$BUNDLE/Miniforge3-Linux-x86_64.sh" -b -p "$CONDA_DIR"
grep -q "conda initialize" ~/.bashrc || "$CONDA_DIR/bin/conda" init bash >/dev/null
CONDA="$CONDA_DIR/bin/conda"

log "Conda envs"
for env in sv sweagent1.1.0; do
    if [ ! -x "$CONDA_DIR/envs/$env/bin/python" ]; then
        mkdir -p "$CONDA_DIR/envs/$env"
        tar -xzf "$BUNDLE/envs/$env.tar.gz" -C "$CONDA_DIR/envs/$env"
        "$CONDA_DIR/envs/$env/bin/conda-unpack"
    fi
    # litellm otherwise fetches its price table from GitHub at import; use the copy it ships with.
    # No registry access: the eval uses the task images ship_images.py loaded instead of pulling.
    "$CONDA" env config vars set -n "$env" LITELLM_LOCAL_MODEL_COST_MAP=True SV_IMAGE_PULL=missing >/dev/null
done

log "Repositories under $WORK"
mkdir -p "$WORK"
branch="$(cat "$BUNDLE/repos/susvibes.branch")"
if [ ! -d "$WORK/susvibes/.git" ]; then
    git clone -q -b "$branch" "$BUNDLE/repos/susvibes.bundle" "$WORK/susvibes"
else
    git -C "$WORK/susvibes" pull -q --ff-only "$BUNDLE/repos/susvibes.bundle" "$branch"
fi
if [ ! -d "$WORK/SWE-agent" ]; then
    mkdir -p "$WORK/SWE-agent"
    tar -xzf "$BUNDLE/repos/SWE-agent-v1.1.0.tar.gz" -C "$WORK/SWE-agent"
    (cd "$WORK/SWE-agent" && git apply "$WORK/susvibes/$PATCH_REL")  # not a git repo; applies like patch -p1
fi
grep -q 'top_p = "None"' "$WORK/SWE-agent/sweagent/agent/models.py" || { echo "SWE-agent patch missing"; exit 1; }

log "Editable installs (offline)"
"$CONDA" run -n sv pip install -q --no-index --no-deps --no-build-isolation -e "$WORK/susvibes"
"$CONDA" run -n sweagent1.1.0 pip install -q --no-index --no-deps --no-build-isolation -e "$WORK/SWE-agent"
"$CONDA" run -n sweagent1.1.0 sweagent --help >/dev/null && echo "sweagent OK"

log "Offline tests"
(cd "$WORK/susvibes" && "$CONDA" run -n sv python -m pytest -q tests)

log "Done"
docker --version
df -h "$(sudo docker info -f '{{.DockerRootDir}}')" | tail -1
