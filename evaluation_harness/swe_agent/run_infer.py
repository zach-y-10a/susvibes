"""Run SWE-agent over a prepared SusVibes dataset and collect its predictions.

The dataset is consumed as-is (its `problem_statement` already carries any strategy
guardrail from `susvibes.eval.core --prepare_dataset`), so this runner is strategy-free.
It builds the SWE-agent batch, runs it, and writes `predictions.json` next to the
trajectories. Score the predictions separately:

    python -m susvibes.eval.core --predictions_path <output_dir>/predictions.json \
        --strategy <strategy> --run_id <run_id>

Usage:
    python run_infer.py --dataset_path <prepared.jsonl> --model <litellm-model> \
        [--run_name NAME] [--output_dir DIR] [--num_workers N] [--instance_ids '[...]']
"""

import argparse
import json
import shutil
from pathlib import Path

from susvibes.core.agents.sweagent import SWEAgentPort
from susvibes.core.utils import load_file, save_file
from susvibes.env_specs import WORKSPACE_DIR_NAME

HARNESS_DIR = Path(__file__).parent
SETTING_PATH = HARNESS_DIR / "setting.yaml"
CONFIGS_DIR = HARNESS_DIR / "configs"
REPO_ROOT = HARNESS_DIR.parent.parent


def load_setting() -> dict:
    """setting.yaml with `sweagent_dir` resolved against the SusVibes repo root."""
    setting = load_file(SETTING_PATH)
    setting["sweagent_dir"] = (REPO_ROOT / setting["sweagent_dir"]).resolve()
    return setting


def install_configs(sweagent_dir: Path) -> None:
    """SWE-agent resolves `--config=config/<name>.yaml` against its own codebase, so the
    SusVibes configs are copied in there before every batch."""
    for config_path in CONFIGS_DIR.glob("*.yaml"):
        shutil.copy(config_path, Path(sweagent_dir) / "config" / config_path.name)


def run_infer(
    dataset_path: Path,
    model_name: str,
    run_name: str = None,
    output_dir: Path = None,
    num_workers: int = None,
    instance_ids: list = None,
    config_name: list = None,
    model_overrides: dict = None,
    force: bool = False,
) -> Path:
    """`config_name` replaces setting.yaml's config list (e.g. to add a per-model config);
    `model_overrides` updates its cost/call limits."""
    setting = load_setting()
    install_configs(setting["sweagent_dir"])
    run_name = run_name or dataset_path.stem
    output_dir = output_dir or (setting["sweagent_dir"] / "trajectories" / "susvibes_eval" / run_name)
    model = {**setting["model"], **(model_overrides or {}), "name": model_name}
    port = SWEAgentPort.from_settings(
        setting, run_name=run_name, model=model,
        output_dir=output_dir, num_workers=num_workers, config_name=config_name,
    )

    dataset = load_file(dataset_path)
    if instance_ids is not None:
        dataset = [record for record in dataset if record["instance_id"] in set(instance_ids)]
    for record in dataset:
        port.add_task(
            image=record["image_name"],
            repo_type="preexisting",
            repo_name=WORKSPACE_DIR_NAME,
            problem_statement=record["problem_statement"],
            instance_id=record["instance_id"],
        )
    port.before_start()

    agent_output_dir = port.run_batch(force=force)
    predictions, _ = SWEAgentPort.after_completion(agent_output_dir)
    predictions_path = agent_output_dir / "predictions.json"
    save_file(predictions, predictions_path)
    print(f"Predictions saved to {predictions_path}.")
    return predictions_path


def main():
    parser = argparse.ArgumentParser(description="Run SWE-agent over a prepared SusVibes dataset.")
    parser.add_argument(
        "--dataset_path",
        type=Path,
        required=True,
        help="Prepared dataset JSONL (problem_statement already guarded).",
    )
    parser.add_argument(
        "--model",
        type=str,
        required=True,
        help="litellm model name passed to SWE-agent.",
    )
    parser.add_argument(
        "--run_name",
        type=str,
        default=None,
        help="Run name for the instances file and output dir (default: dataset stem).",
    )
    parser.add_argument(
        "--output_dir",
        type=Path,
        default=None,
        help="Where SWE-agent writes its trajectories (default: under the SWE-agent repo).",
    )
    parser.add_argument(
        "--num_workers",
        type=int,
        default=None,
        help="Override the worker count from setting.yaml.",
    )
    parser.add_argument(
        "--instance_ids",
        type=json.loads,
        default=None,
        help="Only run for the given instance IDs.",
    )
    args = parser.parse_args()
    run_infer(
        dataset_path=args.dataset_path,
        model_name=args.model,
        run_name=args.run_name,
        output_dir=args.output_dir,
        num_workers=args.num_workers,
        instance_ids=args.instance_ids,
    )


if __name__ == "__main__":
    main()
