Completed Lane E tight OpenRouter artifact metadata hardening.

- Confirmed the prior tight artifact incorrectly used Greenhouse suite/repo metadata.
- Added `eval-realworld-tight-openrouter` to `Makefile`.
- Ran `make eval-realworld-tight-openrouter`.
- Confirmed `eval_run_acg.json` now uses suite `realworld-nestjs-tight`.
- Confirmed `eval_run_acg.json` repo URL contains `lujakob/nestjs-realworld-example-app`.
- Added `tests/test_realworld_artifact_metadata.py`.
- Passed `./.venv/bin/python -m pytest tests/test_realworld_artifact_metadata.py -v`.
- Passed `./.venv/bin/python -m pytest tests/ -q`.
