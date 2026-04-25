"""MCP wrapper tests.

These tests exercise the :func:`acg.mcp.server.build_server` factory by
patching ``fastmcp`` so the optional dependency is not required for the
CI run. The MCP wrapper is imported lazily inside each test (after the
fake module is injected into ``sys.modules``) so that just importing
this test module never imports ``fastmcp``.
"""

from __future__ import annotations

import importlib
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner


def _install_fake_fastmcp(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[ModuleType, MagicMock, dict[str, Any]]:
    """Inject a minimal stand-in ``fastmcp`` module into ``sys.modules``.

    Returns ``(fake_module, server_instance, registered_tools)`` where
    ``registered_tools`` is keyed by the explicit ``name=`` passed to
    ``server.tool(...)``. ``acg.mcp.server`` is reloaded so the
    ``TYPE_CHECKING``-guarded references resolve against the fake.
    """
    fake_fastmcp = ModuleType("fastmcp")
    server_instance = MagicMock(name="FastMCPInstance")

    registered: dict[str, Any] = {}

    def _tool(name: str | None = None, **_kwargs: Any):
        def _decorator(fn):
            registered[name or fn.__name__] = fn
            return fn

        return _decorator

    server_instance.tool = _tool
    fake_fastmcp.FastMCP = MagicMock(name="FastMCP", return_value=server_instance)
    monkeypatch.setitem(sys.modules, "fastmcp", fake_fastmcp)

    # Drop any cached import so build_server() picks up the fake.
    sys.modules.pop("acg.mcp.server", None)
    sys.modules.pop("acg.mcp", None)
    return fake_fastmcp, server_instance, registered


def test_build_server_returns_fastmcp_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_fastmcp, server_instance, registered = _install_fake_fastmcp(monkeypatch)

    from acg.mcp.server import build_server

    server = build_server()

    assert server is server_instance
    assert fake_fastmcp.FastMCP.call_count == 1
    # All four primitives must be registered under the protocol names.
    assert set(registered) == {
        "analyze_repo",
        "predict_writes",
        "compile_lockfile",
        "validate_writes",
    }


def test_analyze_repo_tool_calls_scan_context_graph(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, _, registered = _install_fake_fastmcp(monkeypatch)

    import acg.mcp.server as server_mod

    captured: dict[str, Any] = {}

    def _fake_scan(repo_root: Path, language: str = "auto", out_path: Path | None = None) -> dict:
        captured["repo_root"] = Path(repo_root)
        captured["language"] = language
        return {"files": [], "language": "typescript"}

    monkeypatch.setattr(server_mod, "scan_context_graph", _fake_scan)
    server_mod.build_server()

    result = registered["analyze_repo"](path=str(tmp_path), language="typescript")

    assert result == {"files": [], "language": "typescript"}
    assert captured["repo_root"] == tmp_path.resolve()
    assert captured["language"] == "typescript"


def test_validate_writes_tool_returns_allowed_true_for_in_path_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, registered = _install_fake_fastmcp(monkeypatch)

    from acg.mcp.server import build_server

    build_server()

    lockfile_payload = {
        "version": "1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "repo": {"root": ".", "languages": ["typescript"]},
        "tasks": [
            {
                "id": "settings",
                "prompt": "Add settings page.",
                "predicted_writes": [
                    {
                        "path": "src/app/settings/page.tsx",
                        "confidence": 0.9,
                        "reason": "explicit",
                    }
                ],
                "allowed_paths": ["src/app/settings/**"],
                "depends_on": [],
            }
        ],
        "execution_plan": {"groups": [{"id": 1, "tasks": ["settings"], "type": "parallel"}]},
        "conflicts_detected": [],
    }

    allowed_result = registered["validate_writes"](
        lockfile=lockfile_payload,
        task_id="settings",
        attempted_path="src/app/settings/page.tsx",
    )
    assert allowed_result["allowed"] is True
    assert allowed_result["reason"] == ""

    blocked_result = registered["validate_writes"](
        lockfile=lockfile_payload,
        task_id="settings",
        attempted_path="src/server/auth/config.ts",
    )
    assert blocked_result["allowed"] is False
    assert "src/server/auth/config.ts" in blocked_result["reason"]


def test_cli_mcp_command_errors_when_extra_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force the lazy ``from acg.mcp import run_stdio`` to raise ImportError.
    saved_mcp = sys.modules.pop("acg.mcp", None)
    saved_server = sys.modules.pop("acg.mcp.server", None)

    class _Boom(ModuleType):
        def __getattr__(self, name: str) -> Any:
            raise ImportError(f"fastmcp not installed (asked for {name})")

    monkeypatch.setitem(sys.modules, "acg.mcp", _Boom("acg.mcp"))

    try:
        # Reload the CLI so the previously-cached ``acg.mcp`` reference is dropped
        # and the next invocation re-runs the lazy import.
        import acg.cli as cli_module

        importlib.reload(cli_module)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["mcp", "--transport", "stdio"])
        assert result.exit_code != 0
        assert "MCP extra not installed" in result.output
        assert "pip install -e '.[mcp]'" in result.output
    finally:
        # Restore real modules so subsequent tests are unaffected.
        sys.modules.pop("acg.mcp", None)
        if saved_mcp is not None:
            sys.modules["acg.mcp"] = saved_mcp
        if saved_server is not None:
            sys.modules["acg.mcp.server"] = saved_server
        import acg.cli as cli_module

        importlib.reload(cli_module)
