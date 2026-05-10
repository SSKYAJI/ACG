"""Predictor tests with a stubbed LLM client."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from acg.predictor import (
    MAX_PREDICTIONS,
    _detect_test_layout,
    _env_seed,
    _extract_entity_noun,
    _extract_entity_nouns,
    _graph_expansion_seed,
    _index_seed,
    _looks_like_test_task,
    _sibling_pattern_seed,
    _test_scaffold_seed,
    predict_file_scopes,
    predict_writes,
)
from acg.schema import PredictedWrite, TaskInput, TaskInputHints


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
    assert len(writes) <= MAX_PREDICTIONS
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


# --------------------------------------------------------------------------- #
# Env-file seed.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "prompt,should_seed",
    [
        ("Add Google OAuth via NextAuth.", True),
        ("Wire up Stripe checkout.", True),
        ("Add an Auth0 provider.", True),
        ("Refactor the dashboard sidebar.", False),
    ],
)
def test_env_seed_triggers(prompt: str, should_seed: bool, tmp_path: Path) -> None:
    task = TaskInput(id="env", prompt=prompt, hints=None)
    paths = {seed.path for seed in _env_seed(task, tmp_path)}
    assert (".env.example" in paths) is should_seed


def test_env_seed_nextjs_local_augmentation(tmp_path: Path) -> None:
    (tmp_path / "next.config.js").write_text("/** @type {import('next').NextConfig} */\n")
    task = TaskInput(id="oauth", prompt="Add Clerk OAuth provider.", hints=None)
    seeds = _env_seed(task, tmp_path)
    by_path = {seed.path: seed for seed in seeds}
    assert by_path[".env.example"].confidence == 0.8
    assert by_path[".env.local"].confidence == 0.65


# --------------------------------------------------------------------------- #
# Sibling-pattern seed.
# --------------------------------------------------------------------------- #


def test_sibling_pattern_seed_existing_api_dir() -> None:
    task = TaskInput(id="stripe", prompt="Add a Stripe API endpoint.", hints=None)
    graph = {
        "files": [
            {"path": "src/app/api/auth/route.ts"},
            {"path": "src/app/api/health/route.ts"},
            {"path": "src/app/settings/page.tsx"},
        ]
    }
    seeds = _sibling_pattern_seed(task, graph)
    assert len(seeds) == 1
    assert seeds[0].path == "src/app/api/stripe/route.ts"
    assert seeds[0].confidence == 0.75


def test_sibling_pattern_seed_requires_siblings() -> None:
    task = TaskInput(id="stripe", prompt="Add a Stripe API endpoint.", hints=None)
    graph = {"files": [{"path": "src/app/api/auth/route.ts"}]}
    assert _sibling_pattern_seed(task, graph) == []


def test_sibling_pattern_seed_entity_fallback() -> None:
    task = TaskInput(id="webhook", prompt="Implement webhook endpoint.", hints=None)
    graph = {
        "files": [
            {"path": "src/app/api/auth/route.ts"},
            {"path": "src/app/api/health/route.ts"},
        ]
    }
    seeds = _sibling_pattern_seed(task, graph)
    assert [seed.path for seed in seeds] == ["src/app/api/webhook/route.ts"]


# --------------------------------------------------------------------------- #
# Multi-entity test scaffolding.
# --------------------------------------------------------------------------- #


def test_extract_entity_nouns_collects_multiple_entities() -> None:
    assert _extract_entity_nouns("Add Playwright tests covering login and signup") == [
        "login",
        "signup",
    ]
    assert _extract_entity_noun("Add Playwright tests covering login and signup") == "login"


def test_test_scaffold_seed_multi_entity_playwright(tmp_path: Path) -> None:
    task = TaskInput(
        id="tests",
        prompt="Add Playwright e2e tests covering login and signup",
        hints=TaskInputHints(touches=["tests"]),
    )
    paths = {seed.path for seed in _test_scaffold_seed(task, tmp_path)}
    assert "tests/e2e/login.spec.ts" in paths
    assert "tests/e2e/signup.spec.ts" in paths


# --------------------------------------------------------------------------- #
# Demo trace regression.
# --------------------------------------------------------------------------- #


def test_demo_env_regression_predicts_oauth_and_billing_env_example(tmp_path: Path) -> None:
    (tmp_path / "next.config.js").write_text("const config = {};\nmodule.exports = config;\n")
    graph = {
        "language": "typescript",
        "files": [
            {"path": ".env.example"},
            {"path": "prisma/schema.prisma"},
            {"path": "src/app/api/auth/[...nextauth]/route.ts"},
            {"path": "src/app/api/health/route.ts"},
            {"path": "src/components/Sidebar.tsx"},
            {"path": "src/server/auth/config.ts"},
            {"path": "src/server/auth/index.ts"},
        ],
        "symbols_index": {},
        "hotspots": ["prisma/schema.prisma", "src/components/Sidebar.tsx"],
    }
    tasks = [
        TaskInput(
            id="oauth",
            prompt="Add Google OAuth login. Use NextAuth. Update Prisma schema with required fields.",
            hints=TaskInputHints(touches=["auth", "prisma"]),
        ),
        TaskInput(
            id="billing",
            prompt=(
                "Add a billing dashboard tab at /dashboard/billing with Stripe integration. "
                "Add a sidebar entry. Update Prisma with subscription model."
            ),
            hints=TaskInputHints(touches=["billing", "prisma", "navigation"]),
        ),
    ]
    llm = StubLLM(json.dumps({"writes": []}))

    for task in tasks:
        paths = {write.path for write in predict_writes(task, graph, llm, repo_root=tmp_path)}
        assert ".env.example" in paths


def test_predict_writes_composes_env_sibling_and_multi_entity_seeds(tmp_path: Path) -> None:
    task = TaskInput(
        id="tests",
        prompt=(
            "Add Playwright e2e tests covering login and signup, then create Stripe API route "
            "with provider credentials."
        ),
        hints=TaskInputHints(touches=["tests"]),
    )
    graph = {
        "files": [
            {"path": "src/app/api/auth/route.ts"},
            {"path": "src/app/api/health/route.ts"},
        ],
        "symbols_index": {},
    }
    writes = predict_writes(task, graph, StubLLM(json.dumps({"writes": []})), repo_root=tmp_path)
    paths = {write.path for write in writes}
    assert ".env.example" in paths
    assert "src/app/api/stripe/route.ts" in paths
    assert "tests/e2e/login.spec.ts" in paths
    assert "tests/e2e/signup.spec.ts" in paths


# --------------------------------------------------------------------------- #
# Index aggregator seed (acg.index.aggregate wired into predict_writes).
# --------------------------------------------------------------------------- #


def test_index_seed_passes_through_aggregator_predictions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Aggregator predictions above the floor surface become candidate context."""
    fake_predictions = [
        PredictedWrite(
            path="src/lib/cross-indexer-hit.ts",
            confidence=0.7,
            reason="framework + bm25 fusion",
        ),
        PredictedWrite(
            path="src/lib/below-floor.ts",
            confidence=0.3,
            reason="weak co-change signal",
        ),
    ]

    def fake_aggregate(*_args: Any, **_kwargs: Any) -> list[PredictedWrite]:
        return fake_predictions

    monkeypatch.setattr("acg.index.aggregate", fake_aggregate)

    task = TaskInput(id="x", prompt="Add a feature.")
    scopes = predict_file_scopes(
        task, {}, StubLLM(json.dumps({"writes": []})), repo_root=tmp_path
    )
    by_path = {scope.path: scope for scope in scopes}

    assert by_path["src/lib/cross-indexer-hit.ts"].tier == "candidate_context"
    assert "src/lib/below-floor.ts" not in by_path


def test_index_seed_skipped_without_repo_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When repo_root is None the aggregator is never invoked."""
    calls: list[Any] = []

    def tally(*args: Any, **kwargs: Any) -> list[PredictedWrite]:
        calls.append((args, kwargs))
        return []

    monkeypatch.setattr("acg.index.aggregate", tally)

    task = TaskInput(id="readme", prompt="Update README.md.")
    writes = predict_writes(task, {}, StubLLM(json.dumps({"writes": []})))

    assert calls == []
    assert any(write.path == "README.md" for write in writes)


def test_index_seed_swallows_aggregator_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If aggregate raises, predict_writes still returns existing seed paths."""

    def boom(*_args: Any, **_kwargs: Any) -> list[PredictedWrite]:
        raise RuntimeError("synthetic indexer failure")

    monkeypatch.setattr("acg.index.aggregate", boom)

    task = TaskInput(id="readme", prompt="Update README.md.")
    writes = predict_writes(
        task, {}, StubLLM(json.dumps({"writes": []})), repo_root=tmp_path
    )
    paths = {write.path for write in writes}

    assert "README.md" in paths


def test_index_seed_unit_filters_below_floor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """_index_seed itself drops any prediction below SEED_INDEX_CONFIDENCE_FLOOR."""
    monkeypatch.setattr(
        "acg.index.aggregate",
        lambda *_a, **_k: [
            PredictedWrite(path="keep.ts", confidence=0.9, reason="strong"),
            PredictedWrite(path="drop.ts", confidence=0.49, reason="weak"),
        ],
    )
    task = TaskInput(id="x", prompt="Add a feature.")
    seeds = _index_seed(task, tmp_path, {})
    paths = {seed.path for seed in seeds}
    assert paths == {"keep.ts"}


# --------------------------------------------------------------------------- #
# Graph expansion.
# --------------------------------------------------------------------------- #


def test_graph_expansion_seed_promotes_reverse_import_and_type_links() -> None:
    task = TaskInput(
        id="media",
        prompt="Add request mediaType validation and request handling changes.",
    )
    graph = {
        "files": [
            {
                "path": "lib/content-type-parser.js",
                "symbols": ["ContentTypeParser"],
                "resolved_imports": ["lib/symbols.js"],
                "importers": [],
                "type_links": [],
            },
            {
                "path": "lib/handle-request.js",
                "symbols": ["handleRequest"],
                "resolved_imports": ["lib/validation.js", "lib/symbols.js"],
                "importers": [],
                "type_links": [],
            },
            {
                "path": "lib/validation.js",
                "symbols": ["validate"],
                "resolved_imports": ["lib/symbols.js"],
                "importers": ["lib/handle-request.js"],
                "type_links": [],
            },
            {
                "path": "lib/symbols.js",
                "symbols": ["kRequestCacheValidateFns"],
                "resolved_imports": [],
                "importers": ["lib/handle-request.js", "lib/validation.js"],
                "type_links": [],
            },
            {
                "path": "types/request.d.ts",
                "symbols": ["FastifyRequest"],
                "resolved_imports": [],
                "importers": [],
                "type_links": ["lib/request.js"],
            },
            {
                "path": "lib/request.js",
                "symbols": ["Request"],
                "resolved_imports": ["lib/symbols.js"],
                "importers": [],
                "type_links": ["types/request.d.ts"],
            },
        ],
            "resolved_imports": {
                "lib/content-type-parser.js": ["lib/symbols.js"],
                "lib/handle-request.js": ["lib/validation.js", "lib/symbols.js"],
                "lib/validation.js": ["lib/symbols.js"],
                "lib/request.js": ["lib/symbols.js"],
        },
        "importers": {
            "lib/validation.js": ["lib/handle-request.js"],
            "lib/symbols.js": ["lib/handle-request.js", "lib/validation.js"],
        },
        "type_links": {
            "types/request.d.ts": ["lib/request.js"],
            "lib/request.js": ["types/request.d.ts"],
        },
    }
    seeds = [
        PredictedWrite(path="lib/content-type-parser.js", confidence=0.95, reason="seed"),
        PredictedWrite(path="lib/handle-request.js", confidence=0.95, reason="seed"),
        PredictedWrite(path="types/request.d.ts", confidence=0.95, reason="seed"),
    ]

    expansions = _graph_expansion_seed(task, graph, seeds)
    by_path = {write.path: write for write in expansions}

    assert "lib/validation.js" in by_path
    assert "lib/request.js" in by_path
    assert "lib/symbols.js" in by_path
    assert by_path["lib/request.js"].confidence >= 0.7


def test_predict_file_scopes_keeps_graph_expansion_as_candidate_context() -> None:
    task = TaskInput(id="media", prompt="Refactor handleRequest request handling validation.")
    graph = {
        "files": [
            {
                "path": "lib/handle-request.js",
                "symbols": ["handleRequest"],
                "resolved_imports": ["lib/validation.js"],
                "importers": [],
                "type_links": [],
            },
            {
                "path": "lib/validation.js",
                "symbols": ["validate"],
                "resolved_imports": [],
                "importers": ["lib/handle-request.js"],
                "type_links": [],
            },
        ],
        "symbols_index": {"handleRequest": "lib/handle-request.js"},
        "resolved_imports": {"lib/handle-request.js": ["lib/validation.js"]},
        "importers": {"lib/validation.js": ["lib/handle-request.js"]},
    }

    scopes = predict_file_scopes(task, graph, StubLLM(json.dumps({"writes": []})))
    by_path = {scope.path: scope for scope in scopes}

    assert by_path["lib/handle-request.js"].tier == "must_write"
    assert by_path["lib/validation.js"].tier == "candidate_context"
