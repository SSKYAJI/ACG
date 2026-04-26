"""FastMCP server for ACG.

Wraps the four stable primitives from :mod:`acg.compiler`,
:mod:`acg.predictor`, :mod:`acg.enforce`, and :mod:`acg.repo_graph`
behind a single MCP stdio server. The tools are registered under the
exact names documented in ``docs/COGNITION_INTEGRATION.md``:
``analyze_repo``, ``predict_writes``, ``compile_lockfile``, and
``validate_writes``.

All tools are intended to be idempotent and side-effect-free, with one
exception: ``analyze_repo`` writes the normalized context graph to
``<repo>/.acg/context_graph.json`` (the same on-disk artifact the
``acg init-graph`` CLI emits).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from acg.compiler import compile_lockfile as _compile_lockfile
from acg.enforce import validate_write as _validate_write
from acg.llm import LLMClient
from acg.predictor import predict_writes as _predict_writes
from acg.repo_graph import load_context_graph, scan_context_graph
from acg.schema import AgentLock, TaskInput, TaskInputHints, TasksInput

if TYPE_CHECKING:  # pragma: no cover - type-only import
    from fastmcp import FastMCP


def build_server() -> FastMCP:
    """Construct a FastMCP server registered with all four ACG tools.

    FastMCP is imported lazily so that the optional ``mcp`` extra is
    truly opt-in: importing :mod:`acg.mcp.server` does not require
    ``fastmcp`` to be installed, only calling :func:`build_server`
    does.
    """
    from fastmcp import FastMCP

    server = FastMCP(name="acg", version="0.1.0")

    @server.tool(name="analyze_repo")
    def analyze_repo(path: str, language: str = "auto") -> dict[str, Any]:
        """Build / refresh the deterministic context graph for ``path``.

        Wraps :func:`acg.repo_graph.scan_context_graph`. Returns the
        normalized graph dict (``files``, ``symbols_index``, ``imports``,
        ``exports``, ``hotspots``, ``routes``, ``configs``, ``tests``,
        ``languages``).

        Side effect: writes ``<path>/.acg/context_graph.json``.
        """
        repo = Path(path).resolve()
        return scan_context_graph(repo, language)

    @server.tool(name="predict_writes")
    def predict_writes(
        task: dict[str, Any],
        repo_path: str,
        repo_graph: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Predict the file write-set for a single task.

        ``task`` must contain ``id`` and ``prompt``; ``hints.touches`` is
        optional. When ``repo_graph`` is omitted the cached graph at
        ``<repo>/.acg/context_graph.json`` is used; if missing, a fresh
        scan is performed.

        Returns a list of ``{path, confidence, reason}`` dicts.
        """
        repo = Path(repo_path).resolve()
        graph = repo_graph or load_context_graph(repo) or scan_context_graph(repo, "auto")
        hints_payload = task.get("hints") or {}
        task_input = TaskInput(
            id=task["id"],
            prompt=task["prompt"],
            hints=TaskInputHints(**hints_payload) if hints_payload else None,
        )
        llm = LLMClient.from_env()
        writes = _predict_writes(task_input, graph, llm, repo_root=repo)
        return [w.model_dump() for w in writes]

    @server.tool(name="compile_lockfile")
    def compile_lockfile(
        repo_path: str,
        tasks: dict[str, Any],
        language: str = "auto",
    ) -> dict[str, Any]:
        """Compile a tasks document into a full ``agent_lock.json`` dict.

        ``tasks`` must conform to :class:`acg.schema.TasksInput`
        (i.e. ``{"version": "1.0", "tasks": [...]}``).
        """
        repo = Path(repo_path).resolve()
        tasks_input = TasksInput.model_validate(tasks)
        graph = scan_context_graph(repo, language)
        llm = LLMClient.from_env()
        lock = _compile_lockfile(repo, tasks_input, graph, llm)
        return lock.model_dump(mode="json")

    @server.tool(name="validate_writes")
    def validate_writes(
        lockfile: dict[str, Any],
        task_id: str,
        attempted_path: str,
    ) -> dict[str, Any]:
        """Validate a single attempted write against an in-memory lockfile.

        Returns ``{"allowed": bool, "reason": str}``. Mirrors the CLI's
        ``acg validate-write`` exit-code contract for in-process use.
        """
        lock = AgentLock.model_validate(lockfile)
        allowed, reason = _validate_write(lock, task_id, attempted_path)
        return {"allowed": allowed, "reason": reason or ""}

    return server


def run_stdio() -> None:
    """Boot the MCP server on stdio — the standard transport for tool hosts."""
    server = build_server()
    server.run()
