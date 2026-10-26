#!/usr/bin/env bash
# One-shot setup for a Debian/Ubuntu x86_64 VM running the SusVibes SWE-agent suite.
# Idempotent: re-running skips whatever is already in place.
#
#   bash setup_vm.sh            # workspace at ~/susvibes-work
#   WORK=/data/sv bash setup_vm.sh
#
# Layout it creates (setting.yaml expects SWE-agent next to the SusVibes repo):
#   $WORK/susvibes   fork, branch $BRANCH        conda env: sv
#   $WORK/SWE-agent  v1.1.0 + patches/           conda env: sweagent1.1.0
set -euo pipefail

WORK="${WORK:-$HOME/susvibes-work}"
FORK_URL="${FORK_URL:-https://github.com/zach-y-10a/susvibes.git}"
BRANCH="${BRANCH:-swe-agent-multi-model}"
SWEAGENT_TAG="v1.1.0"
CONDA_DIR="${CONDA_DIR:-$HOME/miniforge3}"
PATCHES_REL="evaluation_harness/swe_agent/patches"

log() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

log "System packages (git, docker)"
if ! command -v docker >/dev/null || ! command -v git >/dev/null; then
    sudo apt-get update -q
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q git curl ca-certificates docker.io docker-buildx
fi
sudo systemctl enable --now docker
if ! id -nG "$USER" | grep -qw docker; then
    sudo usermod -aG docker "$USER"
    echo "Added $USER to the docker group; log out and back in before running the suite."
fi

log "Miniforge (conda)"
if [ ! -x "$CONDA_DIR/bin/conda" ]; then
    installer="$(mktemp -d)/miniforge.sh"
    curl -fsSL -o "$installer" \
        "https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh"
    bash "$installer" -b -p "$CONDA_DIR"
    "$CONDA_DIR/bin/conda" init bash >/dev/null
fi
CONDA="$CONDA_DIR/bin/conda"

log "Repositories under $WORK"
mkdir -p "$WORK"
if [ ! -d "$WORK/susvibes/.git" ]; then
    git clone -b "$BRANCH" "$FORK_URL" "$WORK/susvibes"
else
    git -C "$WORK/susvibes" pull --ff-only
fi
if [ ! -d "$WORK/SWE-agent/.git" ]; then
    git -c advice.detachedHead=false clone --depth 1 --branch "$SWEAGENT_TAG" \
        https://github.com/SWE-agent/SWE-agent.git "$WORK/SWE-agent"
fi
for patch in "$WORK/susvibes/$PATCHES_REL"/*.patch; do
    if git -C "$WORK/SWE-agent" apply --reverse --check "$patch" 2>/dev/null; then
        echo "already applied: $(basename "$patch")"
    else
        git -C "$WORK/SWE-agent" apply "$patch"
    fi
done

log "Conda env: sv (SusVibes harness + eval)"
"$CONDA" env list | grep -qE '^sv\s' || "$CONDA" create -y -q -n sv python=3.11
"$CONDA" run -n sv pip install -q -r "$WORK/susvibes/requirements.txt" pytest
"$CONDA" run -n sv pip install -q -e "$WORK/susvibes"

log "Conda env: sweagent1.1.0 (SWE-agent)"
"$CONDA" env list | grep -qE '^sweagent1\.1\.0\s' || "$CONDA" create -y -q -n sweagent1.1.0 python=3.11
"$CONDA" run -n sweagent1.1.0 pip install -q -e "$WORK/SWE-agent" "mcp>=2.2.0,<3"  # mcp: see offline/build_bundle.sh

log "Offline tests"
(cd "$WORK/susvibes" && "$CONDA" run -n sv python -m pytest -q tests)

log "Done"
df -h "$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || echo /)" | tail -1
cat <<EOF

Next:
  1. Log out and back in if you were just added to the docker group.
  2. Pull the task images:  cd $WORK/susvibes && conda activate sv && python evaluation_harness/swe_agent/pull_images.py
  3. Put API keys in $WORK/keys.env (KEY=value lines), then:  set -a; source $WORK/keys.env; set +a
  4. Smoke test:  python evaluation_harness/swe_agent/run_suite.py --run_id smoke --smoke --evaluate
EOF
