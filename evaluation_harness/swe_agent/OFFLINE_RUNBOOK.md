# Offline runbook: SusVibes model suite on an egress-less VM

The VM (`susvibes`, `benchmark-temp-project`, network `benchmark-workers`) can reach only the model
APIs allowed on that network; GitHub, PyPI, apt and Docker Hub are blocked. Everything else is built
on a workstation with internet and loaded over SSH (IAP, ~15 MB/s measured).

Each phase ends in a **gate**: a cheap check that must pass before the next, longer phase starts.
The order puts every assumption that could sink the run in front of the multi-hour transfer.

## What runs where

| Piece | Built on the workstation | On the VM |
|---|---|---|
| Docker, git, tmux (`.deb`s resolved against the VM's dpkg status) | `offline/build_bundle.sh` | `offline/install_bundle.sh` |
| Conda envs `sv`, `sweagent1.1.0` (conda-pack) | same | same |
| This repo (git bundle), SWE-agent v1.1.0 + patch | same | same |
| Task image per task (what the eval runs) | `ship_images.py` pulls it | streamed in |
| Agent image per task (task image + swe-rex + tool deps, see `prebuilt.py`) | `ship_images.py` builds and checks it offline | streamed in |
| Model API calls | — | live, through the network's allowed egress |

Code changes after install: `offline/sync_code.sh` ships the current branch as a git bundle and
fast-forwards the VM's checkout.

## Known risks and how each is handled

| Risk | Handling | Checked at |
|---|---|---|
| Eval always pulls images from Docker Hub, even when present | `SV_IMAGE_PULL=missing` (set in the `sv` env) uses the local image | Gate 2 |
| SWE-agent builds an image and pip-installs tools at every container start | prebuilt agent images, `python_standalone_dir: null`, `pull: never`; each image run with `--network none` before shipping | build time, Gate 2 |
| litellm fetches its price table from GitHub at import | `LITELLM_LOCAL_MODEL_COST_MAP=True` in both envs | Gate 1 |
| Docker Hub: 100 anonymous pulls/hour, 2 pulls per task | `ship_images.py` reads the remaining quota before each task and waits | Gate 4 |
| Workstation disk (~55 GB free; others' images in Docker) | one task's images on disk per worker, deleted after shipping; free-space floor; dedicated buildx builder pruned per task | Gate 4 |
| VM disk: ~165 GB compressed, est. 330-500 GB unpacked, plus agent layers | grow the disk to 1 TB in Gate 1; measure bytes per task in Gate 4 and extrapolate before the full ship | Gates 1, 4 |
| A task image the agent build can't handle (no apt, no bash, no pip in the session env) | build or offline check fails for that task only; logged, retried, reported | Gate 4 |
| IAP stream drops, gcloud re-auth expires mid-transfer | per-task retry; re-running resumes (tasks already on the VM are skipped) | Gate 4 |
| Workstation sleeps during a long transfer | run under `caffeinate -dims` | Gate 5 |
| Model API host not allowed, wrong model ID, rejected parameter | `check_models.py` makes real 2-turn tool-call requests with each model's exact SWE-agent config | Gate 3 |
| Provider rejects history without its reasoning blocks (SWE-agent drops them) | the same 2-turn check replays a tool call the way SWE-agent does | Gate 3 |
| Eval or environment broken for some tasks | reference run: every task's `golden_patch` (the secure implementation) must pass func and sec | Gates 2, 5 |

## Phases

### Phase 0 — workstation prerequisites
- Docker Desktop running; `gcloud auth login` fresh; repo committed.
- `vm-dpkg-status`: `gcloud compute ssh ... --command 'cat /var/lib/dpkg/status' > offline-bundle/vm-dpkg-status`.

### Phase 1 — bundle and VM base install
1. `bash offline/build_bundle.sh offline-bundle offline-bundle/vm-dpkg-status`
2. Copy `offline-bundle/` (minus `vm-dpkg-status`) to the VM; run `bash install_bundle.sh` there.
3. Grow the disk: `gcloud compute disks resize susvibes --size 1000GB ...`, reboot, confirm `df -h /`.

**Gate 1:** `docker run hello-world`-style check with a loaded image, `sweagent --help`,
offline test suite passes, `df` shows the grown disk, both env vars set in each conda env.

### Phase 2 — one task end to end
1. `ship_images.py --smoke` (first task).
2. On the VM: eval the reference predictions for that task (`reference_predictions.py`) — expect
   func pass and sec pass.
3. On the VM: one real SWE-agent run of that task with Claude (`run_suite.py --smoke --models
   claude-opus-5-5-max --evaluate`); confirm a non-empty patch and a scored report.

**Gate 2:** both evals complete without image pulls; the agent run used the prebuilt image.

### Phase 3 — model access
Fill in every `TODO` in `models.yaml`, put keys in `~/susvibes-work/keys.env` on the VM.
`check_models.py` on the VM.

**Gate 3:** every model passes, or is consciously dropped. A model needing hosts not yet allowed
goes to whoever owns the network now, not after the images are in.

### Phase 4 — pilot of ~10 diverse tasks
`ship_images.py --instance_ids '[...]'` with the largest images (mlflow, superset), several
projects and Python versions. Reference eval on all of them.

**Gate 4:** all build, check, ship and pass the reference eval; measured VM bytes per task ×186
fits the disk with ≥15% spare; measured hours per task gives the full-ship ETA.

### Phase 5 — full ship
`caffeinate -dims python evaluation_harness/swe_agent/ship_images.py --workers 2` until it
reports 0 failed, 0 skipped (re-run to resume). Reference eval on all 186 on the VM.

**Gate 5:** 186/186 task + agent images on the VM; reference eval func and sec pass on all,
or every exception explained (a task whose reference fails can't be scored for any model).

### Phase 6 — model runs
`run_suite.py --smoke --evaluate` across all models first, then the full suite in tmux.
Re-running resumes. `scores.md` holds the table.
