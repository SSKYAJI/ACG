# ACG Visualizer

A React Flow v12 (`@xyflow/react`) visualizer for the ACG `agent_lock.json`.

It reads `../demo-app/agent_lock.json` directly and renders:

- **Task nodes** grouped by `execution_plan.groups` (one column per group).
- **Dependency edges** from `depends_on`.
- **Conflict edges** (red, dashed, animated) from `conflicts_detected`.
- **Sidebar** with lockfile metadata, selected task detail, and a conflicts list.
- **Play execution** control that lights up groups in order — parallel groups light up together, serial groups follow.

## Prerequisites

You need a generated lockfile first. From the repo root:

```bash
make install   # one-time
make compile   # generates demo-app/.acg/context_graph.json + demo-app/agent_lock.json
```

## Run

```bash
cd viz
npm install
npm run dev
```

Open http://localhost:5174.

## Layout

- `src/App.tsx` — top-level state (selection, play/pause/reset).
- `src/components/TaskGraph.tsx` — React Flow canvas, nodes, edges, legend panel.
- `src/components/TaskNode.tsx` — custom task node.
- `src/components/Sidebar.tsx` — task detail / conflict list panel.
- `src/components/Toolbar.tsx` — top bar with metadata + execution controls.
- `src/lib/layout.ts` — column-per-group layout.
- `src/types.ts` — TypeScript mirror of `acg.schema` Pydantic models.

## Notes

- Vite's `server.fs.allow` is set to the repo root so the lockfile import works in dev.
- HMR re-renders when `agent_lock.json` changes — re-run `make compile` and the page updates.
