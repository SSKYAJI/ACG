.PHONY: install scan compile demo benchmark test lint clean viz-install viz

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
