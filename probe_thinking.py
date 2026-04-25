"""Probe: can we re-enable Gemma thinking per-request while server has --reasoning-budget 0?

Runs the same orchestrator-style prompt several times against the live llama-server,
each with a different "make it think" payload knob, so we can compare:
  - which knobs the server actually honors (vs silently ignores)
  - whether content still comes back
  - whether reasoning_content is populated

Usage:
    LLM_URL=http://gx10-f2c9:8080/v1/chat/completions python3 probe_thinking.py
"""

from __future__ import annotations

import json
import os
import time

import httpx

URL = os.environ.get("LLM_URL", "http://gx10-f2c9:8080/v1/chat/completions")

ORCHESTRATOR_PROMPT = [
    {
        "role": "system",
        "content": (
            "You are an orchestrator that analyzes coding tasks for write conflicts "
            "and proposes an execution order. Reason carefully before answering."
        ),
    },
    {
        "role": "user",
        "content": (
            "Three tasks:\n"
            "  A: modifies prisma/schema.prisma\n"
            "  B: modifies prisma/schema.prisma and src/components/Sidebar.tsx\n"
            "  C: modifies src/components/Sidebar.tsx\n\n"
            "Identify the conflicts and propose an execution order. "
            "Output ONLY a JSON object with keys 'conflicts' (list) and 'order' (list of task ids)."
        ),
    },
]

CASES: list[tuple[str, dict]] = [
    ("baseline (no override)", {}),
    (
        "chat_template_kwargs.enable_thinking=true",
        {"chat_template_kwargs": {"enable_thinking": True}},
    ),
    (
        "chat_template_kwargs.thinking=true",
        {"chat_template_kwargs": {"thinking": True}},
    ),
    ("reasoning_effort=high", {"reasoning_effort": "high"}),
    ("reasoning_budget=2048", {"reasoning_budget": 2048}),
    ("thinking=true (root)", {"thinking": True}),
]


def run_case(name: str, extra: dict) -> None:
    payload: dict = {
        "model": "gemma",
        "messages": ORCHESTRATOR_PROMPT,
        "max_tokens": 2048,
        "temperature": 0.2,
        "stream": False,
    }
    payload.update(extra)

    t0 = time.perf_counter()
    try:
        r = httpx.post(URL, json=payload, timeout=120.0)
    except httpx.HTTPError as exc:
        print(f"\n=== {name} ===\ntransport error: {exc}")
        return
    dt = time.perf_counter() - t0

    try:
        data = r.json()
    except Exception:
        print(f"\n=== {name} ===\nHTTP {r.status_code}: {r.text[:300]}")
        return

    if "choices" not in data:
        print(f"\n=== {name} ===\nERROR: {json.dumps(data)[:300]}")
        return

    choice = data["choices"][0]
    msg = choice["message"]
    finish = choice.get("finish_reason")
    usage = data.get("usage", {})
    content = msg.get("content") or ""
    reasoning = msg.get("reasoning_content") or ""

    print(f"\n=== {name} ===")
    print(
        f"wall: {dt:.2f}s  tokens: {usage.get('completion_tokens', 0)}  "
        f"finish: {finish}"
    )
    print(f"keys: {list(msg.keys())}")
    print(f"content_len: {len(content)}  reasoning_len: {len(reasoning)}")
    if content:
        print(f"content[:200]: {content[:200]!r}")
    if reasoning:
        print(f"reasoning[:200]: {reasoning[:200]!r}")


def main() -> None:
    print(f"Probing {URL}\n")
    for name, extra in CASES:
        run_case(name, extra)


if __name__ == "__main__":
    main()
