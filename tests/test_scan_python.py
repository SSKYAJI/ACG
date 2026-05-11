"""Python repo graph scanner tests."""

from __future__ import annotations

import sys
import types
from pathlib import Path

from acg.repo_graph import scan_context_graph


def test_python_graph_extracts_symbols_imports_and_reverse_imports(tmp_path: Path) -> None:
    (tmp_path / "starlette").mkdir()
    (tmp_path / "starlette" / "__init__.py").write_text("")
    (tmp_path / "starlette" / "requests.py").write_text(
        "class Request:\n"
        "    pass\n"
        "\n"
        "class HTTPConnection:\n"
        "    pass\n"
    )
    (tmp_path / "starlette" / "templating.py").write_text(
        "from .requests import Request\n"
        "\n"
        "class Jinja2Templates:\n"
        "    pass\n"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_templates.py").write_text(
        "from starlette.templating import Jinja2Templates\n"
        "\n"
        "def test_template_response():\n"
        "    assert Jinja2Templates\n"
    )

    graph = scan_context_graph(tmp_path, language="python")

    assert graph["language"] == "python"
    assert graph["symbols_index"]["Jinja2Templates"] == "starlette/templating.py"
    assert graph["symbols_index"]["Request"] == "starlette/requests.py"
    assert graph["resolved_imports"]["starlette/templating.py"] == [
        "starlette/requests.py"
    ]
    assert "tests/test_templates.py" in graph["importers"]["starlette/templating.py"]
    assert "tests/test_templates.py" in graph["tests"]


def test_scan_context_graph_merges_scip_metadata(
    monkeypatch, tmp_path: Path
) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "module.py").write_text("def target():\n    pass\n")

    package = types.ModuleType("acg.localization")
    backend = types.ModuleType("acg.localization.scip_backend")

    def build_scip_metadata(repo_root: Path, language: str, mode: str = "scip"):
        assert repo_root == tmp_path.resolve()
        assert language == "python"
        assert mode == "scip"
        return {
            "scip_summary": {"definition_count": 1, "reference_count": 2},
            "scip_entities": [
                {
                    "path": "pkg/module.py",
                    "symbol": "pkg/module.py::target",
                    "name": "target",
                }
            ],
            "scip_references": [
                {"path": "pkg/module.py", "symbol": "pkg/module.py::target"},
                {"path": "pkg/module.py", "symbol": "pkg/module.py::target"},
            ],
        }

    backend.build_scip_metadata = build_scip_metadata
    monkeypatch.setitem(sys.modules, "acg.localization", package)
    monkeypatch.setitem(sys.modules, "acg.localization.scip_backend", backend)

    graph = scan_context_graph(tmp_path, language="python", localization_backend="scip")

    assert graph["localization_backend"] == "scip"
    assert graph["scip_status"] == {"status": "ok"}
    assert graph["scip_summary"] == {"definition_count": 1, "reference_count": 2}
    entry = graph["files"][0]
    assert entry["scip_symbols"] == ["pkg/module.py::target"]
    assert entry["scip_definition_count"] == 1
    assert entry["scip_reference_count"] == 2


def test_scan_context_graph_preserves_native_graph_when_scip_fails(
    monkeypatch, tmp_path: Path
) -> None:
    (tmp_path / "pkg.py").write_text("VALUE = 1\n")
    package = types.ModuleType("acg.localization")
    backend = types.ModuleType("acg.localization.scip_backend")

    def build_scip_metadata(repo_root: Path, language: str, mode: str = "scip"):
        raise RuntimeError("indexer missing")

    backend.build_scip_metadata = build_scip_metadata
    monkeypatch.setitem(sys.modules, "acg.localization", package)
    monkeypatch.setitem(sys.modules, "acg.localization.scip_backend", backend)

    graph = scan_context_graph(tmp_path, language="python", localization_backend="auto")

    assert graph["language"] == "python"
    assert graph["files"][0]["path"] == "pkg.py"
    assert graph["localization_backend"] == "auto"
    assert graph["scip_status"]["status"] == "failed"
    assert "indexer missing" in graph["scip_status"]["reason"]
