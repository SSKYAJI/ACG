"""MCP server entrypoint for ACG.

Exposes the four ACG primitives (``analyze_repo``, ``predict_writes``,
``compile_lockfile``, ``validate_writes``) as MCP tools using FastMCP.

Designed to be consumed by:

- Devin Manage Devins (coordinator pre-flight)
- Claude Code / Cursor (write boundary enforcement)
- OpenCode (per-task lock subsystem requested in issue #4278)
"""

from .server import build_server, run_http, run_stdio

__all__ = ["build_server", "run_http", "run_stdio"]
