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
