"""Write predictions that submit each task's own `golden_patch` (the secure reference implementation).

Scoring them checks the eval environment, not a model: every task should come out functionally
correct and secure. A task that doesn't can't be scored fairly for any model, so this runs before
any model does (offline runbook, Gates 2 and 5).

    python evaluation_harness/swe_agent/reference_predictions.py --out runs/reference/predictions.jsonl
    python -m susvibes.eval.core --run_id reference --predictions_path runs/reference/predictions.jsonl
"""

import argparse
import json
from pathlib import Path

from susvibes.core.utils import load_file, save_file

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DATASET = REPO_ROOT / "datasets" / "default" / "susvibes_dataset.jsonl"
LABEL = "reference-golden"


def main():
    parser = argparse.ArgumentParser(description="Predictions that submit each task's golden patch.")
    parser.add_argument("--out", type=Path, required=True, help="Output predictions JSONL.")
    parser.add_argument("--dataset_path", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--instance_ids", type=json.loads, default=None, help="JSON list of instance IDs.")
    args = parser.parse_args()

    dataset = load_file(args.dataset_path)
    if args.instance_ids is not None:
        dataset = [record for record in dataset if record["instance_id"] in set(args.instance_ids)]
    predictions = [
        {"instance_id": record["instance_id"], "model_name_or_path": LABEL, "model_patch": record["golden_patch"]}
        for record in dataset
    ]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    save_file(predictions, args.out)
    print(f"Wrote {len(predictions)} reference predictions to {args.out}.")


if __name__ == "__main__":
    main()
