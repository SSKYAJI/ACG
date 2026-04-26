# ACG MCP Server

ACG ships an MCP server that exposes its four core primitives as
network-callable tools. Designed for consumption by Devin Manage Devins,
Claude Code, Cursor, and OpenCode.

## Install

```bash
pip install -e '.[mcp]'
```

## Run

```bash
acg mcp                    # stdio transport, blocks until host disconnects
```

Wire as a child process under your MCP host. With Devin's MCP config:

```json
{
  "mcpServers": {
    "acg": {
      "command": "acg",
      "args": ["mcp"]
    }
  }
}
```

## Tools

| Tool | Inputs | Output |
| --- | --- | --- |
| `analyze_repo` | `path` (str), `language` (str, default `auto`) | normalized context-graph dict |
| `predict_writes` | `task` (dict), `repo_path` (str), `repo_graph` (dict, optional) | list of `{path, confidence, reason}` |
| `compile_lockfile` | `repo_path` (str), `tasks` (TasksInput dict), `language` (str, default `auto`) | full `agent_lock.json` dict |
| `validate_writes` | `lockfile` (dict), `task_id` (str), `attempted_path` (str) | `{allowed: bool, reason: str}` |

## Worked example: Devin coordinator pre-flight

```python
graph = await mcp.call("acg", "analyze_repo", {"path": "/repo"})
lock = await mcp.call("acg", "compile_lockfile", {
    "repo_path": "/repo",
    "tasks": {"version": "1.0", "tasks": [...]},
})
for group in lock["execution_plan"]["groups"]:
    await asyncio.gather(*[spawn_child(task_id) for task_id in group["tasks"]])
    for task_id in group["tasks"]:
        for attempted_path in child_writes[task_id]:
            verdict = await mcp.call("acg", "validate_writes", {
                "lockfile": lock,
                "task_id": task_id,
                "attempted_path": attempted_path,
            })
            if not verdict["allowed"]:
                rollback(task_id, attempted_path, verdict["reason"])
```

## Limitations

- `analyze_repo` writes to `<repo>/.acg/context_graph.json`. Mount the
  repo writable.
- `compile_lockfile` requires `ACG_LLM_*` environment variables set
  inside the MCP server process; configure them via your host's
  per-server env block. With no key (and no `ACG_MOCK_LLM=1`), the
  client falls back to a deterministic offline mock — usable for smoke
  tests but not for production planning.
- TypeScript repos require `node` + `npm` on PATH (the graph builder
  shells out to `graph_builder/scan.ts`). Java repos use the in-process
  tree-sitter scanner and have no extra runtime dependency.
