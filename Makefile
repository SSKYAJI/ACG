.PHONY: install scan compile demo benchmark test lint clean viz-install viz gemma-ping compile-gemma demo-gemma

# Override these on the command line if your ASUS hostname / port differ:
#   make compile-gemma GEMMA_HOST=100.x.y.z GEMMA_PORT=8080
GEMMA_HOST      ?= gx10-f2c9
GEMMA_PORT      ?= 8080   # sub-agent / predictor server (--reasoning-budget 0)
GEMMA_ORCH_PORT ?= 8081   # orchestrator server (thinking enabled)
GEMMA_ENV       := ACG_LLM_URL=http://$(GEMMA_HOST):$(GEMMA_PORT)/v1 ACG_LLM_MODEL=gemma ACG_LLM_API_KEY=local
GEMMA_ORCH_ENV  := ACG_ORCH_URL=http://$(GEMMA_HOST):$(GEMMA_ORCH_PORT)/v1 ACG_ORCH_MODEL=gemma ACG_ORCH_API_KEY=local


install:
	python3 -m venv .venv
	./.venv/bin/pip install -e ".[dev]"
	cd graph_builder && npm install

scan:
	cd graph_builder && npm run scan -- --repo ../demo-app --out ../demo-app/.acg/context_graph.json

compile: scan
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

compile-gemma: scan
	$(GEMMA_ENV) ./.venv/bin/acg compile --repo demo-app --tasks demo-app/tasks.json --out demo-app/agent_lock.json

demo-gemma: compile-gemma
	./.venv/bin/acg explain --lock demo-app/agent_lock.json
	$(GEMMA_ENV) ./.venv/bin/acg run-benchmark --mode naive   --repo demo-app --tasks demo-app/tasks.json --out .acg/run_naive.json
	$(GEMMA_ENV) ./.venv/bin/acg run-benchmark --mode planned --repo demo-app --tasks demo-app/tasks.json --lock demo-app/agent_lock.json --out .acg/run_acg.json
	./.venv/bin/acg report --naive .acg/run_naive.json --planned .acg/run_acg.json --out docs/benchmark.png
