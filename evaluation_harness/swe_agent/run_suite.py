"""Run a suite of models through SWE-agent on SusVibes, then score them all in one eval run.

Each model in models.yaml gets a per-model SWE-agent config (sampling, provider kwargs,
endpoint), layered after susvibes_eval (and no_cache unless the model keeps Anthropic caching).
Models run one after another, each as a parallel SWE-agent batch. Everything for a suite lands
under runs/<run_id>/:

    runs/<run_id>/
    ├── <label>/trajectories/      # SWE-agent output (resumable: finished instances are skipped)
    ├── <label>/predictions.jsonl  # model_name_or_path rewritten to <label>
    ├── <label>/infer_status.json  # SWE-agent cost and exit statuses, or the error that stopped it
    ├── predictions.jsonl          # all models combined, the eval input
    └── scores.md                  # FuncPass / SecPass table

Usage:
    python evaluation_harness/swe_agent/run_suite.py --run_id suite1 --smoke      # 1 instance/model
    python evaluation_harness/swe_agent/run_suite.py --run_id suite1 --evaluate
    python evaluation_harness/swe_agent/run_suite.py --run_id suite1 --models claude-opus-5-5-max \
        --skip_infer --evaluate
"""

import argparse
import json
import subprocess
import sys
import traceback
from pathlib import Path

HARNESS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(HARNESS_DIR))

from run_infer import run_infer  # noqa: E402
from suite_config import (  # noqa: E402
    MODELS_PATH, REPO_ROOT, config_names_for, deep_merge, install_configs, load_models, load_setting,
    write_model_config,
)
from susvibes.core.constants import EVAL_LOG_DIR, LOG_SUMMARY  # noqa: E402
from susvibes.core.utils import load_file, save_file  # noqa: E402

DEFAULT_DATASET = REPO_ROOT / "datasets" / "default" / "susvibes_dataset.jsonl"
RUNS_DIR = REPO_ROOT / "runs"
STRATEGY = "none"


def collect_predictions(entry: dict, agent_output_dir: Path, model_dir: Path) -> list[dict]:
    predictions = load_file(agent_output_dir / "predictions.json")
    for prediction in predictions:
        prediction["model_name_or_path"] = entry["label"]
    save_file(predictions, model_dir / "predictions.jsonl")
    return predictions


def infer_status(agent_output_dir: Path) -> dict:
    statuses_path = agent_output_dir / "run_batch_exit_statuses.yaml"
    if not statuses_path.exists():
        return {}
    statuses = load_file(statuses_path)
    return {
        "total_cost": statuses.get("total_cost"),
        "exit_status_counts": {
            status: len(ids) for status, ids in statuses.get("instances_by_exit_status", {}).items()
        },
    }


def run_models(args, models: list[dict], run_dir: Path) -> None:
    setting = load_setting()
    install_configs(setting["sweagent_dir"])
    for entry in models:
        label = entry["label"]
        model_dir = run_dir / label
        model_dir.mkdir(parents=True, exist_ok=True)
        model_config_name = write_model_config(entry, setting["sweagent_dir"])
        config_name = config_names_for(entry, setting["config_name"], model_config_name)
        model_overrides = {
            key: entry[key] for key in ("per_instance_cost_limit", "per_instance_call_limit") if key in entry
        }
        print(f"\n=== {label} ({entry['model']}) — configs: {config_name} ===")
        if args.dry_run:
            config_path = setting["sweagent_dir"] / "config" / f"{model_config_name}.yaml"
            print(config_path.read_text())
            continue
        agent_output_dir = model_dir / "trajectories"
        try:
            run_infer(
                dataset_path=args.dataset_path,
                model_name=entry["model"],
                run_name=f"{args.run_id}__{label}",
                output_dir=agent_output_dir,
                num_workers=args.num_workers,
                instance_ids=args.instance_ids,
                config_name=config_name,
                model_overrides=model_overrides,
                force=args.force,
            )
            predictions = collect_predictions(entry, agent_output_dir, model_dir)
            status = {"ok": True, "num_predictions": len(predictions), **infer_status(agent_output_dir)}
        except Exception as exc:  # one provider failing must not stop the rest of the suite
            traceback.print_exc()
            status = {"ok": False, "error": repr(exc), **infer_status(agent_output_dir)}
        save_file(status, model_dir / "infer_status.json")
        print(f"[{label}] {status}")


def combine_predictions(models: list[dict], run_dir: Path) -> Path:
    combined = []
    for entry in models:
        path = run_dir / entry["label"] / "predictions.jsonl"
        if path.exists():
            combined.extend(load_file(path))
        else:
            print(f"[{entry['label']}] no predictions; left out of the eval.")
    combined_path = run_dir / "predictions.jsonl"
    save_file(combined, combined_path)
    print(f"Combined {len(combined)} predictions into {combined_path}.")
    return combined_path


def evaluate(args, predictions_path: Path) -> None:
    cmd = [
        sys.executable, "-m", "susvibes.eval.core",
        "--run_id", args.run_id,
        "--predictions_path", str(predictions_path),
        "--max_workers", str(args.eval_workers),
        "--strategy", STRATEGY,
    ]
    if args.instance_ids is not None:
        cmd += ["--instance_ids", json.dumps(args.instance_ids)]
    print("Running:", " ".join(cmd))
    subprocess.run(cmd, cwd=REPO_ROOT, check=True)


def write_scores(args, models: list[dict], run_dir: Path) -> None:
    rows = ["| Model | FuncPass | SecPass | Submitted | Empty | Patch errors | Indeterminate | Agent cost ($) |",
            "|---|---|---|---|---|---|---|---|"]
    for entry in models:
        label = entry["label"]
        summary_path = EVAL_LOG_DIR / args.run_id / STRATEGY / label / LOG_SUMMARY
        status_path = run_dir / label / "infer_status.json"
        cost = (load_file(status_path).get("total_cost") if status_path.exists() else None)
        cost = "—" if cost in (None, 0) else f"{cost:.2f}"
        if not summary_path.exists():
            rows.append(f"| {label} | — | — | — | — | — | — | {cost} |")
            continue
        s = load_file(summary_path)
        rows.append(
            f"| {label} | {s['func_pass']:.1%} | {s['sec_pass']:.1%} | "
            f"{s['num_submitted']}/{s['num_candidates']} | {s['num_empty_model_patch']} | "
            f"{s['num_model_patch_errors']} | {s['num_indeterminate']} | {cost} |"
        )
    table = "\n".join(rows) + "\n"
    (run_dir / "scores.md").write_text(table)
    print("\n" + table)


def main():
    parser = argparse.ArgumentParser(description="Run a suite of models on SusVibes via SWE-agent.")
    parser.add_argument("--run_id", type=str, required=True, help="Suite name; output under runs/<run_id>/.")
    parser.add_argument("--models_path", type=Path, default=MODELS_PATH)
    parser.add_argument("--models", type=str, nargs="+", default=None, help="Only these model labels.")
    parser.add_argument("--dataset_path", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--instance_ids", type=json.loads, default=None, help="JSON list of instance IDs.")
    parser.add_argument("--smoke", type=int, nargs="?", const=1, default=None,
                        help="Only the first N instances (default 1) — a cheap end-to-end check per model.")
    parser.add_argument("--num_workers", type=int, default=None, help="SWE-agent workers (default: setting.yaml).")
    parser.add_argument("--eval_workers", type=int, default=5, help="Parallel instances in the eval.")
    parser.add_argument("--skip_infer", action="store_true", help="Reuse existing predictions.")
    parser.add_argument("--evaluate", action="store_true", help="Score the combined predictions afterwards.")
    parser.add_argument("--force", action="store_true", help="Re-run instances SWE-agent already finished.")
    parser.add_argument("--dry_run", action="store_true", help="Write and print per-model configs only.")
    args = parser.parse_args()

    if args.smoke is not None and args.instance_ids is None:
        dataset = load_file(args.dataset_path)
        args.instance_ids = [record["instance_id"] for record in dataset[:args.smoke]]
    models = load_models(args.models_path, args.models)
    run_dir = RUNS_DIR / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    if not args.skip_infer:
        run_models(args, models, run_dir)
    if args.dry_run:
        return
    predictions_path = combine_predictions(models, run_dir)
    if args.evaluate:
        evaluate(args, predictions_path)
    write_scores(args, models, run_dir)


if __name__ == "__main__":
    main()
