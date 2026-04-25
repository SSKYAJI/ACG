# ACG Visualizer

A React Flow v12 (`@xyflow/react`) visualizer for the ACG `agent_lock.json` **and** its companion `run_trace.json` runtime trace.

It reads two JSON files directly:

- `../demo-app/agent_lock.json` — the static lockfile (compiler output).
- `../demo-app/.acg/run_trace.json` — the recorded runtime execution trace (from `acg run` / `make run-gemma`).

…and renders:

- **Task nodes** grouped by `execution_plan.groups` (one column per group), with **live ALLOWED / BLOCKED badges** that increment as the replay reveals each worker's proposals. A red shake fires on every BLOCKED moment.
- **Dependency edges** from `depends_on`.
- **Conflict edges** (red, dashed, animated) from `conflicts_detected`.
- **Orchestrator panel** floating above the canvas: typewriter-renders the orchestrator's `reasoning_content` during the orchestrator phase, then collapses to a pill summarising `wall_s · tokens · approved/rejected`. Click to re-expand.
- **Sidebar** with lockfile metadata, plus (when a task is selected) the live proposal list with allowed / blocked styling, validator reasons on blocked rows, and a `<details>` collapsible showing the worker's raw LLM reply.
- **Toolbar** — phase pill, progress bar, **0.5× / 1× / 2× / 4× speed selector**, and Play / Pause / Reset.

## Prerequisites

You need a lockfile **and** a run trace. From the repo root:

```bash
make install                # one-time
make compile                # generates demo-app/agent_lock.json + .acg/context_graph.json
make run-mock               # generates demo-app/.acg/run_trace.json offline (deterministic)

# Or against the live GX10 servers:
make compile-gemma
make run-gemma              # ~40 s — orchestrator thinks, workers fan out
```

A representative `run_trace.json` is committed to the repo so the viz works the moment you clone — `make run-gemma` overwrites it for your real demo.

## Run

```bash
cd viz
npm install
npm run dev
```

Open http://localhost:5174 (or whichever port Vite picks if 5174 is taken).

## What the replay does

The replay engine in `src/lib/replay.ts` is a pure function of the trace and a single `tSeconds` value (driven by `requestAnimationFrame` in `App.tsx`):

| `t` window     | Phase          | What you see                                                                                                                                                      |
| -------------- | -------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `[0, ~12 s)`   | `orchestrator` | Reasoning text typewriters into the orchestrator panel; canvas idle.                                                                                              |
| `[~12 s, end)` | `groups`       | Each execution group activates in order; workers in a parallel group reveal proposals concurrently; ALLOWED counters tick up; BLOCKED triggers shake + red badge. |
| end            | `done`         | All groups complete; orchestrator panel collapses to a summary pill.                                                                                              |

Visual durations are capped (orch 12 s, group 4 s) so a 30 s real orchestrator pass becomes a watchable beat. The speed selector multiplies that further.

## Layout

- `src/App.tsx` — top-level state (RAF loop, selection, speed).
- `src/lib/replay.ts` — pure replay state machine (`computeReplayState(trace, t)`).
- `src/lib/layout.ts` — column-per-group node layout.
- `src/components/TaskGraph.tsx` — React Flow canvas, nodes, edges.
- `src/components/TaskNode.tsx` — custom task node + live badges + shake.
- `src/components/OrchestratorPanel.tsx` — floating thinking panel.
- `src/components/Sidebar.tsx` — task detail + live proposal drawer + raw-reply collapsible.
- `src/components/Toolbar.tsx` — phase / progress / speed / playback controls.
- `src/types.ts` — TypeScript mirror of `acg.schema` and `acg.runtime` dataclasses.

## Notes

- Vite's `server.fs.allow` is set to the repo root so the JSON imports work in dev.
- HMR re-renders when either `agent_lock.json` or `run_trace.json` changes — re-run `make compile` / `make run-gemma` and the page updates.
- If `run_trace.json` is empty (e.g. a malformed run), the orchestrator panel falls back gracefully and the canvas behaves like the old static lockfile viewer.
