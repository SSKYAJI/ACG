# Lane O1 Done

Selected at: 2026-05-07T05:16:50Z

Final selected repos:

- `fastify/fastify` (`experiments/real_repos/fastify/checkout`)
  - Language: JavaScript
  - License: MIT
  - Source file count: 55
  - Baseline test command: `npm test`
  - Baseline result: passed
  - Tasks:
    - PR #6694: perf: cache parsed ContentType objects in ContentTypeParser
    - PR #6692: perf: defer ContentType parsing in getSchemaSerializer until needed
    - PR #6653: feat: add request.mediaType

- `Kludex/starlette` (`experiments/real_repos/starlette/checkout`)
  - Language: Python
  - License: BSD-3-Clause
  - Source file count: 34
  - Baseline test command: `./.venv/bin/python -m pytest`
  - Baseline result: passed
  - Tasks:
    - PR #3166: Track session access and modification in `SessionMiddleware`
    - PR #3148: Enable `autoescape` by default in `Jinja2Templates`
    - PR #3137: Return explicit origin in CORS response when credentials are allowed

- `psf/black` (`experiments/real_repos/black/checkout`)
  - Language: Python
  - License: MIT
  - Source file count: 49
  - Baseline test command: `./.venv/bin/python -m pytest tests -k "not incompatible_with_mypyc" -n auto`
  - Baseline result: passed
  - Tasks:
    - PR #5080: Fix blackd error handling: split SourceASTParseError from ASTSafetyError
    - PR #5039: Harden blackd browser-facing request handling
    - PR #5092: In stub files, enforce a blank line between a function and a decorated class definition

Task total: 9

Repo tests:

- Initial cognition baseline: `211 passed`
- Fastify baseline: passed
- Starlette baseline: `929 passed, 2 xfailed`
- Black baseline: `496 passed, 2 skipped`

Rejected after clone/setup:

- `pallets/flask`: 24 production source files, below the 30 file minimum.
- `tj/commander.js`: 12 production source files, below the 30 file minimum.
- `expressjs/express`: 7 production source files, below the 30 file minimum.
- `urllib3/urllib3`: local full baseline failed with TLS/proxy dummy-server failures.
- `pallets/werkzeug`: local full baseline failed with dev-server timeout failures.
