# Diagnostic scripts

One-shot probes for hand-debugging the LLM servers. Not part of the test
suite or CI.

| Script | What it does |
|--------|--------------|
| `llm_call_probe.py` | Sends 4 parallel chat completions (1 orchestrator + 3 workers) and prints token/timing telemetry. Useful for verifying both ports are alive. |
| `reasoning_content_probe.py` | Probes the orchestrator-port server for `reasoning_content` field presence; useful when changing reasoning-budget config. |

Run with `python scripts/diagnostics/<script>.py`.
