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
from .repo_graph import (
    GraphScanError,
    context_graph_path,
    load_context_graph,
    scan_context_graph,
)
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
    graph = load_context_graph(repo_path)
    if not graph and context_graph_path(repo_path).exists():
        _err_console.print(f"[yellow]warning[/]: could not read {context_graph_path(repo_path)}")
    return graph


def _load_tasks(tasks_path: Path) -> TasksInput:
    return TasksInput.model_validate_json(tasks_path.read_text())


@app.command("compile")
def cmd_compile(
    repo: Annotated[Path, typer.Option(exists=True, file_okay=False, help="Repository root.")],
    tasks: Annotated[Path, typer.Option(exists=True, dir_okay=False, help="Path to tasks.json.")],
    out: Annotated[Path, typer.Option(help="Where to write agent_lock.json.")],
    language: Annotated[
        str,
        typer.Option(
            "--language",
            help=(
                "Source language of the target repo. "
                "'typescript' (default) runs graph_builder/scan.ts; "
                "'java' runs the in-process tree-sitter scanner before compiling."
            ),
        ),
    ] = "typescript",
) -> None:
    """Compile ``tasks.json`` + repo graph into ``agent_lock.json``."""
    language_normalized = language.strip().lower()
    if language_normalized not in ("auto", "typescript", "javascript", "ts", "js", "java"):
        _err_console.print(
            f"[red]unsupported --language {language!r}; "
            "expected one of: auto, typescript, javascript, java[/]"
        )
        raise typer.Exit(code=EXIT_USER_ERROR)

    tasks_input = _load_tasks(tasks)
    try:
        repo_graph = scan_context_graph(repo, language_normalized)
    except (GraphScanError, ValueError) as exc:
        _err_console.print(f"[red]graph scan failed:[/] {exc}")
        raise typer.Exit(code=EXIT_USER_ERROR) from exc
    _console.print(
        f"[dim]scanned {repo_graph.get('language', 'unknown')} repo → {context_graph_path(repo)}[/]"
    )
    repo_graph = _load_repo_graph(repo)
    if not repo_graph:
        _err_console.print("[red]graph scan did not produce a readable context graph[/]")
        raise typer.Exit(code=EXIT_USER_ERROR)
    llm = LLMClient.from_env()
    lock = compile_lockfile(repo, tasks_input, repo_graph, llm)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(lock.model_dump_json(indent=2) + "\n")
    _console.print(
        f"[green]wrote[/] {out} ({len(lock.tasks)} tasks, "
        f"{len(lock.execution_plan.groups)} groups, "
        f"{len(lock.conflicts_detected)} conflicts)"
    )


@app.command("init-graph")
def cmd_init_graph(
    repo: Annotated[Path, typer.Option(exists=True, file_okay=False, help="Repository root.")],
    language: Annotated[
        str,
        typer.Option(
            "--language",
            help="Source language to scan: auto, typescript, javascript, or java.",
        ),
    ] = "auto",
    out: Annotated[
        Path | None,
        typer.Option(
            help=("Where to write context_graph.json. Defaults to <repo>/.acg/context_graph.json."),
        ),
    ] = None,
) -> None:
    """Initialize a deterministic ``context_graph.json`` for a repository."""
    try:
        graph = scan_context_graph(repo, language, out)
    except (GraphScanError, ValueError) as exc:
        _err_console.print(f"[red]graph scan failed:[/] {exc}")
        raise typer.Exit(code=EXIT_USER_ERROR) from exc
    out_path = out or context_graph_path(repo)
    _console.print(
        f"[green]wrote[/] {out_path} ({len(graph.get('files') or [])} files, "
        f"{len(graph.get('hotspots') or [])} hotspots, "
        f"language={graph.get('language', 'unknown')})"
    )


@app.command("explain")
def cmd_explain(
    lock: Annotated[
        Path, typer.Option(exists=True, dir_okay=False, help="Path to agent_lock.json.")
    ],
) -> None:
    """Print a human-readable summary of an existing lockfile."""
    lockfile = AgentLock.model_validate_json(lock.read_text())
    _console.print(render(lockfile))


@app.command("validate-write")
def cmd_validate_write(
    lock: Annotated[
        Path, typer.Option(exists=True, dir_okay=False, help="Path to agent_lock.json.")
    ],
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


@app.command("validate-lockfile")
def cmd_validate_lockfile(
    lock: Annotated[
        Path, typer.Option(exists=True, dir_okay=False, help="Path to agent_lock.json.")
    ],
    schema: Annotated[
        Path,
        typer.Option(
            exists=True,
            dir_okay=False,
            help="Path to agent_lock.schema.json.",
        ),
    ] = Path("schema/agent_lock.schema.json"),
) -> None:
    """Validate a lockfile against the JSON Schema (exit 2 = invalid)."""
    import jsonschema

    try:
        payload = json.loads(lock.read_text())
        schema_dict = json.loads(schema.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        _err_console.print(f"[red]error reading inputs: {exc}[/]")
        raise typer.Exit(code=EXIT_USER_ERROR) from exc

    try:
        jsonschema.validate(payload, schema_dict)
    except jsonschema.ValidationError as exc:
        _err_console.print(f"[red]INVALID:[/] {exc.message}")
        raise typer.Exit(code=EXIT_BLOCKED) from exc

    _console.print("[green]OK[/]")


@app.command("run")
def cmd_run(
    lock: Annotated[
        Path, typer.Option(exists=True, dir_okay=False, help="Path to agent_lock.json.")
    ],
    repo: Annotated[
        Path,
        typer.Option(
            exists=True,
            file_okay=False,
            help="Repository root (used to locate .acg/context_graph.json).",
        ),
    ],
    out: Annotated[Path, typer.Option(help="Where to write run_trace.json.")],
    mock: Annotated[
        bool,
        typer.Option(
            "--mock", help="Use the deterministic offline runtime LLM instead of live servers."
        ),
    ] = False,
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
        else RuntimeLLM(
            cfg.orch_url, cfg.orch_model, cfg.orch_api_key, timeout=cfg.request_timeout_s
        )
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
    naive: Annotated[
        Path, typer.Option(exists=True, dir_okay=False, help="Naive run metrics JSON.")
    ],
    planned: Annotated[
        Path, typer.Option(exists=True, dir_okay=False, help="ACG-planned run metrics JSON.")
    ],
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


@app.command("analyze-runs")
def cmd_analyze_runs(
    inputs: Annotated[
        list[Path],
        typer.Argument(
            exists=True,
            help=(
                "One or more eval_run.json files (or directories containing them). "
                "Combined files with strategies.{naive_parallel,acg_planned} are "
                "flattened automatically."
            ),
        ),
    ],
    out: Annotated[
        Path | None,
        typer.Option(
            help=(
                "Where to write the Markdown report. Defaults to stdout."
            ),
        ),
    ] = None,
    json_out: Annotated[
        Path | None,
        typer.Option(
            "--json-out",
            help=(
                "Optional path to also write the structured analysis as JSON, "
                "for downstream tooling."
            ),
        ),
    ] = None,
) -> None:
    """Aggregate eval_run artifacts into a predictor-accuracy + scope report.

    Implements the megaplan's "learn from mistakes" loop: compares each
    lockfile's ``predicted_writes`` against the agent's
    ``actual_changed_files`` and surfaces refinement suggestions.
    """
    from .analyze import analyze_paths, collect_suggestions, format_markdown

    report = analyze_paths(inputs)
    md = format_markdown(report)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md)
        _console.print(f"[green]wrote[/] {out}")
    else:
        sys.stdout.write(md)

    if json_out:
        suggestions = collect_suggestions(report)
        payload = {
            "runs": [
                {
                    "source_path": r.source_path,
                    "strategy": r.strategy,
                    "backend": r.backend,
                    "suite_name": r.suite_name,
                    "tasks_total": r.tasks_total,
                    "tasks_completed": r.tasks_completed,
                    "overlapping_write_pairs": r.overlapping_write_pairs,
                    "out_of_bounds_write_count": r.out_of_bounds_write_count,
                    "blocked_invalid_write_count": r.blocked_invalid_write_count,
                    "tokens_prompt_total": r.tokens_prompt_total,
                    "tokens_orchestrator_overhead": r.tokens_orchestrator_overhead,
                }
                for r in report.runs
            ],
            "tasks": {
                tid: {
                    "task_id": t.task_id,
                    "runs_seen": t.runs_seen,
                    "predicted_files": sorted(t.predicted_files),
                    "actual_files_seen": sorted(t.actual_files_seen),
                    "out_of_bounds_files": t.out_of_bounds_files,
                    "blocked_events_total": t.blocked_events_total,
                    "allowed_glob_count": t.allowed_glob_count,
                    "true_positives": t.true_positives,
                    "false_positives": t.false_positives,
                    "false_negatives": t.false_negatives,
                    "precision": round(t.precision, 4),
                    "recall": round(t.recall, 4),
                    "f1": round(t.f1, 4),
                    "suggestions": suggestions.get(tid, []),
                }
                for tid, t in report.tasks.items()
            },
            "overall": {
                "precision": round(report.overall_precision, 4),
                "recall": round(report.overall_recall, 4),
                "f1": round(report.overall_f1, 4),
                "total_blocks": report.total_blocks,
                "total_oob": report.total_oob,
            },
        }
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        _console.print(f"[green]wrote[/] {json_out}")


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
        import fastmcp  # noqa: F401

        from acg.mcp import run_stdio
    except ImportError as exc:
        _err_console.print(
            r"[red]MCP extra not installed.[/] Run: "
            r"[bold]pip install -e '.\[mcp]'[/]"
        )
        raise typer.Exit(code=EXIT_USER_ERROR) from exc
    run_stdio()


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
