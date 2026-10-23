"""Tests for the SWE-agent configs and the multi-model suite driver (no SWE-agent, no Docker)."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

from evaluation_harness.common import get_instance_template

SWE_AGENT_DIR = Path(__file__).resolve().parent.parent / "evaluation_harness" / "swe_agent"
sys.path.insert(0, str(SWE_AGENT_DIR))

import run_suite  # noqa: E402


def _load_config(name: str) -> dict:
    return yaml.safe_load((SWE_AGENT_DIR / "configs" / f"{name}.yaml").read_text())


def test_instance_template_matches_canonical() -> None:
    template = _load_config("susvibes_eval")["agent"]["templates"]["instance_template"]
    assert template == get_instance_template("{{working_dir}}", "{{problem_statement}}").rstrip("\n")


def test_no_cache_clears_history_processors() -> None:
    assert _load_config("susvibes_eval")["agent"]["history_processors"][0]["type"] == "cache_control"
    assert _load_config("no_cache") == {"agent": {"history_processors": []}}


def test_setting_config_names_exist() -> None:
    setting = yaml.safe_load((SWE_AGENT_DIR / "setting.yaml").read_text())
    for name in setting["config_name"]:
        assert (SWE_AGENT_DIR / "configs" / f"{name}.yaml").exists()


def test_models_yaml_loads_with_defaults() -> None:
    models = run_suite.load_models(run_suite.MODELS_PATH)
    assert len(models) == len({entry["label"] for entry in models})
    for entry in models:
        assert entry["agent_model"]["temperature"] is None
        assert entry["agent_model"]["completion_kwargs"]["timeout"] == 1800


def test_deep_merge_keeps_default_kwargs() -> None:
    merged = run_suite.deep_merge(
        {"agent_model": {"completion_kwargs": {"timeout": 1}, "temperature": None}},
        {"agent_model": {"completion_kwargs": {"reasoning_effort": "high"}}},
    )
    assert merged["agent_model"]["completion_kwargs"] == {"timeout": 1, "reasoning_effort": "high"}
    assert merged["agent_model"]["temperature"] is None


def test_config_names_drop_no_cache_only_for_cached_models() -> None:
    base = ["susvibes_eval", "no_cache"]
    assert run_suite.config_names_for({"cache": True}, base, "m") == ["susvibes_eval", "m"]
    assert run_suite.config_names_for({"cache": False}, base, "m") == ["susvibes_eval", "no_cache", "m"]


def test_write_model_config(tmp_path: Path) -> None:
    entry = {"label": "x", "api_base": "https://e", "api_key": "$K", "agent_model": {"top_p": None}}
    name = run_suite.write_model_config(entry, tmp_path)
    written = yaml.safe_load((tmp_path / "config" / f"{name}.yaml").read_text())
    assert written == {"agent": {"model": {"top_p": None, "api_base": "https://e", "api_key": "$K"}}}
