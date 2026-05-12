.PHONY: install scan compile demo benchmark test lint clean viz-install viz gemma-ping compile-gemma demo-gemma run-gemma run-mock setup-greenhouse compile-greenhouse eval-greenhouse-mock eval-greenhouse-local eval-greenhouse-applied-diff eval-greenhouse-devin-manual eval-greenhouse-devin-api eval-greenhouse-tight-mock eval-greenhouse-report mcp-serve cascade-hook-test setup-realworld compile-realworld compile-realworld-blind setup-python-fastapi compile-python-fastapi eval-python-fastapi-mock analyze-python-fastapi-mock eval-realworld-local eval-realworld-blind-local eval-realworld-blind-openrouter-ablation eval-realworld-tight-openrouter eval-realworld-mock analyze-realworld analyze-realworld-blind analyze-realworld-blind-openrouter

# Override these on the command line if your ASUS hostname / port differ:
#   make compile-gemma GEMMA_HOST=100.x.y.z GEMMA_PORT=8080
# NOTE: do NOT put inline `# comments` after the value — GNU make preserves the
# trailing whitespace, which then breaks the URL expansion below.
GEMMA_HOST      ?= gx10-f2c9
# sub-agent / predictor server (--reasoning-budget 0)
GEMMA_PORT      ?= 8080
# orchestrator server (thinking enabled)
GEMMA_ORCH_PORT ?= 8081
GEMMA_ENV       := ACG_LLM_URL=http://$(GEMMA_HOST):$(GEMMA_PORT)/v1 ACG_LLM_MODEL=gemma ACG_LLM_API_KEY=local
GEMMA_ORCH_ENV  := ACG_ORCH_URL=http://$(GEMMA_HOST):$(GEMMA_ORCH_PORT)/v1 ACG_ORCH_MODEL=gemma ACG_ORCH_API_KEY=local

# Stream Python stdout/stderr during long LLM runs (OpenRouter, compile, eval).
export PYTHONUNBUFFERED := 1

install:
	python3 -m venv .venv
	./.venv/bin/pip install -e ".[dev]"
	cd graph_builder && npm install

scan:
	./.venv/bin/acg init-graph --repo demo-app --language typescript

compile:
	./.venv/bin/acg compile --repo demo-app --tasks demo-app/tasks.json --out demo-app/agent_lock.json

explain:
	./.venv/bin/acg explain --lock demo-app/agent_lock.json

demo: compile
	./.venv/bin/acg explain --lock demo-app/agent_lock.json
	./.venv/bin/acg run-benchmark --mode naive   --repo demo-app --tasks demo-app/tasks.json --out .acg/run_naive.json
	./.venv/bin/acg run-benchmark --mode planned --repo demo-app --tasks demo-app/tasks.json --lock demo-app/agent_lock.json --out .acg/run_acg.json
	./.venv/bin/acg report --naive .acg/run_naive.json --planned .acg/run_acg.json --out docs/benchmark.png

benchmark: demo

test:
	./.venv/bin/python -m pytest tests/ -v

lint:
	./.venv/bin/ruff check acg/ tests/ benchmark/
	./.venv/bin/ruff format --check acg/ tests/ benchmark/

clean:
	rm -rf .acg demo-app/.acg demo-app/agent_lock.json __pycache__ .pytest_cache .ruff_cache

viz-install:
	cd viz && npm install

viz:
	cd viz && npm run dev

gemma-ping:
	@echo "sub-agents @ $(GEMMA_HOST):$(GEMMA_PORT)"
	@curl -fsS http://$(GEMMA_HOST):$(GEMMA_PORT)/v1/models | head -c 200 && echo
	@echo
	@echo "orchestrator @ $(GEMMA_HOST):$(GEMMA_ORCH_PORT)"
	@curl -fsS http://$(GEMMA_HOST):$(GEMMA_ORCH_PORT)/v1/models | head -c 200 && echo

compile-gemma:
	$(GEMMA_ENV) ./.venv/bin/acg compile --repo demo-app --tasks demo-app/tasks.json --out demo-app/agent_lock.json

demo-gemma: compile-gemma
	./.venv/bin/acg explain --lock demo-app/agent_lock.json
	$(GEMMA_ENV) ./.venv/bin/acg run-benchmark --mode naive   --repo demo-app --tasks demo-app/tasks.json --out .acg/run_naive.json
	$(GEMMA_ENV) ./.venv/bin/acg run-benchmark --mode planned --repo demo-app --tasks demo-app/tasks.json --lock demo-app/agent_lock.json --out .acg/run_acg.json
	./.venv/bin/acg report --naive .acg/run_naive.json --planned .acg/run_acg.json --out docs/benchmark.png

# Live Gemma runtime: orchestrator (port 8081, thinking) + sub-agents (port 8080).
run-gemma:
	$(GEMMA_ENV) $(GEMMA_ORCH_ENV) ./.venv/bin/acg run \
	  --lock demo-app/agent_lock.json \
	  --repo demo-app \
	  --out demo-app/.acg/run_trace.json

# Offline runtime against the deterministic mock client. No GX10 needed.
run-mock:
	./.venv/bin/acg run --mock \
	  --lock demo-app/agent_lock.json \
	  --repo demo-app \
	  --out demo-app/.acg/run_trace.json

# ----- Greenhouse (legacy-Java demo) -----

setup-greenhouse:
	bash experiments/greenhouse/setup.sh

compile-greenhouse: setup-greenhouse
	./.venv/bin/acg compile \
	  --tasks experiments/greenhouse/tasks.json \
	  --repo experiments/greenhouse/checkout \
	  --language java \
	  --out experiments/greenhouse/agent_lock.json

# ----- Greenhouse head-to-head eval (megaplan v0.1) -----
# Mock backend: deterministic, runs in <2s, CI-friendly. Writes
# experiments/greenhouse/runs/eval_run_naive.json + eval_run_acg.json.
eval-greenhouse-mock: compile-greenhouse
	./.venv/bin/python -m experiments.greenhouse.headtohead \
	  --lock experiments/greenhouse/agent_lock.json \
	  --tasks experiments/greenhouse/tasks.json \
	  --repo experiments/greenhouse/checkout \
	  --backend mock \
	  --strategy both \
	  --out-dir experiments/greenhouse/runs

# Live local LLM (GX10) — same harness, real worker calls.
eval-greenhouse-local: compile-greenhouse
	$(GEMMA_ENV) $(GEMMA_ORCH_ENV) ./.venv/bin/python -m experiments.greenhouse.headtohead \
	  --lock experiments/greenhouse/agent_lock.json \
	  --tasks experiments/greenhouse/tasks.json \
	  --repo experiments/greenhouse/checkout \
	  --backend local \
	  --strategy both \
	  --out-dir experiments/greenhouse/runs

# Generic applied-diff sidecars — each sidecar can name repo_path/base_ref
# plus per-task branch/head_ref/worktree details. The harness records
# git diff changed files as the primary collision evidence.
APPLIED_DIFF_RESULTS_NAIVE ?= experiments/greenhouse/runs/applied_diff_naive_raw.json
APPLIED_DIFF_RESULTS_ACG   ?= experiments/greenhouse/runs/applied_diff_acg_raw.json
eval-greenhouse-applied-diff: compile-greenhouse
	./.venv/bin/python -m experiments.greenhouse.headtohead \
	  --lock experiments/greenhouse/agent_lock.json \
	  --tasks experiments/greenhouse/tasks.json \
	  --backend applied-diff --strategy naive_parallel \
	  --diff-results $(APPLIED_DIFF_RESULTS_NAIVE) \
	  --out experiments/greenhouse/runs/eval_run_applied_diff_naive.json
	./.venv/bin/python -m experiments.greenhouse.headtohead \
	  --lock experiments/greenhouse/agent_lock.json \
	  --tasks experiments/greenhouse/tasks.json \
	  --backend applied-diff --strategy acg_planned \
	  --diff-results $(APPLIED_DIFF_RESULTS_ACG) \
	  --out experiments/greenhouse/runs/eval_run_applied_diff_acg.json

# Live Devin v3 API — submits real sessions against your Devin org. Reads
# DEVIN_API_KEY + DEVIN_ORG_ID from .env (or the shell). Set
# DEVIN_GITHUB_REPO_URL to the fork you connected to your Devin org.
DEVIN_GITHUB_REPO_URL ?= https://github.com/SSKYAJI/greenhouse.git
DEVIN_BASE_BRANCH     ?= master
DEVIN_MAX_ACU_LIMIT   ?= 50
eval-greenhouse-devin-api: compile-greenhouse
	./.venv/bin/python -m experiments.greenhouse.headtohead \
	  --lock experiments/greenhouse/agent_lock.json \
	  --tasks experiments/greenhouse/tasks.json \
	  --repo experiments/greenhouse/checkout \
	  --backend devin-api \
	  --strategy both \
	  --repo-url $(DEVIN_GITHUB_REPO_URL) \
	  --base-branch $(DEVIN_BASE_BRANCH) \
	  --max-acu-limit $(DEVIN_MAX_ACU_LIMIT) \
	  --out-dir experiments/greenhouse/runs

# Manual Devin sidecar — DEVIN_RESULTS_NAIVE / DEVIN_RESULTS_ACG point at
# JSON files exported (manually or programmatically) from Devin sessions.
DEVIN_RESULTS_NAIVE ?= experiments/greenhouse/runs/devin_naive_raw.json
DEVIN_RESULTS_ACG   ?= experiments/greenhouse/runs/devin_acg_raw.json
eval-greenhouse-devin-manual: compile-greenhouse
	./.venv/bin/python -m experiments.greenhouse.headtohead \
	  --lock experiments/greenhouse/agent_lock.json \
	  --tasks experiments/greenhouse/tasks.json \
	  --backend devin-manual --strategy naive_parallel \
	  --devin-results $(DEVIN_RESULTS_NAIVE) \
	  --out experiments/greenhouse/runs/eval_run_devin_naive.json
	./.venv/bin/python -m experiments.greenhouse.headtohead \
	  --lock experiments/greenhouse/agent_lock.json \
	  --tasks experiments/greenhouse/tasks.json \
	  --backend devin-manual --strategy acg_planned \
	  --devin-results $(DEVIN_RESULTS_ACG) \
	  --out experiments/greenhouse/runs/eval_run_devin_acg.json

# Tightened-scope eval: hand-edited lockfile with allowed_paths shrunk to
# the exact ground-truth files. The mock LockfileEchoMockLLM still echoes
# the original predicted_writes (which are wider than the ground truth),
# so the validator visibly fires and ``blocked_write_events`` is non-empty
# — the negative-control fixture the v2 megaplan calls for.
eval-greenhouse-tight-mock: setup-greenhouse
	./.venv/bin/python -m experiments.greenhouse.headtohead \
	  --lock experiments/greenhouse/agent_lock_tight.json \
	  --tasks experiments/greenhouse/tasks.json \
	  --repo experiments/greenhouse/checkout \
	  --backend mock \
	  --strategy both \
	  --out-dir experiments/greenhouse/runs/tight

# Render the markdown table + PNG chart for whatever eval_run files exist.
eval-greenhouse-report:
	./.venv/bin/python -m experiments.greenhouse.report \
	  experiments/greenhouse/runs/eval_run_naive.json \
	  experiments/greenhouse/runs/eval_run_acg.json \
	  --chart docs/greenhouse_benchmark.png

mcp-serve:
	./.venv/bin/acg mcp --transport stdio

# Quick smoke test of the Cascade hook script (exercises ALLOWED + BLOCKED).
cascade-hook-test:
	./.venv/bin/python -m pytest tests/test_precheck_write_script.py -v

# --- RealWorld NestJS benchmark ---
setup-realworld:
	bash experiments/realworld/setup.sh

compile-realworld: setup-realworld
	set -a && . ./.env && set +a && \
	./.venv/bin/acg compile \
	  --repo experiments/realworld/checkout \
	  --tasks experiments/realworld/tasks_explicit.json \
	  --language typescript \
	  --out experiments/realworld/agent_lock.json

compile-realworld-blind: setup-realworld
# Optional: ACG_COMPILE_TASK_CONCURRENCY=4 before make to parallelize predictor
# during compile (separate httpx clients per task; watch OpenRouter rate limits).
	set -a && . ./.env && set +a && \
	./.venv/bin/acg compile \
	  --repo experiments/realworld/checkout \
	  --tasks experiments/realworld/tasks_blind.json \
	  --language typescript \
	  --out experiments/realworld/agent_lock_blind.json

# --- Python FastAPI benchmark ---
setup-python-fastapi:
	bash experiments/python_fastapi/setup.sh

compile-python-fastapi: setup-python-fastapi
	./.venv/bin/acg compile \
	  --repo experiments/python_fastapi/checkout \
	  --tasks experiments/python_fastapi/tasks.json \
	  --language python \
	  --out experiments/python_fastapi/agent_lock.json

eval-python-fastapi-mock: compile-python-fastapi
	./.venv/bin/python -m experiments.greenhouse.headtohead \
	  --lock experiments/python_fastapi/agent_lock.json \
	  --tasks experiments/python_fastapi/tasks.json \
	  --repo experiments/python_fastapi/checkout \
	  --backend mock \
	  --strategy both \
	  --suite-name python-fastapi-template \
	  --out-dir experiments/python_fastapi/runs_mock

analyze-python-fastapi-mock:
	./.venv/bin/acg analyze-runs \
	  experiments/python_fastapi/runs_mock/eval_run_combined.json \
	  --out experiments/python_fastapi/runs_mock/analysis_report.md \
	  --json-out experiments/python_fastapi/runs_mock/analysis_report.json

eval-realworld-local:
	set -a && . ./.env && set +a && \
	./.venv/bin/python -m experiments.greenhouse.headtohead \
	  --lock experiments/realworld/agent_lock.json \
	  --tasks experiments/realworld/tasks_explicit.json \
	  --repo experiments/realworld/checkout \
	  --backend local --strategy both \
	  --out-dir experiments/realworld/runs \
	  --suite-name realworld-nestjs-explicit

eval-realworld-blind-local:
	set -a && . ./.env && set +a && \
	./.venv/bin/python -m experiments.greenhouse.headtohead \
	  --lock experiments/realworld/agent_lock_blind.json \
	  --tasks experiments/realworld/tasks_blind.json \
	  --repo experiments/realworld/checkout \
	  --backend local --strategy both \
	  --out-dir experiments/realworld/runs_blind \
	  --suite-name realworld-nestjs-blind

eval-realworld-blind-openrouter-ablation:
	set -a && . ./.env && set +a && \
	./.venv/bin/python -m experiments.greenhouse.headtohead \
	  --lock experiments/realworld/agent_lock_blind.json \
	  --tasks experiments/realworld/tasks_blind.json \
	  --repo experiments/realworld/checkout \
	  --backend local --strategy ablation \
	  --out-dir experiments/realworld/runs_blind_openrouter \
	  --suite-name realworld-nestjs-blind-openrouter-v2

eval-realworld-tight-openrouter:
	set -a && . ./.env && set +a && \
	./.venv/bin/python -m experiments.greenhouse.headtohead \
	  --lock experiments/realworld/agent_lock_tight.json \
	  --tasks experiments/realworld/tasks_blind.json \
	  --repo experiments/realworld/checkout \
	  --backend local --strategy ablation \
	  --out-dir experiments/realworld/runs/tight \
	  --suite-name realworld-nestjs-tight

eval-realworld-mock:
	./.venv/bin/python -m experiments.greenhouse.headtohead \
	  --lock experiments/realworld/agent_lock.json \
	  --tasks experiments/realworld/tasks_explicit.json \
	  --repo experiments/realworld/checkout \
	  --backend mock --strategy both \
	  --out-dir experiments/realworld/runs_mock \
	  --suite-name realworld-nestjs-explicit

analyze-realworld:
	./.venv/bin/acg analyze-runs \
	  experiments/realworld/runs/eval_run_combined.json \
	  --out experiments/realworld/runs/analysis_report.md \
	  --json-out experiments/realworld/runs/analysis_report.json

analyze-realworld-blind:
	./.venv/bin/acg analyze-runs \
	  experiments/realworld/runs_blind/eval_run_combined.json \
	  --out experiments/realworld/runs_blind/analysis_report.md \
	  --json-out experiments/realworld/runs_blind/analysis_report.json

analyze-realworld-blind-openrouter:
	./.venv/bin/acg analyze-runs \
	  experiments/realworld/runs_blind_openrouter/eval_run_combined.json \
	  --out experiments/realworld/runs_blind_openrouter/analysis_report.md \
	  --json-out experiments/realworld/runs_blind_openrouter/analysis_report.json
