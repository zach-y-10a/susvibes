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


# ── Offline VM support: prebuilt agent images, local-image eval ─────────────────────────────


def test_agent_image_name_drops_registry_and_tag() -> None:
    import prebuilt

    name = prebuilt.agent_image_name("registry.io/songwen6968/susvibes.x86_64.eval_foo_abc:latest")
    assert name == f"susvibes-agent/susvibes.x86_64.eval_foo_abc:swerex-{prebuilt.SWEREX_VERSION}"
    assert f"swe-rex=={prebuilt.SWEREX_VERSION}" in prebuilt.AGENT_DOCKERFILE


def test_prebuilt_task_skips_build_and_pull(tmp_path: Path) -> None:
    from susvibes.core.agents.sweagent import SWEAgentPort

    port = SWEAgentPort("r", tmp_path, "env", ["c"], {"name": "m"}, 1, tmp_path)
    port.add_task(repo_type="preexisting", problem_statement="p", instance_id="i", repo_name="project",
                  image="susvibes-agent/x:y", python_standalone_dir=None, pull="never")
    deployment = port.task_instances[0]["env"]["deployment"]
    assert deployment["python_standalone_dir"] is None and deployment["pull"] == "never"


def test_eval_uses_local_image_when_pull_is_missing(monkeypatch) -> None:
    from unittest import mock

    from susvibes.core import env
    from susvibes.core.constants import ImageLoc

    fake_client = mock.MagicMock()
    monkeypatch.setattr(env, "docker_client", fake_client)
    monkeypatch.setenv("SV_IMAGE_PULL", "missing")
    image = env.Deployment.collect_image("ns/img:latest", image_loc=ImageLoc.REMOTE)
    assert image is fake_client.images.get.return_value
    fake_client.images.pull.assert_not_called()

    monkeypatch.delenv("SV_IMAGE_PULL")
    env.Deployment.collect_image("ns/img:latest", image_loc=ImageLoc.REMOTE)
    fake_client.images.pull.assert_called_once()


def test_baked_tool_install_matches_patch() -> None:
    import prebuilt

    patch = (SWE_AGENT_DIR / "patches" / "sweagent-v1.1.0-edit-tool-install.patch").read_text()
    added = [line[1:] for line in patch.splitlines() if line.startswith("+") and not line.startswith("+++")]
    assert "\n".join(added) + "\n" == prebuilt.TOOL_INSTALL_SCRIPT
