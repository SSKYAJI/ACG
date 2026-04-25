"""Typer-based command-line interface for ACG.

Every command is a thin wrapper around a function in another module so the
core compile / solve / enforce paths can be unit tested without spinning up
the CLI.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from .compiler import compile_lockfile
from .enforce import EXIT_ALLOWED, EXIT_BLOCKED, EXIT_USER_ERROR, cli_validate
from .explain import render
from .llm import LLMClient
from .schema import AgentLock, TasksInput

app = typer.Typer(
    add_completion=False,
    help="Agent Context Graph — pre-flight write contract compiler.",
    no_args_is_help=True,
)

_console = Console()
_err_console = Console(stderr=True)


def _load_repo_graph(repo_path: Path) -> dict:
    """Load ``<repo>/.acg/context_graph.json`` if present, else return ``{}``."""
    graph_path = repo_path / ".acg" / "context_graph.json"
    if not graph_path.exists():
        return {}
    try:
        return json.loads(graph_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        _err_console.print(f"[yellow]warning[/]: could not read {graph_path}: {exc}")
        return {}


def _load_tasks(tasks_path: Path) -> TasksInput:
    return TasksInput.model_validate_json(tasks_path.read_text())


@app.command("compile")
def cmd_compile(
    repo: Annotated[Path, typer.Option(exists=True, file_okay=False, help="Repository root.")],
    tasks: Annotated[
        Path, typer.Option(exists=True, dir_okay=False, help="Path to tasks.json.")
    ],
    out: Annotated[Path, typer.Option(help="Where to write agent_lock.json.")],
) -> None:
    """Compile ``tasks.json`` + repo graph into ``agent_lock.json``."""
    tasks_input = _load_tasks(tasks)
    repo_graph = _load_repo_graph(repo)
    if not repo_graph:
        _console.print(
            "[dim]no .acg/context_graph.json found; running with empty graph "
            "(seeds + LLM only).[/]"
        )
    llm = LLMClient.from_env()
    lock = compile_lockfile(repo, tasks_input, repo_graph, llm)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(lock.model_dump_json(indent=2) + "\n")
    _console.print(
        f"[green]wrote[/] {out} ({len(lock.tasks)} tasks, "
        f"{len(lock.execution_plan.groups)} groups, "
        f"{len(lock.conflicts_detected)} conflicts)"
    )


@app.command("explain")
def cmd_explain(
    lock: Annotated[Path, typer.Option(exists=True, dir_okay=False, help="Path to agent_lock.json.")],
) -> None:
    """Print a human-readable summary of an existing lockfile."""
    lockfile = AgentLock.model_validate_json(lock.read_text())
    _console.print(render(lockfile))


@app.command("validate-write")
def cmd_validate_write(
    lock: Annotated[Path, typer.Option(exists=True, dir_okay=False, help="Path to agent_lock.json.")],
    task: Annotated[str, typer.Option(help="Task id attempting the write.")],
    path: Annotated[str, typer.Option(help="Repository-relative write path.")],
    quiet: Annotated[bool, typer.Option(help="Suppress success message.")] = False,
) -> None:
    """Validate a candidate write against the lockfile (exit 2 = blocked)."""
    code, message = cli_validate(lock, task, path)
    if code == EXIT_ALLOWED:
        if not quiet:
            _console.print(f"[green]{message}[/]")
    elif code == EXIT_BLOCKED:
        _err_console.print(f"[red]{message}[/]")
    else:
        _err_console.print(f"[yellow]{message}[/]")
    raise typer.Exit(code=code)


@app.command("run")
def cmd_run(
    lock: Annotated[Path, typer.Option(exists=True, dir_okay=False, help="Path to agent_lock.json.")],
    repo: Annotated[Path, typer.Option(exists=True, file_okay=False, help="Repository root (used to locate .acg/context_graph.json).")],
    out: Annotated[Path, typer.Option(help="Where to write run_trace.json.")],
    mock: Annotated[bool, typer.Option("--mock", help="Use the deterministic offline runtime LLM instead of live servers.")] = False,
) -> None:
    """Execute the lockfile under runtime enforcement; emit a run trace JSON."""
    import asyncio
    from dataclasses import asdict

    from .runtime import MockRuntimeLLM, RuntimeConfig, RuntimeLLM, run_lockfile

    lockfile = AgentLock.model_validate_json(lock.read_text())
    repo_graph = _load_repo_graph(repo)
    cfg = RuntimeConfig.from_env()
    use_mock = mock or os.environ.get("ACG_MOCK_LLM") == "1"

    orch_llm = (
        MockRuntimeLLM(role="orchestrator")
        if use_mock
        else RuntimeLLM(cfg.orch_url, cfg.orch_model, cfg.orch_api_key, timeout=cfg.request_timeout_s)
    )
    sub_llm = (
        MockRuntimeLLM(role="worker")
        if use_mock
        else RuntimeLLM(cfg.sub_url, cfg.sub_model, cfg.sub_api_key, timeout=cfg.request_timeout_s)
    )

    async def _run() -> object:
        try:
            return await run_lockfile(
                lockfile,
                repo_graph,
                orch_llm,
                sub_llm,
                lockfile_path=str(lock),
                config=cfg,
            )
        finally:
            await orch_llm.aclose()
            await sub_llm.aclose()

    result = asyncio.run(_run())
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(asdict(result), indent=2, default=str) + "\n")
    _console.print(f"[green]wrote[/] {out}")


@app.command("report")
def cmd_report(
    naive: Annotated[Path, typer.Option(exists=True, dir_okay=False, help="Naive run metrics JSON.")],
    planned: Annotated[Path, typer.Option(exists=True, dir_okay=False, help="ACG-planned run metrics JSON.")],
    out: Annotated[Path, typer.Option(help="Output PNG path.")],
) -> None:
    """Render the benchmark chart PNG."""
    from .report import build_chart

    build_chart(naive, planned, out)
    _console.print(f"[green]wrote[/] {out}")


@app.command("run-benchmark")
def cmd_run_benchmark(
    mode: Annotated[str, typer.Option(help="'naive' or 'planned'.")],
    repo: Annotated[Path, typer.Option(exists=True, file_okay=False, help="Repository root.")],
    tasks: Annotated[Path, typer.Option(exists=True, dir_okay=False, help="Path to tasks.json.")],
    out: Annotated[Path, typer.Option(help="Where to write metrics JSON.")],
    lock: Annotated[
        Path | None,
        typer.Option(
            help="Lockfile to drive 'planned' mode. Defaults to <repo>/agent_lock.json.",
        ),
    ] = None,
) -> None:
    """Simulate one of the two execution modes and emit a metrics file."""
    from benchmark.runner import run_naive, run_planned  # local import to keep CLI cheap

    if mode == "naive":
        result = run_naive(repo, _load_tasks(tasks))
    elif mode == "planned":
        lock_path = lock or (repo / "agent_lock.json")
        if not Path(lock_path).exists():
            _err_console.print(
                f"[red]planned mode requires a lockfile; not found at {lock_path}[/]"
            )
            raise typer.Exit(code=EXIT_USER_ERROR)
        result = run_planned(repo, _load_tasks(tasks), Path(lock_path))
    else:
        _err_console.print(f"[red]unknown mode {mode!r} (expected 'naive' or 'planned')[/]")
        raise typer.Exit(code=EXIT_USER_ERROR)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, default=str) + "\n")
    _console.print(f"[green]wrote[/] {out}")


def main() -> None:  # pragma: no cover - convenience entry-point.
    try:
        app()
    except typer.Exit:
        raise
    except Exception as exc:  # noqa: BLE001 - top-level CLI guard
        _err_console.print(f"[red]error:[/] {exc}")
        sys.exit(EXIT_USER_ERROR)


if __name__ == "__main__":  # pragma: no cover
    main()
