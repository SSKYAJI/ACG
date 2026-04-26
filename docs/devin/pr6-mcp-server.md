> **Goal:** turn ACG's four CLI primitives into MCP tools so Devin
> Manage Devins, Claude Code, Cursor, and OpenCode can call them
> directly. This closes the "MCP wrapper is on the roadmap" item in
> `docs/COGNITION_INTEGRATION.md`.

---

## Project context

You are working on **`cognition`** — a Python+TypeScript repo whose product
is **ACG (Agent Context Graph)**. ACG already exposes its core primitives
through a Typer CLI (`acg compile`, `acg validate-write`, `acg
explain`, `acg validate-lockfile`). The README and
`docs/COGNITION_INTEGRATION.md` both say the **same primitives are
intentionally designed to land as MCP tools** for sponsor consumption,
but the FastMCP wrapper has not been written yet.

Your job is to ship that wrapper as a new `acg/mcp/` package, expose it
as an `acg mcp` subcommand, and document the tool surface in
`docs/MCP_SERVER.md`.

This is a **scoped** integration PR: do not modify `compile_lockfile`,
`predict_writes`, `validate_write`, or any of the existing CLI commands.
You are gluing existing functions into MCP transport handlers.

## Repo state to assume

- `main` contains PR 1-4 + the `init-graph` repo-graph normalization.
- `acg/cli.py` registers Typer commands; you add a new `mcp` subcommand
  alongside the existing ones.
- `acg/compiler.py::compile_lockfile`, `acg/predictor.py::predict_writes`,
  `acg/enforce.py::validate_write` are stable public functions.
- `acg/repo_graph.py::scan_context_graph` is the normalized graph
  builder; the MCP `analyze_repo` tool wraps this.
- The Cognition track values **shipped MCP integrations** — this PR
  turns the "(roadmap)" footnote into a "(shipped)" badge in three
  places (README, COGNITION_INTEGRATION, HANDOFF).

## Reference: planned tool surface

From `docs/COGNITION_INTEGRATION.md`:

```python
analyze_repo(path: str) -> dict
predict_writes(task: dict, repo_graph: dict) -> list[dict]
compile_lockfile(repo: str, tasks: dict) -> dict
validate_writes(lockfile: dict, task_id: str, attempted_path: str) -> dict
```

Each of these maps to one or two existing Python functions. Your MCP
server must expose them under exactly those names.

## Deliverables — file by file

### 1. `pyproject.toml` — add an optional dependency group

Add **only this** group; do not modify existing entries:

```toml
[project.optional-dependencies]
dev = ["pytest>=8.0,<9.0", "ruff>=0.5,<1.0"]
mcp = ["fastmcp>=2.0,<3.0"]
```

The `mcp` extra is opt-in so users who only want the CLI don't pull in
FastMCP. Update the comment that currently says "MCP server (FastMCP)
... are roadmap items" to read "MCP server (FastMCP) is shipped under
the `mcp` extra; see `docs/MCP_SERVER.md`."

### 2. `acg/mcp/__init__.py`

```python
"""MCP server entrypoint for ACG.

Exposes the four ACG primitives (analyze_repo, predict_writes,
compile_lockfile, validate_writes) as MCP tools using FastMCP.

Designed to be consumed by:
- Devin Manage Devins (coordinator pre-flight)
- Claude Code / Cursor (write boundary enforcement)
- OpenCode (per-task lock subsystem requested in issue #4278)
"""

from .server import build_server, run_stdio

__all__ = ["build_server", "run_stdio"]
```

### 3. `acg/mcp/server.py`

The substantive module. Single file, ~250 LOC.

```python
"""FastMCP server for ACG."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from acg.compiler import compile_lockfile
from acg.enforce import validate_write
from acg.llm import LLMClient
from acg.predictor import predict_writes
from acg.repo_graph import load_context_graph, scan_context_graph
from acg.schema import AgentLock, TaskInput, TaskInputHints, TasksInput


def build_server() -> "FastMCP":
    """Construct a FastMCP server registered with all four ACG tools."""
    from fastmcp import FastMCP  # imported lazily so the optional dep is truly opt-in

    server = FastMCP(name="acg", version="0.1.0")

    @server.tool()
    def analyze_repo(path: str, language: str = "auto") -> dict[str, Any]:
        """Build / refresh the deterministic context graph for `path`.

        Wraps `acg.repo_graph.scan_context_graph`. Returns the normalized
        graph dict (files, symbols_index, imports, exports, hotspots,
        routes, configs, tests, languages).
        """
        repo = Path(path).resolve()
        return scan_context_graph(repo, language)

    @server.tool()
    def predict_writes_tool(
        task: dict[str, Any],
        repo_path: str,
        repo_graph: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Predict the file write-set for a single task.

        `task` must contain `id` and `prompt`; `hints.touches` optional.
        Returns a list of {path, confidence, reason} dicts.
        """
        repo = Path(repo_path).resolve()
        graph = repo_graph or load_context_graph(repo) or scan_context_graph(repo, "auto")
        hints = task.get("hints") or {}
        task_input = TaskInput(
            id=task["id"],
            prompt=task["prompt"],
            hints=TaskInputHints(**hints) if hints else None,
        )
        llm = LLMClient.from_env()
        writes = predict_writes(task_input, graph, llm, repo_root=repo)
        return [w.model_dump() for w in writes]

    @server.tool()
    def compile_lockfile_tool(
        repo_path: str,
        tasks: dict[str, Any],
        language: str = "auto",
    ) -> dict[str, Any]:
        """Compile a tasks document into a full agent_lock.json dict.

        `tasks` must conform to TasksInput (i.e. `{"version": "1.0",
        "tasks": [...]}`).
        """
        repo = Path(repo_path).resolve()
        tasks_input = TasksInput.model_validate(tasks)
        graph = scan_context_graph(repo, language)
        llm = LLMClient.from_env()
        lock = compile_lockfile(repo, tasks_input, graph, llm)
        return lock.model_dump(mode="json")

    @server.tool()
    def validate_writes_tool(
        lockfile: dict[str, Any],
        task_id: str,
        attempted_path: str,
    ) -> dict[str, Any]:
        """Validate a single attempted write against a lockfile in memory.

        Returns {"allowed": bool, "reason": str}. Mirrors the CLI's
        `acg validate-write` exit-code contract for in-process use.
        """
        lock = AgentLock.model_validate(lockfile)
        result = validate_write(lock=lock, task_id=task_id, path=attempted_path)
        return {"allowed": result.allowed, "reason": result.reason or ""}

    return server


def run_stdio() -> None:
    """Boot the MCP server on stdio — the standard transport for tool hosts."""
    server = build_server()
    server.run()
```

Notes:

- Tools are named `analyze_repo`, `predict_writes_tool`,
  `compile_lockfile_tool`, `validate_writes_tool` in the source but
  FastMCP's `@server.tool()` decorator preserves their unwrapped names
  for the protocol — the registered tool names should be exactly the
  four from `docs/COGNITION_INTEGRATION.md`. **Verify by reading
  FastMCP's docs** that the decorator uses the function's `__name__` by
  default; if it does, rename the Python functions to drop the `_tool`
  suffix. If it lets you pass an explicit name, use the explicit-name
  form.
- `validate_write`'s real return shape lives in `acg/enforce.py`. Read
  it and adapt the dict construction to whatever fields exist (a
  `ValidationResult` dataclass or similar). If the function is
  exit-code-only, wrap it via the lockfile's `Task.allowed_paths` glob
  list directly — but the cleaner path is the dataclass return.
- All four tools must be **idempotent and side-effect-free** except for
  writing the context graph file. Document that in the docstring.

### 4. `acg/cli.py` — add an `acg mcp` subcommand

Add a single new Typer command **at the bottom** of the existing app
registration, after `cmd_run_benchmark`:

```python
@app.command("mcp")
def cmd_mcp(
    transport: Annotated[
        str,
        typer.Option(help="MCP transport. Only 'stdio' is supported today."),
    ] = "stdio",
) -> None:
    """Run the ACG MCP server (requires the `mcp` extra: pip install -e .[mcp])."""
    if transport != "stdio":
        _err_console.print(f"[red]unsupported --transport {transport!r}; expected 'stdio'[/]")
        raise typer.Exit(code=EXIT_USER_ERROR)
    try:
        from acg.mcp import run_stdio
    except ImportError as exc:
        _err_console.print(
            "[red]MCP extra not installed.[/] Run: "
            "[bold]pip install -e '.[mcp]'[/]"
        )
        raise typer.Exit(code=EXIT_USER_ERROR) from exc
    run_stdio()
```

Do not modify any other CLI command. The new command is the **only**
edit to `acg/cli.py`.

### 5. `docs/MCP_SERVER.md`

A new ~1-page doc:

````markdown
# ACG MCP Server

ACG ships an MCP server that exposes its four core primitives as
network-callable tools. Designed for consumption by Devin Manage Devins,
Claude Code, Cursor, and OpenCode.

## Install

```bash
pip install -e '.[mcp]'
```
````

## Run

```bash
acg mcp                    # stdio transport, blocks until host disconnects
```

Wire as a child process under your MCP host. With Devin's MCP config:

```json
{
  "mcpServers": {
    "acg": {
      "command": "acg",
      "args": ["mcp"]
    }
  }
}
```

## Tools

| Tool               | Inputs                                                                         | Output                               |
| ------------------ | ------------------------------------------------------------------------------ | ------------------------------------ |
| `analyze_repo`     | `path` (str), `language` (str, default `auto`)                                 | normalized context-graph dict        |
| `predict_writes`   | `task` (dict), `repo_path` (str), `repo_graph` (dict, optional)                | list of `{path, confidence, reason}` |
| `compile_lockfile` | `repo_path` (str), `tasks` (TasksInput dict), `language` (str, default `auto`) | full `agent_lock.json` dict          |
| `validate_writes`  | `lockfile` (dict), `task_id` (str), `attempted_path` (str)                     | `{allowed: bool, reason: str}`       |

## Worked example: Devin coordinator pre-flight

```python
graph = await mcp.call("acg", "analyze_repo", {"path": "/repo"})
lock = await mcp.call("acg", "compile_lockfile", {
    "repo_path": "/repo",
    "tasks": {"version": "1.0", "tasks": [...]},
})
for group in lock["execution_plan"]["groups"]:
    await asyncio.gather(*[spawn_child(task_id) for task_id in group["tasks"]])
    for task_id in group["tasks"]:
        for attempted_path in child_writes[task_id]:
            verdict = await mcp.call("acg", "validate_writes", {
                "lockfile": lock,
                "task_id": task_id,
                "attempted_path": attempted_path,
            })
            if not verdict["allowed"]:
                rollback(task_id, attempted_path, verdict["reason"])
```

## Limitations

- `analyze_repo` writes to `<repo>/.acg/context_graph.json`. Mount the
  repo writable.
- `compile_lockfile` requires `ACG_LLM_*` environment variables set
  inside the MCP server process; configure them via your host's
  per-server env block.
- TypeScript repos require `node` + `npm` on PATH (the graph builder
  shells out to `graph_builder/scan.ts`).

````

### 6. `tests/test_mcp.py`

A new test module with 4 tests, **all of which must pass without
`fastmcp` installed** (you cannot rely on the optional extra in CI):

```python
"""MCP wrapper tests.

These tests exercise the build_server() factory by patching FastMCP so
the optional dependency is not required for the CI run.
"""

import sys
from types import ModuleType
from unittest.mock import MagicMock
import pytest

# ... (your test setup)

def test_build_server_registers_four_tools(monkeypatch):
    fake_fastmcp = ModuleType("fastmcp")
    server_instance = MagicMock()
    server_instance.tool = lambda *args, **kwargs: (lambda fn: fn)
    fake_fastmcp.FastMCP = MagicMock(return_value=server_instance)
    monkeypatch.setitem(sys.modules, "fastmcp", fake_fastmcp)

    from acg.mcp.server import build_server
    server = build_server()
    assert server is server_instance
    assert fake_fastmcp.FastMCP.call_count == 1
````

Cover:

1. `test_build_server_returns_fastmcp_instance` — patch FastMCP, ensure
   `build_server()` calls the constructor exactly once.
2. `test_analyze_repo_tool_calls_scan_context_graph` — patch
   `acg.mcp.server.scan_context_graph`, invoke the registered
   `analyze_repo` callable, assert the patch was hit with the path.
3. `test_validate_writes_tool_returns_allowed_true_for_in_path_write` —
   construct an in-memory lockfile (mirror style from
   `tests/test_compiler.py`), call the tool, assert
   `result["allowed"] is True`.
4. `test_cli_mcp_command_errors_when_extra_missing` — use Typer's
   `CliRunner`; monkeypatch `acg.mcp.run_stdio` import to `None` (or
   raise `ImportError`) and confirm exit code is `EXIT_USER_ERROR` and
   stderr says "MCP extra not installed".

Do **not** import `fastmcp` at test module level. All imports of the
MCP wrapper must happen inside the test bodies after the monkeypatch
that injects the fake module.

### 7. Documentation updates (small additive edits, no rewrites)

#### `README.md`

In the existing **CLI surface** code block, append a single line:

```text
acg mcp              [--transport stdio]    # MCP server (requires .[mcp] extra)
```

Below the code block, replace the sentence:

> "The same four primitives are designed to land as MCP tools (...) for Devin / Claude Code / Cursor consumption. The MCP wrapper is on the roadmap; ..."

with:

> "The same four primitives are exposed as MCP tools — see [`docs/MCP_SERVER.md`](docs/MCP_SERVER.md). Compatible with Devin Manage Devins, Claude Code, Cursor, and OpenCode."

#### `docs/COGNITION_INTEGRATION.md`

Replace the **MCP tool surface (roadmap)** heading with **MCP tool
surface (shipped)** and replace the sentence:

> "These mirror the four CLI commands one-to-one. A FastMCP wrapper
> that exposes them over stdio is staged for a follow-up release; ..."

with:

> "These mirror the four CLI commands one-to-one. The FastMCP stdio
> wrapper ships in [`acg/mcp/`](../acg/mcp/) — install with `pip install
-e '.[mcp]'` and run `acg mcp`. See [`docs/MCP_SERVER.md`](MCP_SERVER.md)
> for tool schemas and a Devin worked example."

In the **What we explicitly did not build** list, **delete** the bullet
that currently reads "MCP wrapper itself. Roadmap." (it's no longer
accurate).

#### `HANDOFF.md`

Under "What's shipped (merged into local `main`)", add a new bullet:

```markdown
- **PR 6 — MCP server wrapper**: `acg.mcp` package + `acg mcp` CLI +
  `docs/MCP_SERVER.md`. Exposes `analyze_repo`, `predict_writes`,
  `compile_lockfile`, `validate_writes` over FastMCP stdio.
```

### 8. `Makefile` — append a small helper

Append to the `.PHONY` declaration on line 1: `mcp-serve`. Add at the
bottom of the file:

```makefile
mcp-serve:
	./.venv/bin/acg mcp --transport stdio
```

## Branch / commit / PR conventions

- Branch from `main`: `git checkout -b mcp-server-wrapper`
- Commits:
  ```
  pyproject: add optional [mcp] dep group
  mcp: add fastmcp server wrapping analyze_repo/predict_writes/compile_lockfile/validate_writes
  cli: add acg mcp subcommand
  docs: add MCP_SERVER.md with tool surface + Devin worked example
  docs: mark MCP wrapper as shipped in README + COGNITION_INTEGRATION + HANDOFF
  tests: cover MCP wrapper without requiring the fastmcp extra (4 cases)
  ```
- PR title: `mcp: ship FastMCP wrapper exposing the four ACG primitives`
- PR description: paste the table from `docs/MCP_SERVER.md` and the
  output of `./.venv/bin/acg mcp --help`.

## Acceptance gates

```bash
# Without the extra installed (CI default):
./.venv/bin/python -m pytest tests/ -q          # all existing + 4 new tests pass
./.venv/bin/ruff check acg/ tests/ benchmark/
./.venv/bin/acg mcp --help                       # prints usage; should NOT crash
./.venv/bin/acg mcp --transport=stdio < /dev/null
# ↑ should fail fast with the "MCP extra not installed" error message
# (exit code != 0, stderr mentions "pip install -e '.[mcp]'").

# Optional: with the extra installed (you may skip if pip times out):
pip install -e '.[mcp]'
timeout 2 ./.venv/bin/acg mcp --transport=stdio < /dev/null || true
# ↑ should boot the server, then exit 0 (or get killed by timeout, which is fine).
```

The first three gates are mandatory. The fourth is a smoke test only;
if `pip install` of `fastmcp` fails in your sandbox, document that in
the PR description and rely on the unit-test patches.

## DO NOT

- Modify `acg/compiler.py`, `acg/predictor.py`, `acg/enforce.py`,
  `acg/runtime.py`, `acg/repo_graph.py`, or any of the existing Typer
  commands. The MCP wrapper is **glue**, not new behaviour.
- Add `fastmcp` to the top-level `dependencies` array. It MUST stay in
  `[project.optional-dependencies] mcp`.
- Wire MCP tools into `acg/runtime.py`'s worker fan-out. That's
  out-of-scope for this PR (and would conflict with the live GX10
  demo).
- Auto-launch the MCP server from `make demo` or any default target.
  Users opt in via `make mcp-serve` only.
- Modify `viz/`, `demo-app/`, `experiments/greenhouse/`, or anything in
  `docs/devin/` (those are other in-flight PRs' territory).

## When in doubt

- FastMCP docs: https://gofastmcp.com — read the **stdio** transport
  section first; that's the only transport this PR supports.
- `acg/enforce.py::validate_write` is the source-of-truth function for
  the `validate_writes_tool` body. Read its return shape before
  constructing the dict.
- `acg/repo_graph.py::scan_context_graph` is what `analyze_repo`
  wraps; it already handles language detection and graph
  normalization.
- `tests/test_cli.py` shows the Typer `CliRunner` style — mirror it for
  the new `acg mcp` command coverage.

Good luck. Once this lands, the Cognition track narrative ("ACG is the
pre-flight artifact Devin Manage Devins can consume") becomes
demonstrable, not aspirational.
