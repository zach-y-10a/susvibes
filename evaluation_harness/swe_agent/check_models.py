"""Check every suite model can do what a SWE-agent run needs, with two real API calls each.

Builds each model exactly as run_suite.py configures it (same config stack, same SWE-agent model
class, same tools, history processors applied), then:
  1. asks it to call the bash tool;
  2. replays that tool call and its result the way SWE-agent does (text and tool calls only;
     reasoning blocks are not sent back) and asks for one more step.
This catches a blocked API host, a wrong model ID or endpoint, a rejected parameter, a model that
won't use tools, and a provider that refuses history without its reasoning blocks. Costs a few cents.

Run from the SusVibes repo root in the SWE-agent env, with the API keys exported:
    conda run -n sweagent1.1.0 --live-stream python evaluation_harness/swe_agent/check_models.py [--models ...]
"""

import argparse
import copy
import json
import os
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from suite_config import config_names_for, install_configs, load_models, load_setting, write_model_config  # noqa: E402

FIRST_PROMPT = (
    "This is a connectivity check for a coding agent harness. Call the bash tool exactly once "
    "with the command `echo susvibes-check`. Do not call any other tool."
)
SECOND_PROMPT = "Good. Now reply with the single word DONE and do not call any tool."


def build_model(entry: dict, setting: dict):
    """The SWE-agent model object and history processors run_suite would use for this entry."""
    from sweagent.agent.models import get_model
    from sweagent.run.common import BasicCLI
    from sweagent.run.run_single import RunSingleConfig

    names = config_names_for(entry, setting["config_name"], write_model_config(entry, setting["sweagent_dir"]))
    args = [f"--config=config/{name}.yaml" for name in names] + [
        f"--agent.model.name={entry['model']}",
        "--agent.model.per_instance_cost_limit=0",
        "--agent.model.per_instance_call_limit=0",
    ]
    config = BasicCLI(RunSingleConfig, help_text="").get_config(args)
    agent_config = config.agent
    return get_model(agent_config.model, agent_config.tools), agent_config.history_processors


def query(model, processors, history: list[dict]) -> dict:
    processed = copy.deepcopy(history)
    for processor in processors:
        processed = processor(processed)
    return model.query(processed)


def check(entry: dict, setting: dict) -> dict:
    model, processors = build_model(entry, setting)
    history = [
        {"role": "system", "content": "You are a helpful assistant that can interact with a computer."},
        {"role": "user", "content": FIRST_PROMPT},
    ]
    started = time.time()
    first = query(model, processors, history)
    tool_calls = first.get("tool_calls") or []
    result = {"first_call_s": round(time.time() - started, 1), "tool_calls": len(tool_calls)}
    if not tool_calls:
        result.update(ok=False, problem="no tool call in the first reply", reply=first.get("message", "")[:200])
        return result
    call = tool_calls[0]
    result["tool"] = call.get("function", {}).get("name")
    # SWE-agent's own history entries for an action and its observation.
    history += [
        {"role": "assistant", "content": first.get("message", ""), "tool_calls": [call]},
        {"role": "tool", "content": "susvibes-check", "tool_call_ids": [call["id"]]},
        {"role": "user", "content": SECOND_PROMPT},
    ]
    started = time.time()
    second = query(model, processors, history)
    result.update(
        ok=True,
        second_call_s=round(time.time() - started, 1),
        second_reply=(second.get("message") or "")[:80],
        cost=round(model.stats.instance_cost, 4),
    )
    return result


def main():
    parser = argparse.ArgumentParser(description="Two-call API check per suite model.")
    parser.add_argument("--models", type=str, nargs="+", default=None, help="Only these model labels.")
    args = parser.parse_args()

    setting = load_setting()
    install_configs(setting["sweagent_dir"])
    os.chdir(setting["sweagent_dir"])  # SWE-agent resolves config/ and tools/ against its codebase
    results = {}
    for entry in load_models(labels=args.models):
        label = entry["label"]
        print(f"--- {label} ({entry['model']})", flush=True)
        try:
            results[label] = check(entry, setting)
        except Exception as exc:
            traceback.print_exc(limit=2)
            results[label] = {"ok": False, "problem": f"{type(exc).__name__}: {str(exc)[:300]}"}
        print(json.dumps(results[label]), flush=True)

    print("\n=== Summary ===")
    for label, result in results.items():
        print(f"{'OK  ' if result['ok'] else 'FAIL'} {label}  {result.get('problem', '')}")
    if not all(result["ok"] for result in results.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
