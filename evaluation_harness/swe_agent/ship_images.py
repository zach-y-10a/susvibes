"""Build every task's images on a machine with internet and stream them to a VM that has none.

Per task: pull the task image (what the eval runs), build its prebuilt agent image (prebuilt.py),
check that image offline (`--network none`), stream both to the VM through SSH
(`docker save | zstd | ssh 'zstd -d | docker load'`), then delete them locally. Only a few tasks
are on local disk at once, so the full set never has to fit here. Builds run on a dedicated buildx
builder whose cache is trimmed after every task; other images and build cache are left alone.
Tasks whose images are already on the VM are skipped, so re-running resumes.

Usage (from the repo root, on the build machine):
    python evaluation_harness/swe_agent/ship_images.py --smoke          # first task only
    python evaluation_harness/swe_agent/ship_images.py --workers 2      # everything
The VM is reached with $SV_VM_SSH (a command that runs its last argument on the VM), defaulting to
the gcloud IAP command below.
"""

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

HARNESS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(HARNESS_DIR))

from prebuilt import AGENT_DOCKERFILE, PYTHON_VERSION_CMD, VERIFY_CMD, agent_image_name  # noqa: E402
from susvibes.core.utils import load_file, resolve_image_name  # noqa: E402

REPO_ROOT = HARNESS_DIR.parent.parent
DEFAULT_DATASET = REPO_ROOT / "datasets" / "default" / "susvibes_dataset.jsonl"
LOG_PATH = REPO_ROOT / "runs" / "ship_images.jsonl"
DEFAULT_SSH = ("gcloud compute ssh susvibes --zone us-central1-a --project benchmark-temp-project "
               "--tunnel-through-iap --quiet --command")
BUILDER = "susvibes-builder"
ATTEMPTS = 3
PULLS_PER_TASK = 2   # `docker pull` of the task image, and the builder's own pull of it
PLATFORM = "linux/amd64"

log_lock = threading.Lock()
quota_lock = threading.Lock()
disk_lock = threading.Lock()
reserved_gb = 0.0  # local disk promised to tasks in flight (guarded by disk_lock)


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def check(cmd: list[str], what: str, **kwargs) -> str:
    result = run(cmd, **kwargs)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout).strip().splitlines()[-3:]
        raise RuntimeError(f"{what} failed: {' | '.join(tail)}")
    return result.stdout


def vm(ssh: list[str], remote_cmd: str) -> str:
    return check(ssh + [remote_cmd], f"ssh `{remote_cmd}`")


def vm_images(ssh: list[str]) -> set[str]:
    return set(vm(ssh, "docker images --format '{{.Repository}}:{{.Tag}}'").split())


def vm_free_gb(ssh: list[str]) -> float:
    return float(vm(ssh, "df --output=avail -B1G \"$(docker info -f '{{.DockerRootDir}}')\" | tail -1"))


def local_free_gb() -> float:
    return shutil.disk_usage("/").free / 1024 ** 3


def tagged(image: str) -> str:
    return image if ":" in image.rsplit("/", 1)[-1] else f"{image}:latest"


def dockerhub_pulls_remaining() -> int | None:
    """Anonymous pulls left in this IP's Docker Hub window (a HEAD request, which doesn't count)."""
    try:
        token_url = ("https://auth.docker.io/token?service=registry.docker.io"
                     "&scope=repository:ratelimitpreview/test:pull")
        token = json.load(urllib.request.urlopen(token_url, timeout=20))["token"]
        request = urllib.request.Request("https://registry-1.docker.io/v2/ratelimitpreview/test/manifests/latest",
                                         method="HEAD", headers={"Authorization": f"Bearer {token}"})
        header = urllib.request.urlopen(request, timeout=20).headers.get("ratelimit-remaining")
        return int(header.split(";")[0]) if header else None
    except Exception:
        return None  # unknown: don't block on the check itself


def task_image_gb(image: str) -> float | None:
    """Compressed size of a Docker Hub image, from the Hub API (not a pull)."""
    repo, _, tag = image.partition(":")
    try:
        url = f"https://hub.docker.com/v2/repositories/{repo}/tags/{tag or 'latest'}"
        return json.load(urllib.request.urlopen(url, timeout=20))["full_size"] / 1024 ** 3
    except Exception:
        return None


def reserve_local_disk(need_gb: float, floor_gb: float, stop: threading.Event) -> bool:
    """Wait until this task's disk need fits above the floor, counting what tasks in flight have
    reserved, then reserve it. A task alone always proceeds (it may still fail on space, and says so)."""
    global reserved_gb
    while not stop.is_set():
        with disk_lock:
            if reserved_gb == 0 or local_free_gb() - reserved_gb - need_gb >= floor_gb:
                reserved_gb += need_gb
                return True
        time.sleep(15)
    return False


def release_local_disk(need_gb: float) -> None:
    global reserved_gb
    with disk_lock:
        reserved_gb -= need_gb


def wait_for_pull_quota(reserve: int) -> None:
    with quota_lock:
        while (remaining := dockerhub_pulls_remaining()) is not None and remaining < reserve:
            print(f"Docker Hub: {remaining} pulls left this hour; waiting.", flush=True)
            time.sleep(120)


def ensure_builder() -> None:
    if run(["docker", "buildx", "inspect", BUILDER]).returncode != 0:
        check(["docker", "buildx", "create", "--name", BUILDER, "--driver", "docker-container",
               "--platform", PLATFORM], "create buildx builder")


def stream_to_vm(ssh: list[str], images: list[str]) -> None:
    """docker save | zstd | ssh 'zstd -d | docker load', failing if any stage fails."""
    save = subprocess.Popen(["docker", "save", *images], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    compress = subprocess.Popen(["zstd", "-T0", "-3", "-q", "-c"], stdin=save.stdout,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    save.stdout.close()
    load = subprocess.Popen(ssh + ["zstd -dc | docker load -q"], stdin=compress.stdout,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    compress.stdout.close()
    load_out, load_err = load.communicate()
    failures = [
        name for name, proc in (("docker save", save), ("zstd", compress), ("remote docker load", load))
        if proc.wait() != 0
    ]
    if failures:
        raise RuntimeError(f"stream failed in {', '.join(failures)}: {load_err.decode().strip()[-300:]}")


def ship_task(record: dict, args, ssh: list[str], stop: threading.Event) -> dict:
    task_image = tagged(resolve_image_name(record["image_name"]))
    agent_image = agent_image_name(record["image_name"])
    started = time.time()
    # Peak local use while a task is in flight is about 3x its compressed size: Docker's unpacked
    # copy plus the builder's own copy of the base layers.
    need_gb = 3 * (task_image_gb(task_image) or 2.0)
    if not reserve_local_disk(need_gb, args.min_local_free_gb, stop):
        return {"status": "skipped"}
    had_task_image = run(["docker", "image", "inspect", task_image]).returncode == 0
    wait_for_pull_quota(reserve=PULLS_PER_TASK * args.workers)
    try:
        check(["docker", "pull", "-q", "--platform", PLATFORM, task_image], "pull task image")
        check(["docker", "buildx", "build", "--builder", BUILDER, "--platform", PLATFORM, "--load",
               "-q", "--build-arg", f"BASE_IMAGE={task_image}", "-t", agent_image, "-"],
              "build agent image", input=AGENT_DOCKERFILE)
        prune_builder(args)  # the builder's copy of the base layers isn't needed for the upload
        verified = check(["docker", "run", "--rm", "--network", "none", "--platform", PLATFORM,
                          "--entrypoint", "/bin/sh", agent_image, "-c", VERIFY_CMD], "offline check of agent image")
        # e.g. "python=3.7.17 filemap=no": tasks without the filemap view are worth knowing about.
        env_info = verified.strip().splitlines()[-1] if verified.strip() else ""
        own_python = check(["docker", "run", "--rm", "--network", "none", "--platform", PLATFORM,
                            "--entrypoint", "/bin/sh", task_image, "-c", PYTHON_VERSION_CMD],
                           "python3 version of task image").strip().splitlines()[-1]
        if f"python={own_python} " not in env_info + " ":
            raise RuntimeError(f"agent image runs python3 {env_info}, task image runs {own_python}")
        stream_to_vm(ssh, [task_image, agent_image])
        return {"status": "shipped", "seconds": round(time.time() - started), "env": env_info}
    finally:
        to_remove = [agent_image] + ([] if had_task_image else [task_image])
        run(["docker", "rmi", "-f", *to_remove])
        prune_builder(args)
        release_local_disk(need_gb)


def prune_builder(args) -> None:
    run(["docker", "buildx", "prune", "--builder", BUILDER, "-f", "--max-used-space", f"{args.builder_cache_gb}gb"])


def log(entry: dict) -> None:
    with log_lock:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a") as f:
            f.write(json.dumps(entry) + "\n")


def main():
    parser = argparse.ArgumentParser(description="Build task + agent images locally and stream them to the VM.")
    parser.add_argument("--dataset_path", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--instance_ids", type=json.loads, default=None, help="JSON list of instance IDs.")
    parser.add_argument("--smoke", type=int, nargs="?", const=1, default=None, help="Only the first N tasks.")
    parser.add_argument("--workers", type=int, default=2, help="Tasks in flight at once.")
    parser.add_argument("--min_local_free_gb", type=float, default=20,
                        help="Free disk to keep on this machine, after what tasks in flight have reserved.")
    parser.add_argument("--min_vm_free_gb", type=float, default=40,
                        help="Stop once the VM's Docker disk has less free space.")
    parser.add_argument("--builder_cache_gb", type=float, default=8,
                        help="Build cache kept on the dedicated builder (keeps the Python build warm).")
    args = parser.parse_args()

    ssh = shlex.split(os.environ.get("SV_VM_SSH", DEFAULT_SSH))
    dataset = load_file(args.dataset_path)
    if args.instance_ids is not None:
        dataset = [record for record in dataset if record["instance_id"] in set(args.instance_ids)]
    if args.smoke is not None:
        dataset = dataset[:args.smoke]

    ensure_builder()
    on_vm = vm_images(ssh)
    todo = [
        record for record in dataset
        if not {tagged(resolve_image_name(record["image_name"])), agent_image_name(record["image_name"])} <= on_vm
    ]
    print(f"{len(dataset)} tasks, {len(dataset) - len(todo)} already on the VM, {len(todo)} to ship. "
          f"Free: {local_free_gb():.0f} GB here, {vm_free_gb(ssh):.0f} GB on the VM.", flush=True)

    stop = threading.Event()
    counts = {"shipped": 0, "failed": 0, "skipped": 0}
    vm_free_lock = threading.Lock()

    def worker(record: dict) -> dict:
        with vm_free_lock:
            if not stop.is_set() and vm_free_gb(ssh) < args.min_vm_free_gb:
                print(f"VM below {args.min_vm_free_gb:.0f} GB free; stopping.", flush=True)
                stop.set()
        if stop.is_set():
            return {"status": "skipped"}
        errors = []
        for attempt in range(1, ATTEMPTS + 1):
            try:
                result = ship_task(record, args, ssh, stop)
                if result["status"] == "shipped":
                    result.update(attempts=attempt, vm_free_gb=vm_free_gb(ssh))
                return result
            except Exception as exc:
                errors.append(str(exc)[:300])
                time.sleep(30 * attempt)
        return {"status": "failed", "error": " || ".join(errors)}

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(worker, record): record for record in todo}
        for done, future in enumerate(as_completed(futures), 1):
            record, result = futures[future], future.result()
            counts[result["status"]] += 1
            log({"time": datetime.now(timezone.utc).isoformat(), "instance_id": record["instance_id"], **result})
            if result["status"] != "skipped":
                detail = result.get("error") or f"{result.get('seconds')} s, {result.get('env')}"
                print(f"[{done}/{len(todo)}] {result['status']:7s} {record['instance_id']}  ({detail})", flush=True)

    print(f"\nShipped {counts['shipped']}, failed {counts['failed']}, skipped {counts['skipped']}. "
          f"VM free: {vm_free_gb(ssh):.0f} GB. Log: {LOG_PATH}")
    if counts["failed"] or counts["skipped"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
