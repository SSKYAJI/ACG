# Round 3 Orchestrator Summary

## Lane Outcomes

| Lane | Status | Output |
| --- | --- | --- |
| M | succeeded | `experiments/LIMITATIONS_RECALL.md` and `experiments/LANE_M_DONE.md` |
| N | succeeded | `experiments/N_CLEANUP_LOG.md`, `experiments/_archive/round1_round2/`, and `experiments/LANE_N_DONE.md` |
| O1 | succeeded | `experiments/real_repos/manifest.json` and `experiments/real_repos/LANE_O1_DONE.md` |
| O2 | succeeded | Fastify real-repo runs, scores, and `experiments/real_repos/fastify/LANE_O2_DONE.md` |
| O3 | succeeded | Starlette real-repo runs, scores, and `experiments/real_repos/starlette/LANE_O3_DONE.md` |
| O4 | failed | `experiments/real_repos/black/LANE_O4_FAILURE.md` |
| P | succeeded | `experiments/real_repos/VERIFIER_REPORT.md`, `experiments/real_repos/VERIFIER_REPORT.json`, and `experiments/real_repos/LANE_P_DONE.md` |
| Q | succeeded | Append-only `experiments/PAPER_NUMBERS.md` update and `experiments/LANE_Q_DONE.md` |

## Spend And Wall Time

- Total OpenRouter spend: `$0.00082698`, de-duplicated from `cost_usd_total_recorded` in the Fastify and Starlette ground-truth score artifacts.
- Fastify spend: `$0.00039226`.
- Starlette spend: `$0.00043472`.
- Black/O4 spend: `$0.00`; it failed before harness or OpenRouter calls.
- Total wall time: approximately `33m55s`, measured from the first Round 3 output sentinel at `2026-05-06T22:06:57-0700` to Lane Q completion at `2026-05-06T22:40:52-0700`.

## Failed Lanes

- O4/black failed on its first compile command because the installed `acg` CLI rejected `--language python`, expecting one of `auto`, `typescript`, `javascript`, or `java`; no OpenRouter calls were made.

## New Headline Numbers

- Real-repo headline now appended to `experiments/PAPER_NUMBERS.md`: on 2 production repos with 6 historical PRs, mean ground-truth recall was `0.583` and mean ground-truth precision was `0.375`.
- Apply-and-test headline: `0` PRs were verified end-to-end; the completed real-repo lanes skipped apply-and-test, so Lane Q omitted a separate Apply-And-Test Results section.
- Per-repo split: Fastify scored 3 PRs with mean recall `0.278` and mean precision `0.194`; Starlette scored 3 PRs with mean recall `0.889` and mean precision `0.556`.
- Recall limitations were appended as caveats covering saturated recall, filename-hint inflation, agent-proposal-vs-ground-truth measurement, and the descriptive-only N=4 graph-quality result.

## Final Orchestrator Verification

- `./.venv/bin/python -m pytest tests/ -q`: `211 passed, 11 warnings`.
- `grep -c "DONE.md:" experiments/PAPER_NUMBERS.md`: `0`.
- `grep -E "CI: ([0-9.]+)%-\1%" experiments/PAPER_NUMBERS.md`: no matches.
- DONE sentinels found for lanes M, N, O1, O2, O3, P, and Q.
- FAILURE sentinels found only for O4.
