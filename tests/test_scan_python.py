"""Python repo graph scanner tests."""

from __future__ import annotations

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
