"""Pre-pull every task image a run needs, so the suite never stalls or fails on a pull mid-run.

Both SWE-agent and the eval run on each record's `image_name`, so one image per task is all a
suite needs; every model reuses it. Images already present are skipped, pulls run in parallel,
and pulling stops before Docker's disk drops below `--min_free_gb`.

Usage:
    python evaluation_harness/swe_agent/pull_images.py [--workers 4] [--min_free_gb 60] \
        [--dataset_path ...] [--instance_ids '[...]']
"""

import argparse
import json
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from susvibes.core.utils import load_file, resolve_image_name

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DATASET = REPO_ROOT / "datasets" / "default" / "susvibes_dataset.jsonl"


def docker_root() -> str:
    """Docker's data dir, or `/` where it lives inside a VM (Docker Desktop) rather than on this host."""
    out = subprocess.run(["docker", "info", "--format", "{{.DockerRootDir}}"],
                         capture_output=True, text=True, check=True)
    root = out.stdout.strip()
    return root if Path(root).exists() else "/"


def free_gb(path: str) -> float:
    return shutil.disk_usage(path).free / 1024 ** 3


def image_present(image: str) -> bool:
    return subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode == 0


def main():
    parser = argparse.ArgumentParser(description="Pre-pull SusVibes task images.")
    parser.add_argument("--dataset_path", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--instance_ids", type=json.loads, default=None, help="JSON list of instance IDs.")
    parser.add_argument("--workers", type=int, default=4, help="Parallel pulls.")
    parser.add_argument("--min_free_gb", type=float, default=60,
                        help="Stop starting new pulls below this much free space on Docker's disk.")
    args = parser.parse_args()

    dataset = load_file(args.dataset_path)
    if args.instance_ids is not None:
        dataset = [record for record in dataset if record["instance_id"] in set(args.instance_ids)]
    images = sorted({resolve_image_name(record["image_name"]) for record in dataset})
    missing = [image for image in images if not image_present(image)]
    root = docker_root()
    print(f"{len(images)} images, {len(images) - len(missing)} already present, "
          f"{len(missing)} to pull. {free_gb(root):.0f} GB free on {root}.")

    stop = threading.Event()
    failed, skipped = [], []

    def pull(image: str) -> str:
        if stop.is_set() or free_gb(root) < args.min_free_gb:
            stop.set()
            skipped.append(image)
            return "skipped"
        result = subprocess.run(["docker", "pull", "-q", image], capture_output=True, text=True)
        if result.returncode != 0:
            failed.append(image)
            return f"FAILED: {result.stderr.strip().splitlines()[-1] if result.stderr.strip() else '?'}"
        return "ok"

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(pull, image): image for image in missing}
        for done, future in enumerate(as_completed(futures), 1):
            status = future.result()
            if status != "skipped":
                print(f"[{done}/{len(missing)}] {status:6s} {futures[future]}  ({free_gb(root):.0f} GB free)")

    print(f"\nPulled {len(missing) - len(failed) - len(skipped)}, failed {len(failed)}, "
          f"skipped {len(skipped)}. {free_gb(root):.0f} GB free.")
    if skipped:
        print(f"Stopped below {args.min_free_gb:.0f} GB free; grow the disk and re-run to pull the rest.")
    for image in failed:
        print(f"  failed: {image}")
    if failed or skipped:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
