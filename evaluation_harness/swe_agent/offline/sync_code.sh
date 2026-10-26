#!/usr/bin/env bash
# Ship this repo's current branch to the VM's checkout (no GitHub access there) and fast-forward it.
# Commit first: only committed work is in the bundle.
#
#   bash evaluation_harness/swe_agent/offline/sync_code.sh
# The VM is reached with $SV_VM_SSH (a command that runs its last argument on the VM).
set -euo pipefail

SSH="${SV_VM_SSH:-gcloud compute ssh susvibes --zone us-central1-a --project benchmark-temp-project --tunnel-through-iap --quiet --command}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
BRANCH="$(git -C "$REPO" rev-parse --abbrev-ref HEAD)"
REMOTE_REPO='~/susvibes-work/susvibes'

[ -z "$(git -C "$REPO" status --porcelain --untracked-files=no)" ] || echo "Warning: uncommitted changes are not shipped."
git -C "$REPO" bundle create - "$BRANCH" 2>/dev/null \
    | $SSH "cat > /tmp/susvibes-sync.bundle && cd $REMOTE_REPO && git pull -q --ff-only /tmp/susvibes-sync.bundle $BRANCH && git log --oneline -1 && rm /tmp/susvibes-sync.bundle"
