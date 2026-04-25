from __future__ import annotations

from pathlib import Path

from acg.index.pagerank import PageRankIndexer, build_symbol_graph
from acg.schema import TaskInput

TINY = Path(__file__).resolve().parents[1] / "fixtures" / "tiny_repo"


def test_symbol_graph_builds_cross_file_edges() -> None:
    graph = build_symbol_graph(TINY, {})

    assert "components/sidebar.tsx" in graph.graph
    assert graph.graph.has_edge("app/layout.tsx", "components/sidebar.tsx")


def test_personalization_steers_ranking_to_sidebar() -> None:
    task = TaskInput(id="nav", prompt="Update Sidebar navigation items")
    writes = PageRankIndexer().predict(task, TINY, {})

    assert writes[0].path == "components/sidebar.tsx"
    assert "Sidebar" in writes[0].reason


def test_personalization_steers_ranking_to_prisma() -> None:
    task = TaskInput(id="db", prompt="Refactor prisma database helper")
    writes = PageRankIndexer().predict(task, TINY, {})

    assert writes[0].path == "lib/prisma.ts"


def test_centrality_picks_hotspot_without_symbol_match() -> None:
    task = TaskInput(id="cleanup", prompt="Clean up shared dependencies")
    writes = PageRankIndexer().predict(task, TINY, {})

    assert any(write.path == "components/sidebar.tsx" for write in writes[:3])
