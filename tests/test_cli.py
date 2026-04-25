"""Tests for the Typer CLI entry-points.

Covers ``acg validate-lockfile`` end-to-end against the bundled example
lockfiles and the JSON Schema in ``schema/agent_lock.schema.json``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import acg.cli as cli
from acg.cli import app
from acg.schema import AgentLock, ExecutionPlan, Generator, Repo

runner = CliRunner()


def test_validate_lockfile_ok(example_dag_lockfile_path: Path, schema_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "validate-lockfile",
            "--lock",
            str(example_dag_lockfile_path),
            "--schema",
            str(schema_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "OK" in result.output


def test_validate_lockfile_accepts_generated_demo_lockfile(schema_path: Path) -> None:
    demo_lockfile = schema_path.parent.parent / "demo-app" / "agent_lock.json"
    result = runner.invoke(
        app,
        [
            "validate-lockfile",
            "--lock",
            str(demo_lockfile),
            "--schema",
            str(schema_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "OK" in result.output


def test_validate_lockfile_rejects_invalid(
    tmp_path: Path, example_dag_lockfile_path: Path, schema_path: Path
) -> None:
    payload = json.loads(example_dag_lockfile_path.read_text())
    payload["version"] = "9.9"  # break the const "1.0"
    bad = tmp_path / "bad_lock.json"
    bad.write_text(json.dumps(payload))

    result = runner.invoke(
        app,
        [
            "validate-lockfile",
            "--lock",
            str(bad),
            "--schema",
            str(schema_path),
        ],
    )
    assert result.exit_code == 2, result.output


def test_init_graph_command_writes_normalized_graph(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    out = tmp_path / "graph.json"

    def fake_scan_context_graph(
        repo_path: Path, language: str, out_path: Path | None
    ) -> dict[str, Any]:
        assert repo_path == repo
        assert language == "java"
        assert out_path == out
        out.write_text(json.dumps({"language": "java", "files": [], "hotspots": []}))
        return {"language": "java", "files": [], "hotspots": []}

    monkeypatch.setattr(cli, "scan_context_graph", fake_scan_context_graph)

    result = runner.invoke(
        app,
        ["init-graph", "--repo", str(repo), "--language", "java", "--out", str(out)],
    )

    assert result.exit_code == 0, result.output
    assert "language=java" in result.output
    assert out.exists()


def test_compile_initializes_repo_graph_before_compiling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    tasks = tmp_path / "tasks.json"
    tasks.write_text(
        json.dumps(
            {
                "version": "1.0",
                "tasks": [{"id": "readme", "prompt": "Update README.md."}],
            }
        )
    )
    out = tmp_path / "agent_lock.json"
    calls: list[tuple[str, Path | None]] = []

    def fake_scan_context_graph(
        repo_path: Path, language: str, out_path: Path | None = None
    ) -> dict[str, Any]:
        calls.append((language, out_path))
        graph_path = repo_path / ".acg" / "context_graph.json"
        graph_path.parent.mkdir(parents=True)
        graph = {
            "version": "1.0",
            "root": str(repo_path),
            "language": "typescript",
            "files": [{"path": "README.md"}],
            "symbols_index": {},
            "hotspots": [],
        }
        graph_path.write_text(json.dumps(graph))
        return graph

    def fake_compile_lockfile(
        repo_path: Path,
        tasks_input: Any,
        repo_graph: dict[str, Any],
        llm: Any,
    ) -> AgentLock:
        assert repo_path == repo
        assert tasks_input.tasks[0].id == "readme"
        assert repo_graph["files"][0]["path"] == "README.md"
        assert llm.model == "test"
        return AgentLock(
            version="1.0",
            generated_at=AgentLock.utcnow(),
            generator=Generator(tool="acg", version="test", model="test"),
            repo=Repo(root=str(repo), languages=["typescript"]),
            tasks=[],
            execution_plan=ExecutionPlan(groups=[]),
            conflicts_detected=[],
        )

    class TestLLM:
        model = "test"

        def complete(
            self, messages: list[dict[str, str]], response_format: dict[str, Any] | None = None
        ) -> str:
            return json.dumps({"writes": []})

    monkeypatch.setattr(cli, "scan_context_graph", fake_scan_context_graph)
    monkeypatch.setattr(cli, "compile_lockfile", fake_compile_lockfile)
    monkeypatch.setattr(cli.LLMClient, "from_env", lambda: TestLLM())

    result = runner.invoke(
        app,
        [
            "compile",
            "--repo",
            str(repo),
            "--tasks",
            str(tasks),
            "--out",
            str(out),
            "--language",
            "auto",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls == [("auto", None)]
    assert out.exists()
