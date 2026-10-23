# SWE-agent Evaluation Harness

This directory contains tools for running SWE-agent evaluations on a SusVibes dataset and
converting the resulting trajectories into the standard format.

## Overview

The harness drives [SWE-agent](https://github.com/SWE-agent/SWE-agent) over a dataset to
produce predictions, then converts its trajectory output into the standard OpenAI-messages
format. The dataset's `problem_statement` is used as-is.

## Files

- **`run_infer.py`** - Runs SWE-agent over a dataset and writes `predictions.json`.
- **`setup_vm.sh`** - One-shot, idempotent VM setup (Docker, conda envs, fork + patched SWE-agent, offline tests).
- **`pull_images.py`** - Pre-pulls every task image, in parallel, stopping before the disk fills.
- **`run_suite.py`** - Runs every model in `models.yaml` through `run_infer.py`, then scores them in one eval run.
- **`models.yaml`** - The model suite: litellm model string, endpoint, sampling and provider kwargs per model.
- **`setting.yaml`** - Run configuration (SWE-agent config(s), conda env, workers, model limits).
- **`configs/`** - SWE-agent configs: `susvibes_eval` (SWE-agent's default config with the canonical SusVibes prompt) and `no_cache` (drops Anthropic `cache_control` marks). Copied into SWE-agent's `config/` before each batch.
- **`patches/`** - Backport for SWE-agent v1.1.0 letting `temperature`/`top_p` be `null` (not sent), which models that reject sampling parameters need.
- **`expert_instances.yaml`** - Example of the SWE-agent instances file `run_infer.py` builds.
- **`convert.py`** - Converts SWE-agent `.traj` output into the standard format (OpenAI messages).

## Usage

### Installation

Clone SWE-agent v1.1.0 next to this repository (`setting.yaml` expects `../SWE-agent`), apply the
patch, and install it into the conda env named in `setting.yaml`:

```bash
git clone --branch v1.1.0 https://github.com/SWE-agent/SWE-agent.git ../SWE-agent
git -C ../SWE-agent apply "$PWD/evaluation_harness/swe_agent/patches/sweagent-v1.1.0-optional-sampling.patch"
conda create -n sweagent1.1.0 python=3.11 -y
conda run -n sweagent1.1.0 pip install -e ../SWE-agent
```

Provider API keys are read from the environment by litellm (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
`GEMINI_API_KEY`, ...) or from the `$ENV_VAR` named in a model's `api_key`.

### On a fresh VM

```bash
curl -fsSL https://raw.githubusercontent.com/zach-y-10a/susvibes/swe-agent-multi-model/evaluation_harness/swe_agent/setup_vm.sh | bash
```

This clones everything under `~/susvibes-work` and installs both conda envs (`sv` for the harness and
eval, `sweagent1.1.0` for SWE-agent). Then pre-pull the task images with `pull_images.py`. Every model
shares the 186 images: about 165 GB compressed after layer sharing, an estimated 330-500 GB on disk,
plus one derived image per task that SWE-agent builds. Give Docker at least 750 GB.

### Run one model

```bash
python evaluation_harness/swe_agent/run_infer.py --dataset_path <dataset.jsonl> --model <litellm-model>
```

This builds the SWE-agent batch, runs it, and writes `predictions.json` under the output
directory.

### Run the model suite

Fill in the `TODO`s in `models.yaml`, then smoke-test one instance per model before a full run:

```bash
python evaluation_harness/swe_agent/run_suite.py --run_id suite1 --dry_run            # print per-model configs
python evaluation_harness/swe_agent/run_suite.py --run_id smoke --smoke --evaluate     # 1 instance per model
python evaluation_harness/swe_agent/run_suite.py --run_id suite1 --evaluate            # all 186 instances
```

Models run one after another, each as a parallel SWE-agent batch; a model that fails is recorded
in `runs/<run_id>/<label>/infer_status.json` and the suite moves on. Re-running the same command
resumes: SWE-agent skips instances it already finished. `--models <label> ...` limits the run,
`--skip_infer --evaluate` re-scores existing predictions. Scores land in `runs/<run_id>/scores.md`.

The agent cost column is SWE-agent's litellm-computed cost, available only for models litellm has
prices for; check each provider's console for the real spend. SWE-agent's own token counts leave out
reasoning tokens.

### Convert

```bash
python convert.py --input_dir <run dir> [--output_dir <out dir>]
```

See [`../TRAJECTORY_FORMAT.md`](../TRAJECTORY_FORMAT.md) for the record format. Because
trajectories are large, the converter writes the split layout: an index `<DIR>.trials.json`
whose `messages` field is a path, plus one `messages/<id>.json` per instance.

## Requirements

- SusVibes installed
- SWE-agent installed with credentials configured
