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
from .diff import DiffValidationError, validate_git_diff
from .enforce import EXIT_ALLOWED, EXIT_BLOCKED, EXIT_USER_ERROR, cli_validate
from .explain import render
from .llm import LLMClient
from .orchestrator import MAX_TASKS_DEFAULT, TaskPlanningError, plan_tasks_from_goal
from .repo_graph import (
    GraphScanError,
    context_graph_path,
    load_context_graph,
    scan_context_graph,
)
from .schema import AgentLock, TasksInput

LOCALIZATION_BACKENDS = ("native", "scip", "auto")

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


def _cached_graph_matches_backend(repo_graph: dict, localization_backend: str) -> bool:
    backend = localization_backend.strip().lower()
    graph_backend = repo_graph.get("localization_backend") or "native"
    if backend == "native":
        return graph_backend == "native"
    if graph_backend != backend:
        return False
    status = repo_graph.get("scip_status")
    if isinstance(status, dict):
        return status.get("status") == "ok"
    return False


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
                "'python' runs the in-process AST scanner; "
                "'java' runs the in-process tree-sitter scanner before compiling."
            ),
        ),
    ] = "typescript",
    use_cached_graph: Annotated[
        bool,
        typer.Option(
            "--use-cached-graph/--rescan-graph",
            help=(
                "Reuse <repo>/.acg/context_graph.json when present (default). "
                "Pass --rescan-graph to force a fresh ts-morph / tree-sitter scan."
            ),
        ),
    ] = True,
    localization_backend: Annotated[
        str,
        typer.Option(
            "--localization-backend",
            help="Localization backend for graph scans: native, scip, or auto.",
        ),
    ] = "native",
) -> None:
    """Compile ``tasks.json`` + repo graph into ``agent_lock.json``."""
    language_normalized = language.strip().lower()
    if language_normalized not in (
        "auto",
        "typescript",
        "javascript",
        "ts",
        "js",
        "java",
        "python",
        "py",
    ):
        _err_console.print(
            f"[red]unsupported --language {language!r}; "
            "expected one of: auto, typescript, javascript, python, java[/]"
        )
        raise typer.Exit(code=EXIT_USER_ERROR)
    localization_backend_normalized = localization_backend.strip().lower()
    if localization_backend_normalized not in LOCALIZATION_BACKENDS:
        _err_console.print(
            f"[red]unsupported --localization-backend {localization_backend!r}; "
            "expected one of: native, scip, auto[/]"
        )
        raise typer.Exit(code=EXIT_USER_ERROR)

    tasks_input = _load_tasks(tasks)
    graph_file = context_graph_path(repo)
    if use_cached_graph and graph_file.exists():
        repo_graph = _load_repo_graph(repo)
        if _cached_graph_matches_backend(repo_graph, localization_backend_normalized):
            _console.print(f"[dim]reusing cached context graph at {graph_file}[/]")
        else:
            use_cached_graph = False
    else:
        repo_graph = {}
        use_cached_graph = False
    if not use_cached_graph:
        try:
            scan_context_graph(
                repo,
                language_normalized,
                localization_backend=localization_backend_normalized,
            )
        except (GraphScanError, ValueError) as exc:
            _err_console.print(f"[red]graph scan failed:[/] {exc}")
            raise typer.Exit(code=EXIT_USER_ERROR) from exc
        repo_graph = _load_repo_graph(repo)
        _console.print(
            f"[dim]scanned {repo_graph.get('language', 'unknown')} repo → {graph_file}[/]"
        )
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


@app.command("plan-tasks")
def cmd_plan_tasks(
    repo: Annotated[Path, typer.Option(exists=True, file_okay=False, help="Repository root.")],
    goal: Annotated[
        str,
        typer.Option(
            "--goal",
            help="High-level coding goal for the orchestrator to decompose.",
        ),
    ],
    out: Annotated[Path, typer.Option(help="Where to write tasks.json.")],
    language: Annotated[
        str,
        typer.Option(help="Source language to scan: auto, typescript, javascript, or java."),
    ] = "auto",
    max_tasks: Annotated[
        int,
        typer.Option("--max-tasks", min=1, help="Maximum number of sub-agent tasks."),
    ] = MAX_TASKS_DEFAULT,
    use_cached_graph: Annotated[
        bool,
        typer.Option(
            "--use-cached-graph/--rescan-graph",
            help="Reuse <repo>/.acg/context_graph.json when present.",
        ),
    ] = True,
    localization_backend: Annotated[
        str,
        typer.Option(
            "--localization-backend",
            help="Localization backend for graph scans: native, scip, or auto.",
        ),
    ] = "native",
) -> None:
    """Use an orchestrator LLM to decompose a goal into ``tasks.json``."""
    localization_backend_normalized = localization_backend.strip().lower()
    if localization_backend_normalized not in LOCALIZATION_BACKENDS:
        _err_console.print(
            f"[red]unsupported --localization-backend {localization_backend!r}; "
            "expected one of: native, scip, auto[/]"
        )
        raise typer.Exit(code=EXIT_USER_ERROR)
    graph_file = context_graph_path(repo)
    if use_cached_graph and graph_file.exists():
        repo_graph = _load_repo_graph(repo)
        if _cached_graph_matches_backend(repo_graph, localization_backend_normalized):
            _console.print(f"[dim]reusing cached context graph at {graph_file}[/]")
        else:
            use_cached_graph = False
    else:
        repo_graph = {}
        use_cached_graph = False
    if not use_cached_graph:
        try:
            scan_context_graph(
                repo,
                language,
                localization_backend=localization_backend_normalized,
            )
        except (GraphScanError, ValueError) as exc:
            _err_console.print(f"[red]graph scan failed:[/] {exc}")
            raise typer.Exit(code=EXIT_USER_ERROR) from exc
        repo_graph = _load_repo_graph(repo)
        _console.print(
            f"[dim]scanned {repo_graph.get('language', 'unknown')} repo → {graph_file}[/]"
        )
    if not repo_graph:
        _err_console.print("[red]graph scan did not produce a readable context graph[/]")
        raise typer.Exit(code=EXIT_USER_ERROR)

    llm = LLMClient.from_env()
    try:
        tasks_input = plan_tasks_from_goal(goal, repo_graph, llm, max_tasks=max_tasks)
    except TaskPlanningError as exc:
        _err_console.print(f"[red]task planning failed:[/] {exc}")
        raise typer.Exit(code=EXIT_USER_ERROR) from exc

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(tasks_input.model_dump_json(indent=2) + "\n")
    _console.print(f"[green]wrote[/] {out} ({len(tasks_input.tasks)} tasks)")


@app.command("init-graph")
def cmd_init_graph(
    repo: Annotated[Path, typer.Option(exists=True, file_okay=False, help="Repository root.")],
    language: Annotated[
        str,
        typer.Option(
            "--language",
            help="Source language to scan: auto, typescript, javascript, python, or java.",
        ),
    ] = "auto",
    out: Annotated[
        Path | None,
        typer.Option(
            help=("Where to write context_graph.json. Defaults to <repo>/.acg/context_graph.json."),
        ),
    ] = None,
    localization_backend: Annotated[
        str,
        typer.Option(
            "--localization-backend",
            help="Localization backend for graph scans: native, scip, or auto.",
        ),
    ] = "native",
) -> None:
    """Initialize a deterministic ``context_graph.json`` for a repository."""
    localization_backend_normalized = localization_backend.strip().lower()
    if localization_backend_normalized not in LOCALIZATION_BACKENDS:
        _err_console.print(
            f"[red]unsupported --localization-backend {localization_backend!r}; "
            "expected one of: native, scip, auto[/]"
        )
        raise typer.Exit(code=EXIT_USER_ERROR)
    try:
        graph = scan_context_graph(
            repo,
            language,
            out,
            localization_backend=localization_backend_normalized,
        )
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


@app.command("validate-diff")
def cmd_validate_diff(
    lock: Annotated[
        Path, typer.Option(exists=True, dir_okay=False, help="Path to agent_lock.json.")
    ],
    repo: Annotated[Path, typer.Option(exists=True, file_okay=False, help="Repository root.")],
    task: Annotated[str, typer.Option(help="Task id whose applied diff should be checked.")],
    base_ref: Annotated[
        str,
        typer.Option(
            "--base-ref",
            help="Base git ref. Without --head-ref, validate current worktree diff against it.",
        ),
    ] = "HEAD",
    head_ref: Annotated[
        str | None,
        typer.Option("--head-ref", help="Optional head git ref or branch to compare."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON instead of text."),
    ] = False,
) -> None:
    """Validate actual files changed by a git diff against a task contract."""
    try:
        lockfile = AgentLock.model_validate_json(lock.read_text())
        result = validate_git_diff(
            lockfile,
            repo_path=repo,
            task_id=task,
            base_ref=base_ref,
            head_ref=head_ref,
        )
    except (OSError, ValueError, DiffValidationError, KeyError) as exc:
        _err_console.print(f"[red]diff validation failed:[/] {exc}")
        raise typer.Exit(code=EXIT_USER_ERROR) from exc

    payload = {
        "task_id": result.task_id,
        "base_ref": result.base_ref,
        "head_ref": result.head_ref,
        "ok": result.ok,
        "allowed_count": result.allowed_count,
        "blocked_count": result.blocked_count,
        "changed_files": result.changed_files,
        "verdicts": [
            {
                "path": verdict.path,
                "allowed": verdict.allowed,
                "reason": verdict.reason,
            }
            for verdict in result.verdicts
        ],
    }
    if json_output:
        sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    else:
        status = "OK" if result.ok else "BLOCKED"
        color = "green" if result.ok else "red"
        _console.print(
            f"[{color}]{status}[/]: {result.allowed_count} allowed, "
            f"{result.blocked_count} blocked across {len(result.changed_files)} changed files"
        )
        for verdict in result.verdicts:
            if verdict.allowed:
                _console.print(f"  [green]ALLOWED[/] {verdict.path}")
            else:
                _console.print(f"  [red]BLOCKED[/] {verdict.path}: {verdict.reason}")
    raise typer.Exit(code=EXIT_ALLOWED if result.ok else EXIT_BLOCKED)


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
    perf_trace: Annotated[
        Path | None,
        typer.Option("--perf-trace", help="Optional path to write perf_trace.json."),
    ] = None,
    sequential: Annotated[
        bool | None,
        typer.Option(
            "--sequential/--no-sequential",
            help=(
                "Baseline lane: execute workers strictly serially. "
                "Mutually exclusive with --worker-concurrency > 1. "
                "If omitted, ACG_SEQUENTIAL is honored."
            ),
        ),
    ] = None,
    worker_concurrency: Annotated[
        int | None,
        typer.Option(
            "--worker-concurrency",
            help=(
                "Optimized lane: cap on concurrent in-flight workers per group. "
                "0 means unbounded. If omitted, ACG_WORKER_CONCURRENCY is honored."
            ),
        ),
    ] = None,
    grace_overlap: Annotated[
        bool,
        typer.Option(
            "--grace-overlap/--no-grace-overlap",
            help="Overlap Grace CPU validation/rescans with GPU inference.",
        ),
    ] = False,
) -> None:
    """Execute the lockfile under runtime enforcement; emit a run trace JSON."""
    import asyncio
    from dataclasses import asdict

    from .perf import PerfRecorder
    from .runtime import MockRuntimeLLM, RuntimeConfig, RuntimeLLM, run_lockfile

    lockfile = AgentLock.model_validate_json(lock.read_text())
    repo_graph = _load_repo_graph(repo)
    cfg = RuntimeConfig.from_env()
    if sequential is not None:
        cfg.sequential = sequential
    if worker_concurrency is not None:
        cfg.worker_concurrency = worker_concurrency
    cfg.grace_overlap = grace_overlap
    if perf_trace is not None:
        cfg.perf_trace_path = perf_trace

    if cfg.sequential and cfg.worker_concurrency > 1:
        raise typer.BadParameter(
            "sequential mode cannot be combined with worker_concurrency > 1; "
            "pick a baseline (--sequential) or optimized lane (--worker-concurrency N).",
            param_hint="--sequential / --worker-concurrency",
        )
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
    perf = (
        PerfRecorder(config=cfg.perf_public(), lockfile=str(lock)) if cfg.perf_trace_path else None
    )

    async def _run() -> object:
        try:
            return await run_lockfile(
                lockfile,
                repo_graph,
                orch_llm,
                sub_llm,
                lockfile_path=str(lock),
                repo_root=repo,
                config=cfg,
                perf=perf,
            )
        finally:
            await orch_llm.aclose()
            await sub_llm.aclose()

    result = asyncio.run(_run())
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(asdict(result), indent=2, default=str) + "\n")
    _console.print(f"[green]wrote[/] {out}")
    if cfg.perf_trace_path:
        _console.print(f"[green]wrote[/] {cfg.perf_trace_path}")


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
            help=("Where to write the Markdown report. Defaults to stdout."),
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
                    "execution_mode": r.execution_mode,
                    "evidence_kind": r.evidence_kind,
                    "tasks_total": r.tasks_total,
                    "tasks_completed": r.tasks_completed,
                    "tests_ran_count": r.tests_ran_count,
                    "tested_tasks_completed": r.tested_tasks_completed,
                    "overlapping_write_pairs": r.overlapping_write_pairs,
                    "out_of_bounds_write_count": r.out_of_bounds_write_count,
                    "blocked_invalid_write_count": r.blocked_invalid_write_count,
                    "tokens_prompt_total": r.tokens_prompt_total,
                    "tokens_all_in": r.tokens_all_in,
                    "tokens_prompt_method": r.tokens_prompt_method,
                    "tokens_orchestrator_overhead": r.tokens_orchestrator_overhead,
                    "cost_usd_total": r.cost_usd_total,
                    "cost_method": r.cost_method,
                    "cost_source": r.cost_source,
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
                "total_proposal_oob": report.total_proposal_oob,
                "total_posthoc_oob": report.total_posthoc_oob,
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
