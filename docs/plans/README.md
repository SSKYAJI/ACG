# Plans archive

Mirror of the Windsurf plan files that drove ACG's design and execution. The originals live in `~/.windsurf/plans/` on Shashank's laptop; they're copied here so the strategic context travels with the repo (notably to the ASUS GX10 deployment).

If you want to edit any of these as a live Windsurf plan again, copy back to `~/.windsurf/plans/<filename>` and Windsurf will pick it up. Otherwise treat them as read-only reference docs.

## Index

### ACG core

| File | What it is |
| --- | --- |
| [`agent-context-graph-decision-plan-308cc2.md`](agent-context-graph-decision-plan-308cc2.md) | Original strategic decision plan: thesis, rubric mapping, sponsor narratives, honesty box. |
| [`acg-implementation-megaplan-308cc2.md`](acg-implementation-megaplan-308cc2.md) | Tier-by-tier implementation spec the build was executed against (T1 schema → T9 misc). When in doubt, the megaplan wins. |
| [`acg-execution-kickoff-308cc2.md`](acg-execution-kickoff-308cc2.md) | First-day execution kickoff: hour-by-hour milestones, demo crash-test segments, Devpost talking points. |
| [`acg-cognition-review-358359.md`](acg-cognition-review-358359.md) | Cognition-track-specific rubric review: what would make this a 1st-place submission. |
| [`cascade-hook-stretch-308cc2.md`](cascade-hook-stretch-308cc2.md) | Stretch plan for the Cascade `pre_write_code` runtime hook. Out of v1; CLI exit-code contract is the integration point. |

### ASUS GX10 setup

| File | What it is |
| --- | --- |
| [`asus-gx10-headless-usbc-9d5e3d.md`](asus-gx10-headless-usbc-9d5e3d.md) | Headless USB-C connection setup for the GX10 (no monitor / keyboard required). |
| [`asus-gx10-wifi-setup-9d5e3d.md`](asus-gx10-wifi-setup-9d5e3d.md) | Wi-Fi configuration walkthrough for the GX10. |

## Cross-references in the repo

The following files explicitly point into this directory:

- [`HANDOFF.md`](../../HANDOFF.md) — Prajit's first-3-hours read; references the megaplan + execution kickoff.
- [`docs/ASUS_DEPLOYMENT.md`](../ASUS_DEPLOYMENT.md) — operational guide for vLLM on GX10.
- [`docs/COGNITION_INTEGRATION.md`](../COGNITION_INTEGRATION.md) — sponsor narrative; aligned with the cognition-review plan.
