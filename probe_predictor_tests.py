"""Probe: replay the exact predictor prompt for the `tests` task against live
Gemma, and show what the model returned + how the parser interpreted it.

Usage:
    ./.venv/bin/python probe_predictor_tests.py

Prints:
    1. The full system + user prompt that compile_lockfile would send.
    2. The raw Gemma reply.
    3. The parsed `PredictedWrite` list (which becomes `predicted_writes`).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from acg.llm import LLMClient
from acg.predictor import _build_prompt, _parse_llm_writes, _static_seed, _symbol_seed, _topical_seed
from acg.schema import TasksInput

REPO = Path(__file__).resolve().parent / "demo-app"
TASKS_FILE = REPO / "tasks.json"
GRAPH_FILE = REPO / ".acg" / "context_graph.json"


def main() -> None:
    tasks_input = TasksInput.model_validate_json(TASKS_FILE.read_text())
    repo_graph = json.loads(GRAPH_FILE.read_text())

    tests_task = next(t for t in tasks_input.tasks if t.id == "tests")
    print(f"--- task input ---\n{tests_task.model_dump_json(indent=2)}\n")

    # Replicate predictor.predict_writes seed pipeline (no LLM yet).
    static = _static_seed(tests_task.prompt)
    symbol = _symbol_seed(tests_task.prompt, repo_graph)
    topical = (
        _topical_seed(list(tests_task.hints.touches), repo_graph)
        if tests_task.hints and tests_task.hints.touches
        else []
    )
    print(f"--- seeds ---")
    print(f"  static  ({len(static)}): {[s.path for s in static]}")
    print(f"  symbol  ({len(symbol)}): {[s.path for s in symbol]}")
    print(f"  topical ({len(topical)}): {[s.path for s in topical]}")

    seeds = static + symbol + topical
    messages = _build_prompt(tests_task, repo_graph, seeds)

    print("\n--- system prompt ---")
    print(messages[0]["content"])
    print("\n--- user prompt (truncated to 1200 chars) ---")
    print(messages[1]["content"][:1200])
    print(f"\n[user prompt total length: {len(messages[1]['content'])} chars]")

    # Send to live Gemma.
    url = os.environ.get("ACG_LLM_URL", "http://gx10-f2c9:8080/v1")
    model = os.environ.get("ACG_LLM_MODEL", "gemma")
    api_key = os.environ.get("ACG_LLM_API_KEY", "local")
    print(f"\n--- calling LLM ---\n  url={url}\n  model={model}")

    client = LLMClient(base_url=url, model=model, api_key=api_key)
    raw = client.complete(messages)

    print(f"\n--- raw reply (len={len(raw)}) ---")
    print(raw)

    parsed = _parse_llm_writes(raw)
    print(f"\n--- parsed writes ({len(parsed)}) ---")
    for pw in parsed:
        print(f"  {pw.path} ({pw.confidence}) — {pw.reason}")


if __name__ == "__main__":
    main()
