from __future__ import annotations

import json
from pathlib import Path

import pytest

from acg.repo_graph import (
    context_graph_path,
    detect_language,
    load_context_graph,
    normalize_context_graph,
    scan_context_graph,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures"
TINY_JAVA_REPO = FIXTURE_ROOT / "tiny_java_repo"
TINY_PY_RUNTIME = FIXTURE_ROOT / "tiny_py_runtime"
TINY_PY_LIB = FIXTURE_ROOT / "tiny_py_lib"


def test_normalize_context_graph_adds_structural_indexes(tmp_path: Path) -> None:
    graph = {
        "language": "typescript",
        "files": [
            {
                "path": "src/app/api/health/route.ts",
                "imports": ["@/lib/auth"],
                "exports": ["GET"],
                "symbols": ["GET"],
                "is_hotspot": False,
            },
            {
                "path": "src/app/settings/page.tsx",
                "exports": ["SettingsPage"],
                "symbols": ["SettingsPage"],
                "is_hotspot": True,
                "imported_by_count": 3,
            },
            {"path": "playwright.config.ts"},
            {"path": "tests/e2e/settings.spec.ts"},
        ],
    }

    normalized = normalize_context_graph(graph, repo_root=tmp_path)

    assert normalized["root"] == str(tmp_path.resolve())
    assert normalized["languages"] == ["typescript"]
    assert normalized["imports"] == {
        "playwright.config.ts": [],
        "src/app/api/health/route.ts": ["@/lib/auth"],
        "src/app/settings/page.tsx": [],
        "tests/e2e/settings.spec.ts": [],
    }
    assert normalized["exports"]["src/app/settings/page.tsx"] == ["SettingsPage"]
    assert normalized["symbols_index"]["GET"] == "src/app/api/health/route.ts"
    assert normalized["hotspots"] == ["src/app/settings/page.tsx"]
    assert normalized["configs"] == ["playwright.config.ts"]
    assert normalized["tests"] == ["tests/e2e/settings.spec.ts"]
    assert normalized["routes"] == [
        {"kind": "api", "path": "src/app/api/health/route.ts", "route": "/api/health"},
        {"kind": "page", "path": "src/app/settings/page.tsx", "route": "/settings"},
    ]


def test_load_context_graph_normalizes_existing_file(tmp_path: Path) -> None:
    graph_path = context_graph_path(tmp_path)
    graph_path.parent.mkdir(parents=True)
    graph_path.write_text(
        json.dumps(
            {
                "language": "java",
                "files": [
                    {
                        "path": "src/test/java/com/example/AccountTest.java",
                        "imports": [],
                        "exports": ["AccountTest"],
                    },
                    {"path": "pom.xml"},
                ],
            }
        )
    )

    graph = load_context_graph(tmp_path)

    assert graph["language"] == "java"
    assert graph["configs"] == ["pom.xml"]
    assert graph["tests"] == ["src/test/java/com/example/AccountTest.java"]
    assert graph["symbols_index"]["AccountTest"] == "src/test/java/com/example/AccountTest.java"


def test_detect_language_prefers_build_files(tmp_path: Path) -> None:
    (tmp_path / "pom.xml").write_text("<project />\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.ts").write_text("export const x = 1;\n")

    assert detect_language(tmp_path) == "java"


def test_scan_context_graph_java_writes_normalized_graph(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    out = tmp_path / "context_graph.json"

    def fake_scan_java(repo_root: Path, out_path: Path) -> dict:
        assert repo_root == TINY_JAVA_REPO.resolve()
        assert out_path == out.resolve()
        return {
            "version": "1.0",
            "root": str(repo_root),
            "language": "java",
            "files": [
                {
                    "path": "src/main/java/com/example/events/EventService.java",
                    "imports": ["com.example.config.DatabaseConfig"],
                    "exports": ["EventService"],
                    "symbols": ["EventService"],
                },
                {"path": "pom.xml"},
            ],
            "symbols_index": {"EventService": "src/main/java/com/example/events/EventService.java"},
            "hotspots": [],
        }

    import acg.repo_graph as repo_graph

    monkeypatch.setattr(repo_graph, "_scan_java", fake_scan_java)
    graph = scan_context_graph(TINY_JAVA_REPO, "java", out)
    on_disk = json.loads(out.read_text())

    assert graph["language"] == "java"
    assert graph["configs"] == ["pom.xml"]
    assert "src/main/java/com/example/events/EventService.java" in graph["imports"]
    assert graph["symbols_index"]["EventService"].endswith("EventService.java")
    assert on_disk["configs"] == graph["configs"]


def test_detect_language_python_via_pyproject(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    assert detect_language(tmp_path) == "python"


def test_detect_language_python_runtime_fixture() -> None:
    assert detect_language(TINY_PY_RUNTIME) == "python"


def test_detect_language_python_lib_fixture_src_layout() -> None:
    # ``setup.cfg`` (Python) plus a ``src/`` layout should still resolve to python.
    assert detect_language(TINY_PY_LIB) == "python"


def test_scan_context_graph_python_runtime_fixture(tmp_path: Path) -> None:
    out = tmp_path / "context_graph.json"
    graph = scan_context_graph(TINY_PY_RUNTIME, "python", out)

    assert graph["language"] == "python"
    assert graph["languages"] == ["python"]
    # Normalized fields populated for Python the same way as TS/Java.
    assert "app/main.py" in graph["imports"]
    assert "app.config.settings" in graph["imports"]["app/main.py"]
    # normalize_context_graph sorts list fields, so __all__ insertion order
    # is not preserved here; the scanner-level test in test_scan_python.py
    # verifies the raw insertion-order behavior.
    assert sorted(graph["exports"]["app/config.py"]) == ["Settings", "settings"]
    assert graph["symbols_index"]["health"] == "app/main.py"
    assert "pyproject.toml" in graph["configs"]
    assert "tests/test_items.py" in graph["tests"]
    assert any(
        r["path"] == "app/main.py" and r["route"] == "/health"
        for r in graph["routes"]
    )
    # Round-trip: re-reading the on-disk graph yields the same symbols_index.
    on_disk = json.loads(out.read_text())
    assert on_disk["symbols_index"]["health"] == "app/main.py"
    assert on_disk["language"] == "python"


def test_scan_context_graph_python_lib_fixture(tmp_path: Path) -> None:
    out = tmp_path / "context_graph.json"
    graph = scan_context_graph(TINY_PY_LIB, "python", out)

    assert graph["language"] == "python"
    assert graph["routes"] == []
    assert "src/tinypkg/__init__.py" in graph["exports"]
    assert graph["exports"]["src/tinypkg/__init__.py"] == sorted(["greet", "slugify"])
    assert "setup.cfg" in graph["configs"]
    assert "tests/test_core.py" in graph["tests"]


def test_scan_context_graph_python_dispatch_via_monkeypatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Mirrors the Java dispatch test: ensure scan_context_graph routes
    # ``language="python"`` through ``_scan_python``.
    out = tmp_path / "context_graph.json"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname='x'\n")

    captured: dict[str, Path] = {}

    def fake_scan_python(repo_root: Path, out_path: Path) -> dict:
        captured["repo_root"] = repo_root
        captured["out_path"] = out_path
        return {
            "version": "1.0",
            "root": str(repo_root),
            "language": "python",
            "files": [
                {
                    "path": "pkg/mod.py",
                    "imports": ["os"],
                    "exports": ["thing"],
                    "symbols": ["thing"],
                },
            ],
            "symbols_index": {"thing": "pkg/mod.py"},
            "hotspots": [],
        }

    import acg.repo_graph as repo_graph

    monkeypatch.setattr(repo_graph, "_scan_python", fake_scan_python)
    graph = scan_context_graph(repo, "python", out)

    assert captured["repo_root"] == repo.resolve()
    assert captured["out_path"] == out.resolve()
    assert graph["language"] == "python"
    assert graph["symbols_index"]["thing"] == "pkg/mod.py"
