# Diagnostic scripts

One-shot probes for hand-debugging the LLM servers. Not part of the test
suite or CI.

| Script                       | What it does                                                                                                                                                                                                                                                                                                                                                               |
| ---------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `llm_call_probe.py`          | Sends 4 parallel chat completions (1 orchestrator + 3 workers) and prints token/timing telemetry. Useful for verifying both ports are alive.                                                                                                                                                                                                                               |
| `reasoning_content_probe.py` | Probes the orchestrator-port server for `reasoning_content` field presence; useful when changing reasoning-budget config.                                                                                                                                                                                                                                                  |
| `devin_api_probe.py`         | Discovers undocumented Devin v3 API endpoints (poll, message extraction, files/diff). Creates one tiny `pong`-only session against `$DEVIN_ORG_ID` with `max_acu_limit=1`, then sweeps a list of guessed paths and dumps the full request/response trace to JSON. Required to fill `experiments/greenhouse/devin_adapter.py:devin_api_run` once endpoint shapes are known. |

Run with `python scripts/diagnostics/<script>.py`.

For `devin_api_probe.py`:

```bash
export DEVIN_API_KEY=cog_xxxx
export DEVIN_ORG_ID=org_xxxx
# Sanity-check what would be sent without spending any ACUs:
./.venv/bin/python scripts/diagnostics/devin_api_probe.py --dry-run
# Real probe (will create one session):
./.venv/bin/python scripts/diagnostics/devin_api_probe.py \
  --out scripts/diagnostics/devin_probe_output.json
```

The trace file redacts the bearer token before writing — safe to share.
