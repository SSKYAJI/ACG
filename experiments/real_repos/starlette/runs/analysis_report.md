# ACG run-trace analysis

_Aggregated across 0 run artifact(s)._

## Runs

_no runs found_

## Predictor accuracy (per task, across runs)

_no per-task data_

**Overall: precision=0.00 recall=0.00 F1=0.00**

## Contract enforcement

- Proposal out-of-bounds files across proposal-only runs: **0**
- Planned validator-blocked write events across all runs: **0**
- Post-hoc out-of-bounds files detected in applied/manual diffs: **0**

> All agents stayed within their `allowed_paths` on every observed run. The contract acted as a safety net but did not need to fire — agents behaved within bounds. To stress-test the validator, consider tightening `allowed_paths` so the predicted set is closer to the minimal write set.

## Refinement suggestions

_No refinements suggested — predictor and contract are well-calibrated for the observed runs._
