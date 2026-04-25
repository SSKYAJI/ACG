"""Predictor tests with a stubbed LLM client."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from acg.predictor import (
    _detect_test_layout,
    _extract_entity_noun,
    _looks_like_test_task,
    _test_scaffold_seed,
    predict_writes,
)
from acg.schema import TaskInput, TaskInputHints


class StubLLM:
    """LLM stand-in returning a fixed JSON reply."""

    model = "stub"

    def __init__(self, reply: str) -> None:
        self._reply = reply
        self.calls: list[list[dict[str, str]]] = []

    def complete(
        self, messages: list[dict[str, str]], response_format: dict[str, Any] | None = None
    ) -> str:
        self.calls.append(messages)
        return self._reply


@pytest.fixture
def repo_graph() -> dict[str, Any]:
    return {
        "language": "typescript",
        "files": [
            {
                "path": "lib/auth.ts",
                "exports": ["authOptions", "getCurrentUser"],
                "imports": ["next-auth"],
                "is_hotspot": True,
            },
            {
                "path": "components/sidebar.tsx",
                "exports": ["Sidebar"],
                "imports": ["next/link"],
                "is_hotspot": True,
            },
        ],
        "symbols_index": {
            "authOptions": "lib/auth.ts",
            "getCurrentUser": "lib/auth.ts",
        },
        "hotspots": ["lib/auth.ts", "components/sidebar.tsx"],
    }


def test_static_seed_picks_up_explicit_file_mention(repo_graph: dict[str, Any]) -> None:
    task = TaskInput(
        id="readme",
        prompt="Update README.md with a quickstart.",
        hints=TaskInputHints(touches=["docs"]),
    )
    llm = StubLLM(json.dumps({"writes": []}))
    writes = predict_writes(task, repo_graph, llm)
    assert any(w.path == "README.md" and w.confidence >= 0.9 for w in writes)


def test_symbol_seed_uses_repo_graph(repo_graph: dict[str, Any]) -> None:
    task = TaskInput(
        id="auth",
        prompt="Refactor authOptions to add a Google provider.",
        hints=TaskInputHints(touches=["auth"]),
    )
    llm = StubLLM(json.dumps({"writes": []}))
    writes = predict_writes(task, repo_graph, llm)
    paths = {w.path for w in writes}
    assert "lib/auth.ts" in paths


def test_llm_rerank_can_add_files(repo_graph: dict[str, Any]) -> None:
    task = TaskInput(
        id="settings",
        prompt="Redesign the settings page and tweak the sidebar entry.",
        hints=TaskInputHints(touches=["settings", "navigation"]),
    )
    rerank = {
        "writes": [
            {
                "path": "app/settings/page.tsx",
                "confidence": 0.95,
                "reason": "Settings page route.",
            },
            {
                "path": "components/sidebar.tsx",
                "confidence": 0.85,
                "reason": "Sidebar tweak.",
            },
        ]
    }
    llm = StubLLM(json.dumps(rerank))
    writes = predict_writes(task, repo_graph, llm)
    paths = {w.path for w in writes}
    assert "app/settings/page.tsx" in paths
    assert "components/sidebar.tsx" in paths


def test_llm_failure_falls_back_to_seeds(repo_graph: dict[str, Any]) -> None:
    class BoomLLM:
        model = "boom"

        def complete(self, messages, response_format=None):  # type: ignore[no-untyped-def]
            raise RuntimeError("network down")

    task = TaskInput(
        id="readme",
        prompt="Update README.md with new install steps.",
        hints=None,
    )
    writes = predict_writes(task, repo_graph, BoomLLM())
    assert writes, "fallback should still return seed predictions"
    assert all(w.path == "README.md" for w in writes)


def test_malformed_llm_reply_is_ignored(repo_graph: dict[str, Any]) -> None:
    task = TaskInput(
        id="auth",
        prompt="Refactor authOptions.",
        hints=None,
    )
    llm = StubLLM("not json at all { ::")
    writes = predict_writes(task, repo_graph, llm)
    # Symbol seed still finds lib/auth.ts even though the LLM reply was junk.
    assert any(w.path == "lib/auth.ts" for w in writes)


def test_predictions_are_capped_and_sorted(repo_graph: dict[str, Any]) -> None:
    rerank = {
        "writes": [
            {"path": f"file_{i}.ts", "confidence": 0.5 + 0.01 * i, "reason": ""}
            for i in range(20)
        ]
    }
    task = TaskInput(id="big", prompt="Touch many files.", hints=None)
    writes = predict_writes(task, repo_graph, StubLLM(json.dumps(rerank)))
    assert len(writes) <= 8
    assert all(
        writes[i].confidence >= writes[i + 1].confidence
        for i in range(len(writes) - 1)
    )


# --------------------------------------------------------------------------- #
# Test-scaffold seed (Track A from the file-set prediction research dump).
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "prompt,expected",
    [
        ("Write end-to-end Playwright tests for the checkout flow.", "checkout"),
        ("Add unit tests for the auth helper.", "auth"),
        ("Tests covering signup.", "signup"),
        ("Implement the billing endpoint correctly.", "billing"),
        ("Refactor the search component.", "search"),
        ("Write some tests.", None),  # no entity → None
        ("Update README.md.", None),  # not a test task → None
    ],
)
def test_extract_entity_noun(prompt: str, expected: str | None) -> None:
    assert _extract_entity_noun(prompt) == expected


@pytest.mark.parametrize(
    "prompt,is_test",
    [
        ("Write Playwright tests for the checkout flow.", True),
        ("Add e2e specs for billing.", True),
        ("Set up vitest for the math util.", True),
        ("Refactor the auth module.", False),
        ("Add a Stripe webhook endpoint.", False),
    ],
)
def test_looks_like_test_task(prompt: str, is_test: bool) -> None:
    assert _looks_like_test_task(prompt) is is_test


def test_test_scaffold_seed_greenfield_playwright(tmp_path: Path) -> None:
    """No config file on disk + 'Playwright' in prompt → defaults + config."""
    task = TaskInput(
        id="tests",
        prompt="Write end-to-end Playwright tests for the checkout flow.",
        hints=TaskInputHints(touches=["tests"]),
    )
    seeds = _test_scaffold_seed(task, tmp_path)
    paths = {s.path for s in seeds}
    assert "playwright.config.ts" in paths
    assert "tests/e2e/checkout.spec.ts" in paths
    assert all(s.confidence >= 0.8 for s in seeds)


def test_test_scaffold_seed_existing_playwright_config(tmp_path: Path) -> None:
    """Real config file on disk → uses its testDir, omits the config seed."""
    cfg = tmp_path / "playwright.config.ts"
    cfg.write_text(
        "import { defineConfig } from '@playwright/test';\n"
        "export default defineConfig({ testDir: './e2e' });\n"
    )
    task = TaskInput(
        id="tests",
        prompt="Add Playwright tests for the signup feature.",
        hints=None,
    )
    seeds = _test_scaffold_seed(task, tmp_path)
    paths = {s.path for s in seeds}
    # Config exists → don't predict it again.
    assert "playwright.config.ts" not in paths
    # testDir from config is honoured.
    assert "e2e/signup.spec.ts" in paths


def test_test_scaffold_seed_pytest_existing_pyproject(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\ntestpaths = ['tests']\n"
    )
    task = TaskInput(
        id="tests",
        prompt="Add pytest unit tests for the billing helper.",
        hints=None,
    )
    seeds = _test_scaffold_seed(task, tmp_path)
    paths = {s.path for s in seeds}
    assert "tests/test_billing.py" in paths


def test_test_scaffold_seed_returns_empty_for_non_test_tasks(tmp_path: Path) -> None:
    task = TaskInput(
        id="oauth",
        prompt="Add Google OAuth via NextAuth.",
        hints=TaskInputHints(touches=["auth"]),
    )
    assert _test_scaffold_seed(task, tmp_path) == []


def test_test_scaffold_seed_returns_empty_when_no_signal(tmp_path: Path) -> None:
    """Test task but no framework keyword and no config → cannot guess."""
    task = TaskInput(
        id="tests",
        prompt="Write some integration tests.",
        hints=TaskInputHints(touches=["tests"]),
    )
    # tmp_path has no config files of any kind.
    assert _test_scaffold_seed(task, tmp_path) == []


def test_detect_test_layout_prefers_existing_config_over_keyword(tmp_path: Path) -> None:
    """If both a Vitest config file AND a 'playwright' keyword are present,
    the on-disk config wins because it represents the project's actual stance."""
    (tmp_path / "vitest.config.ts").write_text("export default {};\n")
    layout = _detect_test_layout(tmp_path, "Add Playwright tests for billing.")
    assert layout is not None
    framework, _td, _ext, _cfg = layout
    assert framework == "vitest"


def test_test_scaffold_seed_integrated_with_predict_writes(
    tmp_path: Path, repo_graph: dict[str, Any]
) -> None:
    """End-to-end: predict_writes() with repo_root surfaces test-scaffold seeds."""
    task = TaskInput(
        id="tests",
        prompt="Write end-to-end Playwright tests for the checkout flow.",
        hints=TaskInputHints(touches=["tests"]),
    )
    llm = StubLLM(json.dumps({"writes": []}))
    writes = predict_writes(task, repo_graph, llm, repo_root=tmp_path)
    paths = {w.path for w in writes}
    assert "playwright.config.ts" in paths
    assert "tests/e2e/checkout.spec.ts" in paths


def test_test_scaffold_seed_missing_repo_root_is_safe(
    repo_graph: dict[str, Any]
) -> None:
    """predict_writes must not crash when repo_root is None and the seed
    can still infer from the prompt keyword alone."""
    task = TaskInput(
        id="tests",
        prompt="Write Playwright tests for the checkout flow.",
        hints=None,
    )
    llm = StubLLM(json.dumps({"writes": []}))
    writes = predict_writes(task, repo_graph, llm, repo_root=None)
    paths = {w.path for w in writes}
    # Greenfield: config-file seed still fires because repo_root is None
    # (we treat that as "config does not exist").
    assert "playwright.config.ts" in paths
    assert any(p.endswith("checkout.spec.ts") for p in paths)
