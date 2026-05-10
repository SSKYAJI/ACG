"""Unit tests for ``graph_builder.scan_python`` (LibCST-based)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from graph_builder import scan_python

FIXTURE_ROOT = Path(__file__).parent / "fixtures"
TINY_PY_RUNTIME = FIXTURE_ROOT / "tiny_py_runtime"
TINY_PY_LIB = FIXTURE_ROOT / "tiny_py_lib"


def _file_node(graph: dict, rel_path: str) -> dict:
    for entry in graph["files"]:
        if entry["path"] == rel_path:
            return entry
    raise AssertionError(f"file {rel_path!r} not in graph")


# ---------------------------------------------------------------------------
# Runtime fixture (FastAPI)
# ---------------------------------------------------------------------------


def test_runtime_fixture_top_level_shape() -> None:
    graph = scan_python.scan(TINY_PY_RUNTIME)

    assert graph["language"] == "python"
    assert graph["version"] == "1.0"
    assert isinstance(graph["scanned_at"], str)
    assert isinstance(graph["files"], list) and graph["files"]


def test_runtime_fixture_extracts_fastapi_routes() -> None:
    graph = scan_python.scan(TINY_PY_RUNTIME)
    routes = graph["routes"]
    paths_routes = {(r["path"], r["route"]) for r in routes}
    assert ("app/main.py", "/health") in paths_routes
    assert ("app/routers/items.py", "/") in paths_routes
    assert all(r["kind"] == "api" for r in routes)


def test_runtime_fixture_resolves_relative_imports() -> None:
    graph = scan_python.scan(TINY_PY_RUNTIME)
    main = _file_node(graph, "app/main.py")
    # ``from .config import settings`` -> "app.config.settings"
    # ``from .routers import items`` -> "app.routers.items"
    assert "app.config.settings" in main["imports"]
    assert "app.routers.items" in main["imports"]
    # The relative import should bump imported_by_count for both targets.
    assert _file_node(graph, "app/routers/items.py")["imported_by_count"] >= 1
    assert _file_node(graph, "app/config.py")["imported_by_count"] >= 1


def test_runtime_fixture_honors_dunder_all() -> None:
    graph = scan_python.scan(TINY_PY_RUNTIME)
    config = _file_node(graph, "app/config.py")
    assert config["exports"] == ["settings", "Settings"]
    # __all__ itself must NOT leak into symbols / symbols_index.
    assert "__all__" not in config["symbols"]
    assert "__all__" not in graph["symbols_index"]


def test_runtime_fixture_class_methods_in_symbols() -> None:
    graph = scan_python.scan(TINY_PY_RUNTIME)
    config = _file_node(graph, "app/config.py")
    assert "Settings" in config["symbols"]


def test_runtime_fixture_pyproject_surfaced_as_asset() -> None:
    graph = scan_python.scan(TINY_PY_RUNTIME)
    paths = {entry["path"] for entry in graph["files"]}
    assert "pyproject.toml" in paths


# ---------------------------------------------------------------------------
# Non-runtime fixture (library, src/ layout)
# ---------------------------------------------------------------------------


def test_lib_fixture_src_layout_module_index() -> None:
    graph = scan_python.scan(TINY_PY_LIB)
    paths = {entry["path"] for entry in graph["files"]}
    # Files keep their on-disk paths (with ``src/`` prefix), but the resolver
    # should still link cross-file imports.
    assert "src/tinypkg/__init__.py" in paths
    assert "src/tinypkg/core.py" in paths
    init = _file_node(graph, "src/tinypkg/__init__.py")
    # ``from .core import greet`` resolves to ``tinypkg.core.greet``.
    assert "tinypkg.core.greet" in init["imports"]
    assert _file_node(graph, "src/tinypkg/core.py")["imported_by_count"] >= 1
    assert _file_node(graph, "src/tinypkg/util.py")["imported_by_count"] >= 1


def test_lib_fixture_dunder_all_exports() -> None:
    graph = scan_python.scan(TINY_PY_LIB)
    init = _file_node(graph, "src/tinypkg/__init__.py")
    assert init["exports"] == ["greet", "slugify"]


def test_lib_fixture_no_routes() -> None:
    graph = scan_python.scan(TINY_PY_LIB)
    assert graph["routes"] == []


def test_lib_fixture_symbols_index_first_win() -> None:
    graph = scan_python.scan(TINY_PY_LIB)
    # ``greet`` is first defined in ``core.py``; the index should point there
    # even though ``__init__.py`` re-exports it via ``__all__``.
    assert graph["symbols_index"]["greet"] == "src/tinypkg/__init__.py"
    # Internal helper is still indexed (so renamed-symbol tasks resolve).
    assert graph["symbols_index"]["_shout"] == "src/tinypkg/_internal.py"


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------


def test_parse_error_file_is_skipped(tmp_path: Path) -> None:
    (tmp_path / "good.py").write_text("def f():\n    return 1\n")
    (tmp_path / "broken.py").write_text("def )(:\n  this is not python\n")
    graph = scan_python.scan(tmp_path)
    paths = {entry["path"] for entry in graph["files"]}
    assert "good.py" in paths
    assert "broken.py" not in paths
    assert graph.get("parse_failures") == ["broken.py"]


def test_flask_route_decorators_extracted(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(
        'from flask import Flask, Blueprint\n'
        'app = Flask(__name__)\n'
        'bp = Blueprint("bp", __name__)\n'
        '\n'
        '@app.route("/ping")\n'
        'def ping():\n'
        '    return "pong"\n'
        '\n'
        '@bp.route("/users", methods=["GET", "POST"])\n'
        'def users():\n'
        '    return []\n'
    )
    graph = scan_python.scan(tmp_path)
    routes = {(r["path"], r["route"]) for r in graph["routes"]}
    assert ("app.py", "/ping") in routes
    assert ("app.py", "/users") in routes


def test_non_literal_route_is_skipped(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text(
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n"
        "PATH = '/dynamic'\n"
        "@app.get(PATH)\n"
        "def dyn(): return None\n"
        "@app.get('/literal')\n"
        "def lit(): return None\n"
    )
    graph = scan_python.scan(tmp_path)
    routes = [r["route"] for r in graph["routes"]]
    assert "/literal" in routes
    assert "/dynamic" not in routes


def test_walks_skip_venv_and_pycache(tmp_path: Path) -> None:
    (tmp_path / "keep.py").write_text("x = 1\n")
    (tmp_path / ".venv" / "lib").mkdir(parents=True)
    (tmp_path / ".venv" / "lib" / "junk.py").write_text("y = 2\n")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "z.py").write_text("z = 3\n")
    graph = scan_python.scan(tmp_path)
    paths = {entry["path"] for entry in graph["files"]}
    assert "keep.py" in paths
    assert all(not p.startswith(".venv/") for p in paths)
    assert all(not p.startswith("__pycache__/") for p in paths)


def test_write_graph_round_trip(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("def hello(): pass\n")
    out = tmp_path / "graph.json"
    graph = scan_python.write_graph(tmp_path, out)
    on_disk = json.loads(out.read_text())
    assert graph == on_disk
    assert graph["language"] == "python"


def test_hotspot_threshold(tmp_path: Path) -> None:
    # Make a "shared" module imported by 3 sibling modules.
    (tmp_path / "shared.py").write_text("X = 1\n")
    for i in range(3):
        (tmp_path / f"caller_{i}.py").write_text("from shared import X\n")
    graph = scan_python.scan(tmp_path)
    shared = _file_node(graph, "shared.py")
    assert shared["imported_by_count"] == 3
    assert shared["is_hotspot"] is True
    assert "shared.py" in graph["hotspots"]


def test_module_name_helpers() -> None:
    # Exercise the pure helpers directly; they're easy to regress.
    assert scan_python._module_name("a/b/__init__.py", "") == "a.b"
    assert scan_python._module_name("a/b/c.py", "") == "a.b.c"
    assert scan_python._module_name("src/a/b.py", "src") == "a.b"
    assert scan_python._module_name("__init__.py", "") == ""
    # ``from .x`` inside package ``a.b`` -> ``a.b.x``.
    assert scan_python._resolve_relative("a.b", 1, "x") == "a.b.x"
    # ``from ..x`` inside package ``a.b.c`` -> ``a.b.x``.
    assert scan_python._resolve_relative("a.b.c", 2, "x") == "a.b.x"
    # ``from . import y`` inside ``a.b`` -> ``a.b``.
    assert scan_python._resolve_relative("a.b", 1, "") == "a.b"
    # _module_package: regular file vs __init__.py.
    assert scan_python._module_package("a.b.c", is_init=False) == "a.b"
    assert scan_python._module_package("a.b", is_init=True) == "a.b"
    assert scan_python._module_package("c", is_init=False) == ""


def test_cli_entrypoint(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "m.py").write_text("def f(): pass\n")
    out = tmp_path / "graph.json"
    rc = scan_python.main(["--repo", str(tmp_path), "--out", str(out)])
    assert rc == 0
    payload = json.loads(out.read_text())
    assert payload["language"] == "python"
    captured = capsys.readouterr()
    assert "language=python" in captured.out
