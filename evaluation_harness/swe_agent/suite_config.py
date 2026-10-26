"""Settings and model-suite config shared by run_infer.py, run_suite.py and check_models.py.

Depends only on PyYAML, so check_models.py can import it from the SWE-agent env, which doesn't have
the susvibes package.
"""

import copy
import shutil
from pathlib import Path

import yaml

HARNESS_DIR = Path(__file__).resolve().parent
REPO_ROOT = HARNESS_DIR.parent.parent
SETTING_PATH = HARNESS_DIR / "setting.yaml"
CONFIGS_DIR = HARNESS_DIR / "configs"
MODELS_PATH = HARNESS_DIR / "models.yaml"
MODEL_CONFIG_SUBDIR = "susvibes_models"


def load_setting() -> dict:
    """setting.yaml with `sweagent_dir` resolved against the SusVibes repo root."""
    setting = yaml.safe_load(SETTING_PATH.read_text())
    setting["sweagent_dir"] = (REPO_ROOT / setting["sweagent_dir"]).resolve()
    return setting


def install_configs(sweagent_dir: Path) -> None:
    """SWE-agent resolves `--config=config/<name>.yaml` against its own codebase, so the
    SusVibes configs are copied in there before every batch."""
    for config_path in CONFIGS_DIR.glob("*.yaml"):
        shutil.copy(config_path, Path(sweagent_dir) / "config" / config_path.name)


def deep_merge(base: dict, override: dict) -> dict:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def load_models(models_path: Path = MODELS_PATH, labels: list = None) -> list[dict]:
    spec = yaml.safe_load(Path(models_path).read_text())
    defaults = spec.get("defaults", {})
    models = [deep_merge(defaults, entry) for entry in spec["models"]]
    seen = set()
    for entry in models:
        assert entry["label"] not in seen, f"Duplicate label {entry['label']!r} in {models_path}."
        assert "/" not in entry["label"], f"Label {entry['label']!r} must not contain '/'."
        seen.add(entry["label"])
    if labels is not None:
        unknown = set(labels) - seen
        assert not unknown, f"Unknown model labels: {sorted(unknown)}."
        models = [entry for entry in models if entry["label"] in set(labels)]
    return models


def write_model_config(entry: dict, sweagent_dir: Path) -> str:
    """Write the per-model SWE-agent config and return its config name."""
    model_config = dict(entry.get("agent_model", {}))
    for key in ("api_base", "api_key"):
        if entry.get(key):
            model_config[key] = entry[key]
    config_name = f"{MODEL_CONFIG_SUBDIR}/{entry['label']}"
    config_path = Path(sweagent_dir) / "config" / f"{config_name}.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.dump({"agent": {"model": model_config}}, sort_keys=False))
    return config_name


def config_names_for(entry: dict, base_names: list, model_config_name: str) -> list[str]:
    names = [name for name in base_names if not (entry.get("cache") and name == "no_cache")]
    return names + [model_config_name]
