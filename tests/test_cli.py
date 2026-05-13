"""Tests for the Typer CLI entry-points.

Covers ``acg validate-lockfile`` end-to-end against the bundled example
lockfiles and the JSON Schema in ``schema/agent_lock.schema.json``.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import acg.cli as cli
from acg.cli import app
from acg.schema import AgentLock, ExecutionPlan, Generator, Group, PredictedWrite, Repo, Task

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
        repo_path: Path,
        language: str,
        out_path: Path | None,
        localization_backend: str = "native",
    ) -> dict[str, Any]:
        assert repo_path == repo
        assert language == "java"
        assert out_path == out
        assert localization_backend == "scip"
        out.write_text(json.dumps({"language": "java", "files": [], "hotspots": []}))
        return {"language": "java", "files": [], "hotspots": []}

    monkeypatch.setattr(cli, "scan_context_graph", fake_scan_context_graph)

    result = runner.invoke(
        app,
        [
            "init-graph",
            "--repo",
            str(repo),
            "--language",
            "java",
            "--out",
            str(out),
            "--localization-backend",
            "scip",
        ],
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
    calls: list[tuple[str, Path | None, str]] = []

    def fake_scan_context_graph(
        repo_path: Path,
        language: str,
        out_path: Path | None = None,
        localization_backend: str = "native",
    ) -> dict[str, Any]:
        calls.append((language, out_path, localization_backend))
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
    monkeypatch.setattr(cli.LLMClient, "from_env_for_compile", lambda: TestLLM())

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
            "--localization-backend",
            "auto",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls == [("auto", None, "auto")]
    assert out.exists()


def test_plan_tasks_command_writes_orchestrated_tasks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    out = tmp_path / "tasks.json"

    def fake_scan_context_graph(
        repo_path: Path,
        language: str,
        out_path: Path | None = None,
        localization_backend: str = "native",
    ) -> dict[str, Any]:
        assert localization_backend == "scip"
        del language, out_path
        graph_path = repo_path / ".acg" / "context_graph.json"
        graph_path.parent.mkdir(parents=True)
        graph = {
            "version": "1.0",
            "root": str(repo_path),
            "language": "typescript",
            "files": [{"path": "src/server/auth/config.ts"}],
            "hotspots": [],
        }
        graph_path.write_text(json.dumps(graph))
        return graph

    class PlannerLLM:
        model = "planner-test"

        def complete(
            self, messages: list[dict[str, str]], response_format: dict[str, Any] | None = None
        ) -> str:
            del messages, response_format
            return json.dumps(
                {
                    "tasks": [
                        {
                            "id": "add-auth",
                            "prompt": "Add auth support.",
                            "hints": {"touches": ["auth"]},
                        }
                    ]
                }
            )

    monkeypatch.setattr(cli, "scan_context_graph", fake_scan_context_graph)
    monkeypatch.setattr(cli.LLMClient, "from_env", lambda: PlannerLLM())

    result = runner.invoke(
        app,
        [
            "plan-tasks",
            "--repo",
            str(repo),
            "--goal",
            "Add auth support",
            "--out",
            str(out),
            "--localization-backend",
            "scip",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(out.read_text())
    assert payload["tasks"][0]["id"] == "add-auth"
    assert payload["tasks"][0]["hints"]["touches"] == ["auth"]


def test_validate_diff_command_blocks_oob_worktree_change(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "acg@example.com"],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "ACG Test"],
        check=True,
        capture_output=True,
        text=True,
    )
    (repo / "README.md").write_text("initial\n")
    subprocess.run(
        ["git", "-C", str(repo), "add", "README.md"],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "initial"],
        check=True,
        capture_output=True,
        text=True,
    )
    (repo / "README.md").write_text("changed\n")
    (repo / "src").mkdir()
    (repo / "src" / "oops.ts").write_text("export {}\n")

    lock = AgentLock(
        generated_at=AgentLock.utcnow(),
        repo=Repo(root=str(repo), languages=["typescript"]),
        tasks=[
            Task(
                id="docs",
                prompt="Update docs.",
                predicted_writes=[PredictedWrite(path="README.md", confidence=0.9, reason="docs")],
                allowed_paths=["README.md"],
                depends_on=[],
                parallel_group=1,
            )
        ],
        execution_plan=ExecutionPlan(groups=[Group(id=1, tasks=["docs"], type="serial")]),
    )
    lock_path = tmp_path / "agent_lock.json"
    lock_path.write_text(lock.model_dump_json())

    result = runner.invoke(
        app,
        [
            "validate-diff",
            "--lock",
            str(lock_path),
            "--repo",
            str(repo),
            "--task",
            "docs",
            "--json",
        ],
    )

    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload["allowed_count"] == 1
    assert payload["blocked_count"] == 1
    assert payload["verdicts"][1]["path"] == "src/oops.ts"
