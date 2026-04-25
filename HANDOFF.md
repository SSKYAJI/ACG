# HANDOFF — Prajit, start here

This file is the first thing to open when you wake up. It tells you exactly what's done, what's left, and what to do in the next three hours, in order. If anything contradicts the megaplan in [`docs/plans/acg-implementation-megaplan-308cc2.md`](docs/plans/acg-implementation-megaplan-308cc2.md), the megaplan wins.

All original Windsurf plan files (megaplan, execution kickoff, decision plan, Cognition review, Cascade-hook stretch, ASUS GX10 setup notes) are mirrored into [`docs/plans/`](docs/plans/) so they travel with the repo — the GX10 has the full strategic context after a single `git pull`.

## Status as of last commit

**Tiers 1–6 acceptance gates: all green.**

| Tier | What's in | Acceptance check |
| --- | --- | --- |
| T1  | `schema/agent_lock.schema.json` + 3 example lockfiles | `python -c 'import jsonschema, json; ...'` passes for both lockfiles |
| T2  | `acg/{schema,llm,solver,predictor,compiler,explain,enforce,report,cli}.py` + 22 pytest tests | `pytest tests/ -v` → 22 passed; `acg compile/explain/validate-write` all behave |
| T3  | `graph_builder/scan.ts` (ts-morph) with tsconfig path-alias resolver | `npm run scan` against `demo-app/` → 16 files, 3 hotspots |
| T4  | `demo-app/` scaffolded via `create-t3-app` + 5 stub files (Sidebar, settings/page, dashboard/profile/projects/health routes); `demo-app/tasks.json` | scan finds ≥6 files and ≥3 hotspots |
| T5  | `acg validate-write` enforcement | `settings → src/server/auth/config.ts` blocks (exit 2); `settings → src/components/Sidebar.tsx` allows (exit 0) |
| T6  | `benchmark/runner.py` + `acg/report.py`; `docs/benchmark.png` rendered | PNG is 1200×600, 5 metric pairs, naive worse on every metric |
| T8  | `README.md`, `HANDOFF.md` (this file), `docs/{CITATIONS,ASUS_DEPLOYMENT,COGNITION_INTEGRATION,ARCHITECTURE}.md` | all internal links resolve |
| T9  | `.gitignore`, `.env.example`, `Makefile` | `make demo` runs end-to-end |

**Open / stretch (deliberately deferred):**

- T7 MCP server wrapper — skipped because the megaplan's `fastmcp>=0.5,<1.0` pin doesn't exist on PyPI (FastMCP versioning jumps from 0.4.1 → 1.0 → 2.x → 3.x). The four primitives still live in `acg/{compiler,predictor,enforce}.py`; a FastMCP wrapper is a thin follow-up that needs a fresh version pin. Documented as roadmap in the README and `docs/COGNITION_INTEGRATION.md`.
- Cascade `pre_write_code` hook — explicitly out of v1 per the strategic plan; the CLI exit-code contract (`acg validate-write` returning 0 / 1 / 2) is the integration point.

## Your first 3 hours (in order)

### 30 min — sanity-check the demo flow on your machine

```bash
git clone <repo>
cd "Cognition Winning project"
make install
cp .env.example .env       # leave ACG_LLM_API_KEY blank for the offline mock; or paste a Groq key
make demo
```

Open `docs/benchmark.png`. Confirm 5 metric pairs and the planned bars are visibly shorter (or 0) on overlapping_writes / manual_merge_steps / wall_time.

If anything fails: ping Shashank with the failing command + last 30 lines of output.

### 60 min — hand-label ground-truth write-sets

Create `demo-app/ground_truth.json` with the shape:

```json
{
  "version": "1.0",
  "tasks": [
    {
      "id": "oauth",
      "writes": ["src/server/auth/config.ts", "prisma/schema.prisma", "src/app/api/auth/[...nextauth]/route.ts"]
    },
    {
      "id": "billing",
      "writes": ["src/app/dashboard/billing/page.tsx", "src/server/stripe.ts", "prisma/schema.prisma", "src/components/Sidebar.tsx"]
    },
    {
      "id": "settings",
      "writes": ["src/app/settings/page.tsx", "src/components/Sidebar.tsx"]
    },
    {
      "id": "tests",
      "writes": ["tests/e2e/checkout.spec.ts"]
    }
  ]
}
```

These are *your* honest call about what each task should touch in the demo-app. Use them to compute precision / recall against the predicted writes in `demo-app/agent_lock.json`. Don't fudge the labels to match the predictions.

### 30 min — verify every citation in `docs/CITATIONS.md`

Open every URL in the file. For each row, mark `verified` (verbatim match), `paraphrased` (close but not identical), or `missing` (URL 404 / quote not findable). Anything that isn't `verified` must have its language softened in the README and demo video script before recording.

### 60 min — write the demo video script (text only)

Copy the 6-segment crash-test from [`docs/plans/acg-execution-kickoff-308cc2.md`](docs/plans/acg-execution-kickoff-308cc2.md) (lines 65–77) into `docs/VIDEO_SCRIPT.md`. Annotate every claim with the source it came from (`agent_lock.json`, `docs/benchmark.png`, citation N). If you can't source a claim, soften or cut it before we hit record.

## What Shashank is working on next

- Final regression sweep (`make test && make lint && make demo`) before recording.
- Devpost copy-paste pass from this README into the LA Hacks Devpost page.
- If buffer remains: re-pin and ship the FastMCP wrapper as a follow-up commit. Skip if the recording deadline is closer than 2 hours.

## Decision authority

You can decide alone:

- File naming inside `docs/`, README phrasing, video script wording.
- Whether to add small wording softeners to honesty claims.
- Whether to drop citations that you can't verify.

Ask Shashank before:

- Changing the lockfile schema or any field name in `agent_lock.json`.
- Changing the demo-app starter (it's already `create-t3-app`).
- Rewording the thesis sentence or the chart title.
- Dropping a sponsor track.

## Daily milestones (from execution kickoff)

| Hour | Gate | Status |
| --- | --- | --- |
| H+1 | ts-morph hello-world graph | done |
| H+3 | First lockfile generated | done |
| H+5 | Hand-labeled ground truth | **your job above** |
| H+8 | Naive vs planned numbers differ | done (chart shows clear separation) |
| H+12 | Hook blocks one bad write | done at the CLI level (`acg validate-write` exit 2); Cascade hook itself is stretch |
| H+18 | Benchmark chart populated | done with simulator numbers; rerun with real Aider/Devin if available |
| H+24 | MCP wrapper exposes 4 tools | not yet (T7 stretch) |
| H+28 | Demo video recorded | your job, after the script lands |
| H+32 | Devpost + GitHub + video uploaded | both of us |
