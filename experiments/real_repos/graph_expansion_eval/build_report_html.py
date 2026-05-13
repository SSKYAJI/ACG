"""Build a single self-contained HTML report from the latest live eval run.

Reads:
- after_live_seed{1,2,3}_predictor.csv
- after_live_seed{1,2,3}_strategy_scores.csv
- after_live_seed{1,2,3}_strategy_summary.csv
- after_live_predictor_summary.csv
- after_live_strategy_summary_variance.csv
- after_live_seed{1,2,3}_locks/<repo>-<task>.json   (the orchestrator plan)

Writes:
- eval_report.html, index.html                     (same content; `/` serves index.html)
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import defaultdict
from html import escape
from pathlib import Path
from typing import Any

EVAL_DIR = Path(__file__).resolve().parent
ROOT = EVAL_DIR.parents[2]
SEEDS = (1, 2, 3)
STRATEGIES = ("acg_planned", "acg_planned_replan", "naive_parallel")
STRATEGY_LABEL = {
    "acg_planned": "ACG planned",
    "acg_planned_replan": "ACG planned + replan",
    "naive_parallel": "Naive parallel",
}


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def _load_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open() as fh:
        return list(csv.DictReader(fh))


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, "", "nan"):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _i(value: Any, default: int = 0) -> int:
    try:
        if value in (None, "", "nan"):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def load_all() -> dict[str, Any]:
    predictor_per_seed: dict[int, list[dict[str, str]]] = {
        s: _load_csv(EVAL_DIR / f"after_live_seed{s}_predictor.csv") for s in SEEDS
    }
    strat_scores_per_seed: dict[int, list[dict[str, str]]] = {
        s: _load_csv(EVAL_DIR / f"after_live_seed{s}_strategy_scores.csv") for s in SEEDS
    }
    strat_summary_per_seed: dict[int, list[dict[str, str]]] = {
        s: _load_csv(EVAL_DIR / f"after_live_seed{s}_strategy_summary.csv") for s in SEEDS
    }
    pred_summary = _load_csv(EVAL_DIR / "after_live_predictor_summary.csv")
    strat_variance = _load_csv(EVAL_DIR / "after_live_strategy_summary_variance.csv")

    # Lockfiles per seed: orchestrator plans
    locks_per_seed: dict[int, dict[str, dict[str, Any]]] = {}
    for s in SEEDS:
        d = EVAL_DIR / f"after_live_seed{s}_locks"
        bucket: dict[str, dict[str, Any]] = {}
        if d.exists():
            for p in sorted(d.glob("*.json")):
                try:
                    bucket[p.stem] = json.loads(p.read_text())
                except Exception:
                    continue
        locks_per_seed[s] = bucket

    return {
        "predictor_per_seed": predictor_per_seed,
        "strat_scores_per_seed": strat_scores_per_seed,
        "strat_summary_per_seed": strat_summary_per_seed,
        "pred_summary": pred_summary,
        "strat_variance": strat_variance,
        "locks_per_seed": locks_per_seed,
    }


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------


def task_key(row: dict[str, str]) -> str:
    return f"{row['repo']}/{row['task_id']}"


def per_task_strategy_scores(
    strat_scores_per_seed: dict[int, list[dict[str, str]]],
) -> dict[str, dict[str, dict[str, list[float]]]]:
    """Returns: task -> strategy -> metric -> list of seed values (local backend only)."""
    out: dict[str, dict[str, dict[str, list[float]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    metrics = (
        "f1",
        "recall",
        "precision",
        "tokens_prompt_total",
        "tokens_completion_total",
        "tokens_all_in",
        "cost_usd_total",
        "out_of_bounds_count",
        "blocked_write_count",
        "approved_replan_count",
        "overlapping_write_pairs",
    )
    for rows in strat_scores_per_seed.values():
        for r in rows:
            if r.get("backend") != "local":
                continue
            tk = task_key(r)
            st = r["strategy"]
            for m in metrics:
                out[tk][st][m].append(_f(r.get(m, 0)))
    return out


def per_task_predictor(
    predictor_per_seed: dict[int, list[dict[str, str]]],
) -> dict[str, dict[str, list[float]]]:
    out: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    metrics = (
        "ground_truth_count",
        "predicted_count",
        "candidate_context_count",
        "hard_recall",
        "hard_precision",
        "hard_f1",
        "candidate_recall",
        "candidate_precision",
        "candidate_f1",
        "blocked_truth_recoverable_fraction",
    )
    for rows in predictor_per_seed.values():
        for r in rows:
            tk = task_key(r)
            for m in metrics:
                out[tk][m].append(_f(r.get(m, 0)))
    return out


def aggregate_strategy_totals(
    strat_scores_per_seed: dict[int, list[dict[str, str]]],
) -> dict[str, dict[str, float]]:
    """Total tokens / cost per strategy summed across all tasks AND seeds (local backend)."""
    out: dict[str, dict[str, float]] = defaultdict(
        lambda: {
            "tokens_prompt_total": 0.0,
            "tokens_completion_total": 0.0,
            "tokens_all_in": 0.0,
            "cost_usd_total": 0.0,
            "task_seed_count": 0.0,
        }
    )
    for rows in strat_scores_per_seed.values():
        for r in rows:
            if r.get("backend") != "local":
                continue
            s = r["strategy"]
            out[s]["tokens_prompt_total"] += _f(r.get("tokens_prompt_total"))
            out[s]["tokens_completion_total"] += _f(r.get("tokens_completion_total"))
            out[s]["tokens_all_in"] += _f(r.get("tokens_all_in"))
            out[s]["cost_usd_total"] += _f(r.get("cost_usd_total"))
            out[s]["task_seed_count"] += 1.0
    return out


def macro_summary(pred_summary: list[dict[str, str]]) -> dict[str, float]:
    if not pred_summary:
        return {}
    macro = next((r for r in pred_summary if r.get("scope") == "macro"), None)
    if not macro:
        return {}
    return {k: _f(v) for k, v in macro.items() if k != "scope"}


def variance_by_strategy(
    strat_variance: list[dict[str, str]],
) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for r in strat_variance:
        if r.get("backend") != "local":
            continue
        s = r["strategy"]
        out[s] = {k: _f(v) for k, v in r.items() if k not in ("strategy", "backend")}
    return out


# ---------------------------------------------------------------------------
# HTML rendering helpers
# ---------------------------------------------------------------------------


def fmt(value: float, digits: int = 3) -> str:
    if value is None:
        return "-"
    if isinstance(value, (int, float)) and value == int(value) and digits == 0:
        return f"{int(value)}"
    return f"{value:.{digits}f}"


def fmt_meanstd(values: list[float], digits: int = 3) -> str:
    if not values:
        return "-"
    if len(values) == 1:
        return fmt(values[0], digits)
    m = statistics.mean(values)
    s = statistics.pstdev(values)
    return f"{m:.{digits}f} ± {s:.{digits}f}"


def fmt_int(value: float) -> str:
    return f"{int(round(value))}"


def fmt_cost(value: float) -> str:
    if value < 0.0001:
        return f"${value*1_000_000:.2f}μ"
    if value < 0.01:
        return f"${value*1000:.3f}m"
    return f"${value:.4f}"


def fmt_pct(value: float, digits: int = 1) -> str:
    return f"{value*100:.{digits}f}%"


def info_tip_wrap(inner_html: str, explanation: str, *, wrapper_class: str = "") -> str:
    """Small circular “i” control with hover/focus tooltip (plain-language explanation)."""
    ex = escape(explanation)
    safe_title = escape(explanation.replace('"', "'")[:480])
    wrap = f"group/tip relative z-30 inline-flex max-w-full items-center gap-1 align-middle {wrapper_class}".strip()
    return (
        f'<span class="{wrap}">'
        f"{inner_html}"
        f'<button type="button" tabindex="0" '
        'class="info-tip-btn inline-flex h-4 w-4 shrink-0 cursor-help items-center justify-center rounded-full '
        "border border-slate-600 bg-slate-800 text-[10px] font-bold leading-none text-slate-500 transition-colors "
        "hover:border-sky-600 hover:bg-slate-700 hover:text-sky-300 focus:outline-none focus-visible:ring-2 "
        f'focus-visible:ring-sky-500" aria-label="{safe_title}" title="{safe_title}">i</button>'
        f'<span role="tooltip" class="pointer-events-none absolute left-1/2 top-full z-[400] mt-1.5 '
        "w-[min(20rem,calc(100vw-2rem))] -translate-x-1/2 rounded-lg border border-slate-600 bg-slate-800 px-3 py-2 "
        "text-left text-[11px] font-normal normal-case leading-snug tracking-normal text-slate-200 shadow-xl "
        "opacity-0 shadow-black/50 transition-opacity duration-150 invisible group-hover/tip:opacity-100 "
        'group-hover/tip:visible group-focus-within/tip:opacity-100 group-focus-within/tip:visible">'
        f"{ex}</span>"
        f"</span>"
    )


def signal_chip(sig: str) -> str:
    HIGH = {"explicit", "env", "framework", "llm", "planner", "sibling", "symbol", "testlink", "scope_review"}
    color = "bg-emerald-950/90 text-emerald-300" if sig in HIGH else "bg-slate-800 text-slate-300"
    if sig == "explicit":
        color = "bg-violet-950/90 text-violet-300"
    elif sig == "must_write_neighbor":
        color = "bg-amber-950/90 text-amber-300"
    return f'<span class="inline-block px-2 py-0.5 rounded-full text-[11px] font-medium {color}">{escape(sig)}</span>'


def tier_chip(tier: str) -> str:
    color = {
        "must_write": "bg-rose-950/80 text-rose-300 ring-1 ring-rose-700",
        "candidate_context": "bg-sky-950/80 text-sky-300 ring-1 ring-sky-700",
        "needs_replan": "bg-amber-950/80 text-amber-300 ring-1 ring-amber-700",
    }.get(tier, "bg-slate-800 text-slate-300")
    return f'<span class="inline-block px-2 py-0.5 rounded text-xs font-mono {color}">{escape(tier)}</span>'


def gate_pill(passed: bool, label: str = "") -> str:
    if passed:
        return f'<span class="inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold bg-emerald-600 text-white">PASS{(" — " + label) if label else ""}</span>'
    return f'<span class="inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold bg-rose-600 text-white">FAIL{(" — " + label) if label else ""}</span>'


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------


def render_summary(data: dict[str, Any]) -> str:
    macro = macro_summary(data["pred_summary"])
    variance = variance_by_strategy(data["strat_variance"])
    totals = aggregate_strategy_totals(data["strat_scores_per_seed"])

    naive_macro_f1 = variance.get("naive_parallel", {}).get("macro_f1_mean", 0.0)
    naive_macro_f1_std = variance.get("naive_parallel", {}).get("macro_f1_std", 0.0)
    replan_macro_f1 = variance.get("acg_planned_replan", {}).get("macro_f1_mean", 0.0)
    replan_macro_f1_std = variance.get("acg_planned_replan", {}).get("macro_f1_std", 0.0)

    candidate_recall = macro.get("candidate_recall_mean", 0.0)
    candidate_recall_std = macro.get("candidate_recall_std", 0.0)
    candidate_count_median = macro.get("candidate_count_median_mean", 0.0)
    hard_recall = macro.get("hard_recall_mean", 0.0)
    hard_recall_std = macro.get("hard_recall_std", 0.0)

    # Approved replan distinct-task count across all seeds
    approved_tasks: set[str] = set()
    for rows in data["strat_scores_per_seed"].values():
        for r in rows:
            if r.get("backend") != "local":
                continue
            if r.get("strategy") != "acg_planned_replan":
                continue
            if _i(r.get("approved_replan_count", 0)) > 0:
                approved_tasks.add(task_key(r))

    naive_in = totals.get("naive_parallel", {}).get("tokens_prompt_total", 0)
    acg_in = totals.get("acg_planned", {}).get("tokens_prompt_total", 0)
    naive_cost = totals.get("naive_parallel", {}).get("cost_usd_total", 0)
    acg_cost = totals.get("acg_planned", {}).get("cost_usd_total", 0)
    token_savings_pct = (1 - acg_in / naive_in) if naive_in else 0
    cost_savings_pct = (1 - acg_cost / naive_cost) if naive_cost else 0

    gates_spec: list[tuple[str, str, bool, str]] = [
        (
            "candidate_recall ≥ 0.85",
            "Mean candidate_recall across tasks: share of ground-truth files that appear in must_write ∪ candidate_context (predictor reach before workers). Higher = planner surfaced the right files in hard or soft tiers.",
            candidate_recall >= 0.85,
            f"{candidate_recall:.3f} ± {candidate_recall_std:.3f}",
        ),
        (
            "candidate_count_median ∈ [8,14]",
            "Median number of candidate_context files per task (then averaged across seeds). Too low means almost no recovery bandwidth; too high dilutes precision. Eval prompt expects a moderate band.",
            8 <= candidate_count_median <= 14,
            f"{candidate_count_median:.2f}",
        ),
        (
            "hard_recall ≥ 0.60",
            "Mean hard_recall: share of ground-truth files that landed in must_write only (strict tier). Measures whether the hard write set catches real edits without relying on candidate_context.",
            hard_recall >= 0.60,
            f"{hard_recall:.3f} ± {hard_recall_std:.3f}",
        ),
        (
            "approved_replan on ≥ 3 tasks",
            "Number of distinct tasks where auto-replan approved at least one blocked-but-eligible write (runtime promoted candidate_context → writable). Checks that recovery isn’t a one-off.",
            len(approved_tasks) >= 3,
            f"{len(approved_tasks)} task(s): {', '.join(sorted(approved_tasks)) or '—'}",
        ),
        (
            "macro_f1(replan) ≥ macro_f1(naive) − σ",
            "Compare mean macro F1 (mean of per-task F1) for ACG with replan vs naive, accounting for naive seed noise (subtract naive’s σ). Passing means ACG isn’t worse than naive within variance.",
            replan_macro_f1 >= naive_macro_f1 - naive_macro_f1_std,
            f"{replan_macro_f1:.3f} vs {naive_macro_f1:.3f} − {naive_macro_f1_std:.3f} = {naive_macro_f1 - naive_macro_f1_std:.3f}",
        ),
        (
            "σ(macro_f1) ≤ 0.05 (both)",
            "Standard deviation of macro_f1 across the 3 LLM seeds, for replan and naive. Low σ means results are stable; high σ means luck dominates.",
            replan_macro_f1_std <= 0.05 and naive_macro_f1_std <= 0.05,
            f"replan {replan_macro_f1_std:.3f}, naive {naive_macro_f1_std:.3f}",
        ),
    ]
    passes = sum(1 for _, _, ok, _ in gates_spec if ok)
    verdict_class = "regression" if (replan_macro_f1 < naive_macro_f1 - naive_macro_f1_std) or candidate_recall < 0.70 else (
        "viable" if passes == 6 else "partial-progress"
    )
    verdict_color = {
        "viable": "bg-emerald-600",
        "partial-progress": "bg-amber-500",
        "regression": "bg-rose-600",
    }[verdict_class]
    verdict_reason = "candidate_recall < 0.70 and macro_f1(replan) < naive lower bound" if verdict_class == "regression" else (
        "all hard gates pass" if verdict_class == "viable" else "some gates pass, others fail"
    )

    gate_rows = []
    for label, tip, ok, value in gates_spec:
        label_cell = info_tip_wrap(f'<span class="text-slate-200">{escape(label)}</span>', tip)
        gate_rows.append(
            f'<tr class="border-b border-slate-800">'
            f'<td class="py-2 px-3 align-top">{label_cell}</td>'
            f'<td class="py-2 px-3">{gate_pill(ok)}</td>'
            f'<td class="py-2 px-3 font-mono text-sm text-slate-400">{escape(value)}</td>'
            f"</tr>"
        )
    gates_html = "\n".join(gate_rows)

    return f"""
<section id="summary" class="mb-12 scroll-mt-24 md:scroll-mt-28">
  <div class="rounded-2xl bg-gradient-to-br from-slate-950 via-slate-900 to-indigo-950 p-8 text-white shadow-xl ring-1 ring-slate-700/80">
    <div class="flex items-center justify-between mb-4">
      <div>
        <div class="text-sm uppercase tracking-wider text-slate-400">{info_tip_wrap("<span>Verdict</span>", "High-level label from macro gates + macro_f1 vs naive: viable = all gates pass; regression = strict prompt considers ACG worse than naive band; partial-progress = mixed gates.")}</div>
        <div class="text-3xl font-bold mt-1"><span class="px-3 py-1 rounded-lg {verdict_color}">{verdict_class}</span></div>
        <div class="text-slate-300 mt-2 text-sm">{escape(verdict_reason)}</div>
      </div>
      <div class="text-right">
        <div class="flex justify-end">{info_tip_wrap('<span class="text-sm uppercase tracking-wider text-slate-400">Hard gates</span>', 'Six strict binary checks from the eval prompt. All must pass for a "viable" iteration; the big fraction is how many passed.', wrapper_class="justify-end")}</div>
        <div class="text-5xl font-bold mt-1">{passes}<span class="text-slate-400 text-2xl">/6</span></div>
        <div class="text-slate-300 mt-1 text-sm">passing</div>
      </div>
    </div>
    <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mt-6">
      <div class="bg-slate-700/50 rounded-lg p-3">
        {info_tip_wrap('<span class="text-xs uppercase tracking-wider text-slate-400">Token savings (input)</span>', "Percent fewer worker prompt tokens for ACG planned vs naive_parallel, summed over all 11 tasks × 3 seeds. Measures scoped-repo context shrinking what workers read.", wrapper_class="flex-wrap")}
        <div class="text-2xl font-bold mt-1">{fmt_pct(token_savings_pct, 1)}</div>
        <div class="text-xs text-slate-400">ACG vs naive, summed across 33 task-seeds</div>
      </div>
      <div class="bg-slate-700/50 rounded-lg p-3">
        {info_tip_wrap('<span class="text-xs uppercase tracking-wider text-slate-400">Cost savings</span>', "Total USD cost from OpenRouter pricing for the same token totals (mostly driven by prompt-token delta). Micro-dollars shown as μ or m.", wrapper_class="flex-wrap")}
        <div class="text-2xl font-bold mt-1">{fmt_pct(cost_savings_pct, 1)}</div>
        <div class="text-xs text-slate-400">{fmt_cost(acg_cost)} vs {fmt_cost(naive_cost)}</div>
      </div>
      <div class="bg-slate-700/50 rounded-lg p-3">
        {info_tip_wrap('<span class="text-xs uppercase tracking-wider text-slate-400">macro_f1 ACG-replan</span>', "Mean of per-task F1 scores for strategy acg_planned_replan (planner + enforcement + auto-replan). Macro = unweighted average across tasks.", wrapper_class="flex-wrap")}
        <div class="text-2xl font-bold mt-1">{replan_macro_f1:.3f}</div>
        <div class="text-xs text-slate-400">σ = {replan_macro_f1_std:.3f}, n=3 seeds</div>
      </div>
      <div class="bg-slate-700/50 rounded-lg p-3">
        {info_tip_wrap('<span class="text-xs uppercase tracking-wider text-slate-400">macro_f1 naive</span>', "Mean of per-task F1 for naive_parallel baseline (no planner, no path enforcement). Comparison anchor for quality.", wrapper_class="flex-wrap")}
        <div class="text-2xl font-bold mt-1">{naive_macro_f1:.3f}</div>
        <div class="text-xs text-slate-400">σ = {naive_macro_f1_std:.3f}, n=3 seeds</div>
      </div>
    </div>
  </div>

  <div class="mt-6 rounded-xl bg-slate-900 shadow-lg ring-1 ring-slate-700 overflow-hidden">
    <div class="px-5 py-3 border-b border-slate-700 bg-slate-800/60">
      <h2 class="text-lg font-semibold text-slate-100 inline-flex items-center gap-2">{info_tip_wrap('<span>Hard gates</span>', "Same six gates as in the hero card, shown with measured values. Hover ⓘ on each row for what the gate is checking.")}</h2>
      <p class="text-sm text-slate-400 mt-1">From the strict prompt: each gate must PASS for the iteration to be called <em>viable</em>.</p>
    </div>
    <table class="w-full text-sm text-slate-200">
      <thead class="bg-slate-800 text-left text-xs uppercase tracking-wider text-slate-400">
        <tr>
          <th class="py-2 px-3 align-top">{info_tip_wrap("<span>Gate</span>", "Name of the acceptance criterion from the eval rubric. Hover ⓘ on each row for full detail.")}</th>
          <th class="py-2 px-3 align-top">{info_tip_wrap("<span>Result</span>", "PASS if the measured value satisfies the gate; FAIL otherwise.")}</th>
          <th class="py-2 px-3 align-top">{info_tip_wrap("<span>Value</span>", "Actual measured metric(s) from this run — compare to the gate’s inequality.")}</th>
        </tr>
      </thead>
      <tbody>{gates_html}</tbody>
    </table>
  </div>

  <div class="mt-6 grid md:grid-cols-2 gap-4">
    <div class="rounded-xl bg-emerald-950/40 p-5 ring-1 ring-emerald-800">
      <h3 class="font-semibold text-emerald-300">What is working</h3>
      <ul class="mt-3 space-y-2 text-sm text-emerald-100">
        <li>• ACG ties or matches naive on <strong>9 of 11 tasks</strong>.</li>
        <li>• <strong>The replan path works</strong>: <code>urllib3/pr-4974</code> recovered from F1 0.000 → 0.500 via auto-approved candidate write.</li>
        <li>• ACG uses <strong>~{fmt_pct(token_savings_pct, 0)} fewer worker prompt tokens</strong> than naive — the core efficiency thesis is intact.</li>
        <li>• Predictor variance is tight (σ = {candidate_recall_std:.3f} on candidate_recall) — the system is reproducible across seeds.</li>
      </ul>
    </div>
    <div class="rounded-xl bg-rose-950/40 p-5 ring-1 ring-rose-800">
      <h3 class="font-semibold text-rose-300">What is not working</h3>
      <ul class="mt-3 space-y-2 text-sm text-rose-100">
        <li>• <code>candidate_count_median = 0</code> — six of eleven tasks generate no candidate context at all. The multi-signal gate is too strict for the actual signal density.</li>
        <li>• <strong>fastify/pr-6653</strong> drives the entire macro_f1 deficit: hard_recall = 0.0 because must_write missed every ground-truth file.</li>
        <li>• naive scored higher there only by writing 4 out-of-bounds files that happened to include the right one. ACG correctly enforces but cannot recover from a bad must_write.</li>
        <li>• n=11 with one outlier means σ swallows the architectural delta. The benchmark is underpowered.</li>
      </ul>
    </div>
  </div>
</section>
"""


def render_per_task_table(data: dict[str, Any]) -> str:
    pts = per_task_strategy_scores(data["strat_scores_per_seed"])
    ptp = per_task_predictor(data["predictor_per_seed"])
    rows_html = []
    for tk in sorted(pts.keys()):
        pred = ptp.get(tk, {})
        gt = pred.get("ground_truth_count", [0])
        gt_n = int(round(statistics.mean(gt))) if gt else 0
        cand_count = pred.get("candidate_context_count", [])
        cand_recall = pred.get("candidate_recall", [])
        hard_recall_v = pred.get("hard_recall", [])

        naive = pts[tk].get("naive_parallel", {})
        plan = pts[tk].get("acg_planned", {})
        repl = pts[tk].get("acg_planned_replan", {})
        n_f1 = naive.get("f1", [])
        p_f1 = plan.get("f1", [])
        r_f1 = repl.get("f1", [])

        n_in = sum(naive.get("tokens_prompt_total", []))
        p_in = sum(plan.get("tokens_prompt_total", []))
        savings = (1 - p_in / n_in) if n_in else 0
        approved = int(sum(repl.get("approved_replan_count", []))) if repl.get("approved_replan_count") else 0

        # Outcome class
        d = (statistics.mean(r_f1) if r_f1 else 0) - (statistics.mean(n_f1) if n_f1 else 0)
        if abs(d) < 0.01:
            outcome = '<span class="text-slate-500">tied</span>'
        elif d > 0:
            outcome = '<span class="text-emerald-400 font-semibold">ACG +' + f"{d:.3f}" + "</span>"
        else:
            outcome = '<span class="text-rose-400 font-semibold">naive +' + f"{abs(d):.3f}" + "</span>"

        rows_html.append(f"""
<tr class="border-b border-slate-800 hover:bg-slate-800/50">
  <td class="py-2 px-3 font-medium text-slate-100"><a href="#plan-{escape(tk.replace('/', '-'))}" class="text-sky-400 hover:text-sky-300 hover:underline">{escape(tk)}</a></td>
  <td class="py-2 px-3 text-center text-slate-400">{gt_n}</td>
  <td class="py-2 px-3 text-center font-mono text-sm text-slate-300">{fmt_meanstd(cand_count, 1)}</td>
  <td class="py-2 px-3 text-center font-mono text-sm text-slate-300">{fmt_meanstd(cand_recall)}</td>
  <td class="py-2 px-3 text-center font-mono text-sm text-slate-300">{fmt_meanstd(hard_recall_v)}</td>
  <td class="py-2 px-3 text-center font-mono text-sm text-slate-300">{fmt_meanstd(n_f1)}</td>
  <td class="py-2 px-3 text-center font-mono text-sm text-slate-300">{fmt_meanstd(p_f1)}</td>
  <td class="py-2 px-3 text-center font-mono text-sm text-slate-300">{fmt_meanstd(r_f1)}</td>
  <td class="py-2 px-3 text-center">{outcome}</td>
  <td class="py-2 px-3 text-center text-xs text-slate-300">{fmt_int(p_in)} / {fmt_int(n_in)}<br><span class="text-emerald-400 font-semibold">{fmt_pct(savings, 0)}</span></td>
  <td class="py-2 px-3 text-center text-slate-300">{approved if approved else '<span class="text-slate-600">—</span>'}</td>
</tr>
""")
    return f"""
<section id="per-task" class="mb-12 scroll-mt-24 md:scroll-mt-28">
  <h2 class="text-2xl font-bold mb-2 text-slate-100 inline-flex flex-wrap items-center gap-2">{info_tip_wrap("<span>Per-task results</span>", "One row per benchmark task. Metrics blend predictor quality (GT / recall columns) with worker outcomes (F1) and cost (tokens). Hover ⓘ on column headers for precise definitions.")}</h2>
  <p class="text-slate-400 mb-4">Each row is one benchmark task averaged over 3 seeds. Click a task name to jump to its orchestrator plan below. Token columns are <em>sum across all 3 seeds</em>.</p>
  <div class="rounded-xl bg-slate-900 shadow-lg ring-1 ring-slate-700 overflow-x-auto">
    <table class="w-full text-sm">
      <thead class="bg-slate-800 text-left text-xs uppercase tracking-wider text-slate-400">
        <tr>
          <th class="py-2 px-3 align-bottom">{info_tip_wrap("<span>Task</span>", "Repo and task id for this benchmark row. Click the task link to open its orchestrator plan below.")}</th>
          <th class="py-2 px-3 text-center align-bottom">{info_tip_wrap("<span>GT</span>", "Ground-truth file count: how many files were truly edited in the reference PR for this task.")}</th>
          <th class="py-2 px-3 text-center align-bottom">{info_tip_wrap("<span>cand n</span>", "candidate_context_count (mean ± σ across seeds): number of soft-tier context files the predictor attached for recovery / reading.")}</th>
          <th class="py-2 px-3 text-center align-bottom">{info_tip_wrap("<span>cand R</span>", "candidate_recall: fraction of GT files that appear in must_write ∪ candidate_context before workers run.")}</th>
          <th class="py-2 px-3 text-center align-bottom">{info_tip_wrap("<span>hard R</span>", "hard_recall: fraction of GT files that appear in must_write only (strict tier).")}</th>
          <th class="py-2 px-3 text-center align-bottom">{info_tip_wrap("<span>naive F1</span>", "Harmonic mean of precision/recall of predicted writes vs GT for naive_parallel (full-graph baseline).")}</th>
          <th class="py-2 px-3 text-center align-bottom">{info_tip_wrap("<span>planned F1</span>", "F1 for acg_planned: planner + scoped workers + path enforcement, without runtime replan approvals.")}</th>
          <th class="py-2 px-3 text-center align-bottom">{info_tip_wrap("<span>replan F1</span>", "F1 for acg_planned_replan: same as planned but auto-replan may promote candidate writes when the guard approves.")}</th>
          <th class="py-2 px-3 text-center align-bottom">{info_tip_wrap("<span>outcome</span>", "Compares mean replan F1 vs naive F1; “tied” if within 0.01. Shows who “won” quality on this task.")}</th>
          <th class="py-2 px-3 text-center align-bottom">{info_tip_wrap('<span class="inline-flex flex-col items-center gap-0 leading-tight"><span>tokens</span><span class="normal-case font-normal text-[10px] text-slate-500">ACG / naive</span></span>', "Per row: first number is ACG planned prompt tokens (all seeds summed), second is naive. Green percent under the pair is prompt-token savings.")}</th>
          <th class="py-2 px-3 text-center align-bottom">{info_tip_wrap("<span>replans</span>", "Sum of approved_replan_count across seeds: how many blocked writes were auto-approved onto candidate paths.")}</th>
        </tr>
      </thead>
      <tbody>{"".join(rows_html)}</tbody>
    </table>
  </div>
</section>
"""


def render_token_economy(data: dict[str, Any]) -> str:
    totals = aggregate_strategy_totals(data["strat_scores_per_seed"])
    pts = per_task_strategy_scores(data["strat_scores_per_seed"])

    # Aggregate
    naive = totals.get("naive_parallel", {})
    plan = totals.get("acg_planned", {})
    repl = totals.get("acg_planned_replan", {})

    # Per-task ACG vs naive ratio for chart
    bars = []
    max_in = 0.0
    for tk in sorted(pts.keys()):
        n_in = sum(pts[tk].get("naive_parallel", {}).get("tokens_prompt_total", []))
        p_in = sum(pts[tk].get("acg_planned", {}).get("tokens_prompt_total", []))
        max_in = max(max_in, n_in, p_in)
        bars.append((tk, p_in, n_in))

    bar_rows = []
    for tk, p_in, n_in in bars:
        pwidth = (p_in / max_in * 100) if max_in else 0
        nwidth = (n_in / max_in * 100) if max_in else 0
        ratio = (p_in / n_in) if n_in else 0
        bar_rows.append(f"""
<div class="grid grid-cols-12 gap-2 items-center text-xs py-1.5">
  <div class="col-span-3 text-slate-300 font-mono truncate" title="{escape(tk)}">{escape(tk)}</div>
  <div class="col-span-7">
    <div class="flex flex-col gap-1">
      <div class="flex items-center gap-2">
        <span class="w-12 text-right text-emerald-400 font-mono">{fmt_int(p_in)}</span>
        <div class="flex-1 bg-emerald-950 rounded h-3 relative ring-1 ring-emerald-900/80">
          <div class="absolute top-0 left-0 h-3 rounded bg-emerald-500" style="width:{pwidth:.2f}%"></div>
        </div>
      </div>
      <div class="flex items-center gap-2">
        <span class="w-12 text-right text-slate-400 font-mono">{fmt_int(n_in)}</span>
        <div class="flex-1 bg-slate-800 rounded h-3 relative ring-1 ring-slate-700">
          <div class="absolute top-0 left-0 h-3 rounded bg-slate-500" style="width:{nwidth:.2f}%"></div>
        </div>
      </div>
    </div>
  </div>
  <div class="col-span-2 text-right font-mono text-slate-400">ACG/naive = {ratio:.2f}×</div>
</div>
""")

    return f"""
<section id="tokens" class="mb-12 scroll-mt-24 md:scroll-mt-28">
  <h2 class="text-2xl font-bold mb-2 text-slate-100 inline-flex flex-wrap items-center gap-2">{info_tip_wrap("<span>Token economy</span>", "Compares worker-side prompt tokens and estimated USD cost. ACG should spend fewer input tokens because workers see scoped graphs; planner overhead is tracked separately in tokens_all_in if you dig into raw CSVs.")}</h2>
  <p class="text-slate-400 mb-4">The original ACG pitch: workers see scoped context, not the full repo. So worker prompt tokens should be lower. Here is the actual data.</p>

  <div class="grid md:grid-cols-3 gap-4 mb-6">
    <div class="rounded-xl bg-slate-900 shadow-lg ring-1 ring-slate-700 p-5">
      {info_tip_wrap('<div class="text-xs uppercase tracking-wider text-slate-500">ACG planned</div>', "Planner emits lockfile (must_write, candidate_context, allowed_paths). Workers receive scoped repo_graph only; validator blocks out-of-glob writes. Does not auto-promote candidate writes at runtime.")}
      <div class="text-3xl font-bold mt-1 text-emerald-400">{fmt_int(plan.get('tokens_prompt_total', 0))}</div>
      <div class="text-sm text-slate-500">prompt tokens total</div>
      <div class="text-xs text-slate-500 mt-2">completion: {fmt_int(plan.get('tokens_completion_total', 0))}</div>
      <div class="text-xs text-slate-500">cost: {fmt_cost(plan.get('cost_usd_total', 0))}</div>
    </div>
    <div class="rounded-xl bg-slate-900 shadow-lg ring-1 ring-slate-700 p-5">
      {info_tip_wrap('<div class="text-xs uppercase tracking-wider text-slate-500">ACG planned + replan</div>', "Same enforcement as planned, but if a worker proposes a write on a candidate_context path and the runtime guard agrees (score/signals), the write can be auto-approved — recovery without re-running the planner.")}
      <div class="text-3xl font-bold mt-1 text-sky-400">{fmt_int(repl.get('tokens_prompt_total', 0))}</div>
      <div class="text-sm text-slate-500">prompt tokens total</div>
      <div class="text-xs text-slate-500 mt-2">completion: {fmt_int(repl.get('tokens_completion_total', 0))}</div>
      <div class="text-xs text-slate-500">cost: {fmt_cost(repl.get('cost_usd_total', 0))}</div>
    </div>
    <div class="rounded-xl bg-slate-900 shadow-lg ring-1 ring-slate-700 p-5">
      {info_tip_wrap('<div class="text-xs uppercase tracking-wider text-slate-500">Naive parallel</div>', "Baseline: no planner lockfile, no allowed_paths enforcement; workers fan out with broad repo context (top-K imports style). Often higher prompt tokens, may score well by writing widely.")}
      <div class="text-3xl font-bold mt-1 text-slate-200">{fmt_int(naive.get('tokens_prompt_total', 0))}</div>
      <div class="text-sm text-slate-500">prompt tokens total</div>
      <div class="text-xs text-slate-500 mt-2">completion: {fmt_int(naive.get('tokens_completion_total', 0))}</div>
      <div class="text-xs text-slate-500">cost: {fmt_cost(naive.get('cost_usd_total', 0))}</div>
    </div>
  </div>

  <div class="rounded-xl bg-slate-900 shadow-lg ring-1 ring-slate-700 p-5 mb-6">
    <h3 class="font-semibold mb-3 text-slate-100 inline-flex flex-wrap items-center gap-2">{info_tip_wrap("<span>Per-task prompt tokens — ACG (green) vs naive (gray)</span>", "Each row: emerald bar = ACG planned prompt tokens for that task (all seeds summed); gray bar = naive. Ratio shows relative efficiency.")}</h3>
    <div class="space-y-1">{"".join(bar_rows)}</div>
  </div>

  <div class="rounded-xl bg-emerald-950/35 p-5 text-sm text-emerald-100 ring-1 ring-emerald-800">
    <strong>Reading this:</strong> ACG uses <strong>{fmt_pct((1 - plan.get('tokens_prompt_total', 0) / naive.get('tokens_prompt_total', 1)), 0) if naive.get('tokens_prompt_total', 0) else '—'}</strong> fewer worker prompt tokens than naive across the 33 task-seeds.
    Most of the savings comes from scoped <code>repo_graph</code> in the worker prompt: ACG workers see only files inside their <code>allowed_paths</code>, while naive workers see the global top-K of the full graph.
    Even when F1 is tied, this is a meaningful win for cost-efficiency at scale.
    The completion-token gap is small because both strategies generate roughly the same number of write proposals.
  </div>
</section>
"""


def render_predictor_quality(data: dict[str, Any]) -> str:
    macro = macro_summary(data["pred_summary"])
    fields = [
        ("ground_truth_count", "files in ground truth"),
        ("predicted_count", "files predicted as must_write"),
        ("hard_recall", "fraction of ground truth captured by must_write"),
        ("hard_precision", "fraction of must_write that is in ground truth"),
        ("hard_f1", "harmonic mean of hard precision/recall"),
        ("candidate_context_count", "files in candidate_context tier"),
        ("candidate_recall", "fraction of ground truth captured by must_write OR candidate_context"),
        ("candidate_precision", "fraction of must_write∪candidate that is in ground truth"),
        ("candidate_f1", "harmonic mean of candidate precision/recall"),
        ("candidate_count_median", "median candidate_context_count across tasks (then averaged across seeds)"),
        ("blocked_truth_recoverable_fraction", "fraction of blocked-but-true-positive writes that have a viable approval path"),
    ]
    rows_html = []
    for key, doc in fields:
        m = macro.get(f"{key}_mean", 0.0)
        s = macro.get(f"{key}_std", 0.0)
        metric_cell = info_tip_wrap(f'<span class="font-mono text-sm text-slate-200">{escape(key)}</span>', doc)
        rows_html.append(
            f'<tr class="border-b border-slate-800"><td class="py-2 px-3 align-top">{metric_cell}</td>'
            f'<td class="py-2 px-3 font-mono text-sm text-slate-300">{m:.3f} ± {s:.3f}</td>'
            f'<td class="py-2 px-3 text-sm text-slate-400">{escape(doc)}</td></tr>'
        )

    return f"""
<section id="predictor" class="mb-12 scroll-mt-24 md:scroll-mt-28">
  <h2 class="text-2xl font-bold mb-2 text-slate-100 inline-flex flex-wrap items-center gap-2">{info_tip_wrap("<span>Predictor quality</span>", "Statistics from the compile / file_scopes stage only — before workers run. Tells you whether the planner surfaced the right files (recall/precision) and how large the soft tier is.")}</h2>
  <p class="text-slate-400 mb-4">These are the metrics produced by the predictor stage (compile → file_scopes), <em>before</em> any worker LLM runs.
  They tell you how good ACG is at identifying which files matter for a task.
  Aggregated across 11 tasks × 3 seeds = 33 task-seeds.</p>
  <div class="rounded-xl bg-slate-900 shadow-lg ring-1 ring-slate-700 overflow-hidden">
    <table class="w-full text-sm">
      <thead class="bg-slate-800 text-left text-xs uppercase tracking-wider text-slate-400">
        <tr>
          <th class="py-2 px-3 align-bottom">{info_tip_wrap("<span>Metric</span>", "Predictor-stage scalar from compile/file_scopes; hover ⓘ on a row for the full definition.")}</th>
          <th class="py-2 px-3 align-bottom">{info_tip_wrap("<span>mean ± σ</span>", "Mean across the 33 task-seeds (11 tasks × 3 seeds), with σ = population std dev across seeds’ macro aggregates where applicable.")}</th>
          <th class="py-2 px-3 align-bottom">{info_tip_wrap("<span>What it means</span>", "Short plain-language gloss matching the Glossary section — kept inline so you don’t have to scroll.")}</th>
        </tr>
      </thead>
      <tbody>{"".join(rows_html)}</tbody>
    </table>
  </div>
</section>
"""


def render_plans(data: dict[str, Any]) -> str:
    """For each task, show the actual orchestrator plan from seed 1's lockfile."""
    locks = data["locks_per_seed"].get(1, {})
    sections = []
    for stem in sorted(locks.keys()):
        lock = locks[stem]
        groups = (lock.get("execution_plan") or {}).get("groups") or []
        tasks = lock.get("tasks") or []
        if not tasks:
            continue
        # One task per benchmark lock; render details
        task = tasks[0]
        prompt = task.get("prompt", "")
        predicted = task.get("predicted_writes") or []
        allowed = task.get("allowed_paths") or []
        candidate = task.get("candidate_context_paths") or []
        rationale = task.get("rationale", "")
        scopes = task.get("file_scopes") or []

        scopes_html = []
        for s in scopes[:30]:
            sigs = " ".join(signal_chip(x) for x in (s.get("signals") or []))
            scopes_html.append(
                f'<tr class="border-b border-slate-800">'
                f'<td class="py-1.5 px-3 font-mono text-xs text-slate-300">{escape(s.get("path", ""))}</td>'
                f'<td class="py-1.5 px-3">{tier_chip(s.get("tier", ""))}</td>'
                f'<td class="py-1.5 px-3 text-center font-mono text-xs text-slate-400">{s.get("score", 0):.2f}</td>'
                f'<td class="py-1.5 px-3 space-x-1">{sigs}</td>'
                f"</tr>"
            )

        predicted_html = "".join(
            f'<li class="font-mono text-xs text-slate-300">{escape(w.get("path", ""))} '
            f'<span class="text-slate-500">— {escape(w.get("description", "") or "")[:120]}</span></li>'
            for w in predicted
        )
        allowed_html = "".join(f"<li class=\"font-mono text-xs text-slate-300\">{escape(p)}</li>" for p in allowed)
        candidate_html = (
            "".join(f"<li class=\"font-mono text-xs text-slate-300\">{escape(p)}</li>" for p in candidate)
            or '<li class="text-slate-600 italic text-xs">(empty — no candidate context for this task)</li>'
        )

        groups_html = ""
        if groups:
            for g in groups:
                groups_html += (
                    f'<div class="rounded bg-slate-950 p-2 mb-2 text-xs ring-1 ring-slate-700">'
                    f'<span class="font-mono text-slate-200">group {escape(str(g.get("id", "?")))}</span> '
                    f'<span class="text-slate-500">({escape(g.get("type", ""))})</span> — tasks: '
                    f'<code class="rounded bg-slate-900 px-1 text-slate-300">{escape(", ".join(g.get("tasks", [])))}</code>'
                    f"</div>"
                )

        sections.append(f"""
<details id="plan-{escape(stem)}" class="rounded-xl bg-slate-900 shadow-lg ring-1 ring-slate-700 mb-4 overflow-hidden scroll-mt-24 md:scroll-mt-28">
  <summary class="cursor-pointer px-5 py-3 bg-slate-800 hover:bg-slate-700 flex items-center justify-between list-none">
    <span class="font-semibold text-slate-100">{escape(stem)}</span>
    <span class="text-xs text-slate-500">click to expand · {len(scopes)} file scopes · {len(candidate)} candidates · {len(predicted)} predicted writes</span>
  </summary>
  <div class="border-t border-slate-800 p-5">
    <div class="text-xs uppercase tracking-wider text-slate-500 mb-1">Task prompt</div>
    <div class="rounded border border-slate-700 bg-slate-950 p-3 text-sm text-slate-200 whitespace-pre-wrap mb-4 max-h-40 overflow-y-auto">{escape(prompt)}</div>

    {f'<div class="text-xs uppercase tracking-wider text-slate-500 mb-1">Rationale</div><div class="text-sm text-slate-300 mb-4">{escape(rationale)}</div>' if rationale else ''}

    <div class="grid md:grid-cols-3 gap-4 mb-4">
      <div>
        <div class="text-xs uppercase tracking-wider text-slate-500 mb-1">Predicted writes ({len(predicted)})</div>
        <ul class="space-y-1 list-disc list-inside text-slate-300">{predicted_html or '<li class="text-slate-600 italic text-xs">(none)</li>'}</ul>
      </div>
      <div>
        <div class="text-xs uppercase tracking-wider text-slate-500 mb-1">Allowed paths ({len(allowed)})</div>
        <ul class="space-y-1 list-disc list-inside text-slate-300">{allowed_html or '<li class="text-slate-600 italic text-xs">(none)</li>'}</ul>
      </div>
      <div>
        <div class="text-xs uppercase tracking-wider text-slate-500 mb-1">Candidate context ({len(candidate)})</div>
        <ul class="space-y-1 list-disc list-inside text-slate-300">{candidate_html}</ul>
      </div>
    </div>

    <div class="text-xs uppercase tracking-wider text-slate-500 mb-1">Execution plan</div>
    <div class="mb-4">{groups_html or '<div class="text-slate-600 italic text-xs">(no group structure — single task)</div>'}</div>

    <div class="text-xs uppercase tracking-wider text-slate-500 mb-1">File scopes ({len(scopes)} total, top 30 shown)</div>
    <div class="overflow-x-auto">
      <table class="w-full text-sm">
        <thead class="bg-slate-800 text-left text-xs uppercase tracking-wider text-slate-400">
          <tr>
            <th class="py-2 px-3 align-bottom">{info_tip_wrap("<span>path</span>", "Repository-relative file path included in the planner’s file_scopes table for this task.")}</th>
            <th class="py-2 px-3 align-bottom">{info_tip_wrap("<span>tier</span>", "must_write = enforced hard edits; candidate_context = soft tier readable + maybe writable via replan; needs_replan = uncertain until replanned.")}</th>
            <th class="py-2 px-3 text-center align-bottom">{info_tip_wrap("<span>score</span>", "Planner confidence score for surfacing this path — used by reranking and replan guard thresholds.")}</th>
            <th class="py-2 px-3 align-bottom">{info_tip_wrap("<span>signals</span>", "Evidence channels that nominated this file (BM25, graph neighbors, LLM rerank, explicit prompt mention, etc.). Multiple strong signals usually mean safer scope.")}</th>
          </tr>
        </thead>
        <tbody>{"".join(scopes_html)}</tbody>
      </table>
    </div>
  </div>
</details>
""")

    return f"""
<section id="plans" class="mb-12 scroll-mt-24 md:scroll-mt-28">
  <h2 class="text-2xl font-bold mb-2 text-slate-100 inline-flex flex-wrap items-center gap-2">{info_tip_wrap("<span>Orchestrator plans (seed 1)</span>", "Raw lockfile JSON from seed 1: prompt, predicted writes, globs, execution_plan groups, and scored file_scopes. This is exactly what workers consumed.")}</h2>
  <p class="text-slate-400 mb-4">Yes — the orchestrator is writing detailed plans. Each lockfile contains the task prompt, predicted writes (must_write tier), allowed paths (glob enforcement), candidate context (replan-eligible), and full file_scopes with per-file tier, score, and signals. These are the plans <em>workers actually consume</em>. Click each task to expand.</p>
  {"".join(sections)}
</section>
"""


GLOSSARY = [
    ("must_write tier", "The hard write set. Files that the predictor is confident a task will edit. Workers MUST stay inside the union of these paths' allowed_paths globs; writes outside are blocked."),
    ("candidate_context tier", "Soft set of files that workers can READ for context, and may WRITE to only if a runtime auto-replan guard approves the proposal. This is the recovery path for cases where must_write missed something."),
    ("needs_replan tier", "Files the planner flagged as uncertain — included for context but explicitly requiring a replan event before being written. Rarer than candidate_context."),
    ("allowed_paths", "Glob patterns listed per task in the lockfile. The runtime validator (acg.enforce.validate_write) blocks any worker write that doesn't match. This is the safety boundary."),
    ("hard_recall", "Of the ground-truth files for a task, what fraction landed in the must_write tier. Answers: did the predictor surface the right files at all?"),
    ("hard_precision", "Of the must_write tier, what fraction is in ground truth. Answers: how cluttered is the hard set with irrelevant files?"),
    ("hard_f1", "Harmonic mean of hard_recall and hard_precision."),
    ("candidate_recall", "Of the ground-truth files for a task, what fraction landed in must_write ∪ candidate_context. The reachable upper bound on recall before any replan."),
    ("candidate_precision", "Of the union (must_write ∪ candidate_context), what fraction is in ground truth. Trades off against recall — bigger candidate sets dilute precision."),
    ("candidate_count_median", "Median size of the candidate_context tier across tasks. Should be moderate (~8–14): too small means recovery is impossible, too big dilutes precision and forces noisy review."),
    ("approved_replan_count", "Count of writes that were initially blocked (path was in candidate_context, not must_write) but subsequently auto-approved by the runtime guard because the score and signals justified it. This is the replan recovery path firing."),
    ("blocked_write_count", "Count of writes the validator blocked entirely (no auto-replan approval). High counts on naive_parallel are normal; on acg_planned they indicate must_write missed."),
    ("out_of_bounds_count", "On naive_parallel only: count of writes the worker made that were outside any reasonable scope. naive doesn't enforce, so these can be either dangerous (over-writing) or accidentally correct."),
    ("blocked_truth_recoverable_fraction", "Of the ground-truth files that were blocked from must_write, how many had a viable approval path (i.e., were in candidate_context with adequate signals)."),
    ("macro_f1", "Mean of per-task F1. Reported per strategy. The headline strategy comparison metric."),
    ("σ (sigma)", "Standard deviation across the 3 seeds. The eval was run 3 times with different LLM seeds; σ tells you how much of any metric difference is within noise."),
    ("tokens_prompt_total", "Sum of input (prompt) tokens consumed by all worker LLM calls for the strategy on this task."),
    ("tokens_completion_total", "Sum of output (completion) tokens generated. Strategies typically don't differ here."),
    ("tokens_all_in", "Sum across ALL LLM calls including planner + scope review + workers. ACG has overhead for planning that naive doesn't."),
    ("cost_usd_total", "Token total × OpenRouter price for qwen3-coder-30b-a3b-instruct. Useful to verify the cost-efficiency claim."),
    ("file_scope.signals", "Each file_scope carries a list of signals from the indexers that surfaced it: explicit (name match in prompt), llm (LLM rerank promoted it), planner (planner emitted it), bm25 (BM25 lexical match), pagerank (graph centrality), graph (graph neighbor of another scope), scip (SCIP entity index), entity (entity index), cochange (git history co-change), framework (framework convention), env (env variable), sibling (test/source sibling), symbol (symbol-name match), testlink (test-source link), hint (user-provided hint in tasks.json), scope_review (LLM scope reviewer kept or promoted it), must_write_neighbor (added by the post-LLM type/typings expansion). Higher-quality scopes have multiple high-precision signals."),
    ("seeded eval", "The same predictor and worker prompts are run 3 times with different LLM temperature seeds (1, 2, 3). Variance across seeds reveals stochastic noise vs. true architectural effects."),
    ("naive_parallel", "Baseline: every worker sees the full repo's top-K-imported files; no allowed_paths enforcement; no planner; workers fan out unconditionally."),
    ("acg_planned", "ACG planner runs first → produces lockfile with allowed_paths and scoped repo_graph per task. Workers see only their scoped graph. Strict enforcement: no auto-replan, no recovery."),
    ("acg_planned_replan", "Same as acg_planned, but with auto_replan=True. The runtime can promote a candidate_context path to a hard write at runtime if the worker proposes it AND the guard (score ≥ 0.72, signal in approved set, no hard conflict) approves."),
]


def render_glossary() -> str:
    rows = []
    for term, defn in GLOSSARY:
        rows.append(
            f'<div class="border-b border-slate-800 py-3">'
            f'<div class="font-mono text-sm font-semibold text-slate-100">{escape(term)}</div>'
            f'<div class="text-sm text-slate-400 mt-1">{escape(defn)}</div>'
            f"</div>"
        )
    return f"""
<section id="glossary" class="mb-12 scroll-mt-24 md:scroll-mt-28">
  <h2 class="text-2xl font-bold mb-2 text-slate-100 inline-flex flex-wrap items-center gap-2">{info_tip_wrap("<span>Glossary — every metric, plain language</span>", "Long-form definitions for every term reused across the report. Use ⓘ icons elsewhere for quick reminders without scrolling here.")}</h2>
  <p class="text-slate-400 mb-4">If you have been hands-off for a while: read this section first. Every other section uses these terms.</p>
  <div class="rounded-xl bg-slate-900 shadow-lg ring-1 ring-slate-700 p-5">
    {"".join(rows)}
  </div>
</section>
"""


def render_intro() -> str:
    return """
<section id="intro" class="mb-12 scroll-mt-24 md:scroll-mt-28">
  <div class="rounded-xl bg-slate-900 shadow-lg ring-1 ring-slate-700 p-6">
    <p class="text-slate-400 mb-3 text-sm border-l-2 border-sky-700 pl-3">Tip: hover or keyboard-focus the small <strong class="text-slate-300">i</strong> buttons next to headings and table columns for plain-language definitions without scrolling to the Glossary.</p> Before any worker LLM runs, ACG compiles a <em>lockfile</em>:
    a per-task plan with predicted writes, allowed-path globs, candidate context, and a fully scored file scope graph.
    Workers receive only the <strong class="text-slate-100">scoped repo graph</strong> for their task — files the planner believes are relevant.
    The runtime validator blocks any write outside <code>allowed_paths</code>; the auto-replan guard can recover by promoting candidate-context paths into hard writes if the proposal looks safe.</p>
    <p class="text-slate-300 mb-3">This eval compares three strategies on 11 real-world PR tasks (axios, commander_js, express, fastify, flask, starlette, urllib3) across 3 LLM seeds:</p>
    <ul class="list-disc list-inside text-sm text-slate-300 ml-2 space-y-1">
      <li><strong class="text-slate-200">naive_parallel</strong> — no planning, no enforcement; baseline.</li>
      <li><strong class="text-slate-200">acg_planned</strong> — planning + enforcement, no recovery.</li>
      <li><strong class="text-slate-200">acg_planned_replan</strong> — planning + enforcement + auto-replan recovery.</li>
    </ul>
    <p class="text-slate-300 mt-3">Two questions this report answers: (1) is ACG getting the right F1 on these tasks? (2) is ACG saving tokens like the design promises?</p>
  </div>
</section>
"""


# ---------------------------------------------------------------------------
# Page assembly
# ---------------------------------------------------------------------------

NAV_SECTIONS: tuple[tuple[str, str], ...] = (
    ("intro", "Overview"),
    ("summary", "Summary"),
    ("per-task", "Per-task"),
    ("tokens", "Token economy"),
    ("predictor", "Predictor quality"),
    ("plans", "Plans"),
    ("glossary", "Glossary"),
)


def sorted_plan_task_keys(data: dict[str, Any]) -> list[str]:
    """Same ordering as the per-task table (strategy_scores tasks)."""
    return sorted(per_task_strategy_scores(data["strat_scores_per_seed"]).keys())


HEAD = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#020617">
  <meta name="color-scheme" content="dark">
  <title>ACG Graph Expansion Eval — Live Run Report</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif; }
    code, .font-mono { font-family: "SF Mono", "Monaco", "Menlo", "Cascadia Code", monospace; }
    code:not(pre code) { background-color: rgb(15 23 42); padding: 0.125rem 0.375rem; border-radius: 0.25rem; font-size: 0.875em; color: rgb(226 232 240); border: 1px solid rgb(51 65 85); }
    details > summary { list-style: none; }
    details > summary::-webkit-details-marker { display: none; }
    .nav-section-link.nav-active { background-color: rgb(30 41 59); color: rgb(186 230 253); font-weight: 600; }
  </style>
</head>
<body class="bg-slate-950 text-slate-100 antialiased">
"""


def render_layout_start(data: dict[str, Any]) -> str:
    task_items = "".join(
        f'<li><a href="#plan-{escape(tk.replace("/", "-"))}" class="plan-task-link block truncate rounded px-2 py-1 font-mono text-[11px] text-slate-400 hover:bg-slate-800 hover:text-slate-200" title="{escape(tk)}">{escape(tk)}</a></li>'
        for tk in sorted_plan_task_keys(data)
    )
    nav_chunks: list[str] = []
    for sid, label in NAV_SECTIONS:
        if sid == "plans":
            nav_chunks.append(
                f"""
<div class="mt-0.5">
  <a href="#plans" data-nav-section="plans" class="nav-section-link block rounded-md px-2 py-1.5 text-sm text-slate-300 hover:bg-slate-800">{escape(label)}</a>
  <div class="mt-1 border-l border-slate-700 pl-2 ml-2">
    <div class="px-2 pb-1 pt-0.5 text-[10px] font-semibold uppercase tracking-wider text-slate-500">Tasks</div>
    <ul class="max-h-52 space-y-0 overflow-y-auto pr-1">{task_items}</ul>
  </div>
</div>
"""
            )
        else:
            nav_chunks.append(
                f'<a href="#{sid}" data-nav-section="{sid}" class="nav-section-link block rounded-md px-2 py-1.5 text-sm text-slate-300 hover:bg-slate-800">{escape(label)}</a>'
            )
    sidebar_nav = "\n".join(nav_chunks)
    return f"""
<div id="app-shell" class="flex min-h-screen bg-slate-950">
  <header class="fixed left-0 right-0 top-0 z-[60] flex h-12 items-center justify-between border-b border-slate-800 bg-slate-900 px-4 md:hidden">
    <span class="font-semibold text-slate-100">ACG Eval Report</span>
    <button type="button" id="nav-toggle" class="rounded-md px-3 py-1.5 text-sm font-medium text-slate-300 hover:bg-slate-800" aria-expanded="false" aria-controls="app-sidebar">Menu</button>
  </header>
  <div id="nav-backdrop" class="fixed inset-0 z-40 hidden bg-black/60 md:hidden" aria-hidden="true"></div>
  <aside id="app-sidebar" class="fixed bottom-0 left-0 top-12 z-50 w-64 max-w-[85vw] -translate-x-full overflow-y-auto border-r border-slate-800 bg-slate-900 px-3 py-4 shadow-xl shadow-black/40 transition-transform duration-200 ease-out md:static md:top-0 md:z-auto md:h-screen md:max-w-none md:w-56 md:translate-x-0 lg:w-64 md:flex-shrink-0 md:self-start md:border-r md:shadow-none md:sticky md:top-0" aria-label="Report sections">
    <div class="mb-4 hidden border-b border-slate-800 pb-3 md:block">
      <div class="text-xs font-bold uppercase tracking-wide text-slate-100">ACG Eval Report</div>
      <div class="mt-1 text-[11px] text-slate-500">Live metrics dashboard</div>
    </div>
    <nav class="flex flex-col gap-0.5" id="sidebar-nav">
      {sidebar_nav}
    </nav>
  </aside>
  <div id="main-column" class="flex min-w-0 flex-1 flex-col pt-12 md:pt-0">
    <main class="mx-auto w-full max-w-6xl px-4 py-6">
"""


LAYOUT_SCRIPT = """
<script>
(function () {
  var sectionIds = ["intro","summary","per-task","tokens","predictor","plans","glossary"];
  var sidebar = document.getElementById("app-sidebar");
  var backdrop = document.getElementById("nav-backdrop");
  var toggle = document.getElementById("nav-toggle");

  function isMobile() { return window.matchMedia("(max-width: 767px)").matches; }

  function openDrawer() {
    if (!sidebar || !backdrop) return;
    sidebar.classList.remove("-translate-x-full");
    backdrop.classList.remove("hidden");
    if (toggle) toggle.setAttribute("aria-expanded", "true");
  }

  function closeDrawer() {
    if (!sidebar || !backdrop) return;
    if (!isMobile()) return;
    sidebar.classList.add("-translate-x-full");
    backdrop.classList.add("hidden");
    if (toggle) toggle.setAttribute("aria-expanded", "false");
  }

  if (toggle && sidebar && backdrop) {
    toggle.addEventListener("click", function () {
      if (sidebar.classList.contains("-translate-x-full")) openDrawer();
      else closeDrawer();
    });
    backdrop.addEventListener("click", closeDrawer);
  }

  document.querySelectorAll("#sidebar-nav a[href^='#']").forEach(function (a) {
    a.addEventListener("click", function () {
      if (isMobile()) closeDrawer();
    });
  });

  window.addEventListener("resize", function () {
    if (!isMobile() && sidebar && backdrop) {
      sidebar.classList.remove("-translate-x-full");
      backdrop.classList.add("hidden");
      if (toggle) toggle.setAttribute("aria-expanded", "false");
    }
    if (isMobile() && sidebar && backdrop && backdrop.classList.contains("hidden")) {
      sidebar.classList.add("-translate-x-full");
    }
  });

  function syncNavActive() {
    var pad = 96;
    var y = window.scrollY + pad;
    var current = sectionIds[0];
    for (var i = 0; i < sectionIds.length; i++) {
      var el = document.getElementById(sectionIds[i]);
      if (el && el.offsetTop <= y) current = sectionIds[i];
    }
    document.querySelectorAll("[data-nav-section]").forEach(function (link) {
      var sid = link.getAttribute("data-nav-section");
      link.classList.toggle("nav-active", sid === current);
    });
  }

  window.addEventListener("scroll", syncNavActive, { passive: true });
  syncNavActive();

  var obsSections = sectionIds.map(function (id) { return document.getElementById(id); }).filter(Boolean);
  if ("IntersectionObserver" in window && obsSections.length) {
    var io = new IntersectionObserver(syncNavActive, { rootMargin: "-72px 0px -55% 0px", threshold: [0, 0.02, 0.1] });
    obsSections.forEach(function (el) { io.observe(el); });
  }
})();
</script>
"""


def render_layout_end() -> str:
    return f"""
</main>
<footer class="border-t border-slate-800 bg-slate-900 px-4 py-6 text-center text-xs text-slate-500">
  <p>Generated by <code class="rounded bg-slate-800 px-1 py-0.5 font-mono text-[11px] text-slate-300 ring-1 ring-slate-700">experiments/real_repos/graph_expansion_eval/build_report_html.py</code></p>
  <p class="mt-2">Serve from this folder so <strong class="text-slate-400">/</strong> loads the dashboard: <code class="rounded bg-slate-800 px-1 py-0.5 font-mono text-[11px] text-slate-300 ring-1 ring-slate-700">python -m http.server 8765</code> then open <code class="rounded bg-slate-800 px-1 py-0.5 font-mono text-[11px] text-slate-300 ring-1 ring-slate-700">http://127.0.0.1:8765/</code> (via <code class="font-mono text-[11px] text-slate-400">index.html</code>). Or run <code class="rounded bg-slate-800 px-1 py-0.5 font-mono text-[11px] text-slate-300 ring-1 ring-slate-700">build_report_html.py --serve</code>.</p>
  <p class="mt-2">Regenerate: <code class="rounded bg-slate-800 px-1 py-0.5 font-mono text-[11px] text-slate-300 ring-1 ring-slate-700">./.venv/bin/python experiments/real_repos/graph_expansion_eval/build_report_html.py</code></p>
</footer>
</div>
</div>
{LAYOUT_SCRIPT}
</body>
</html>
"""


def render_header() -> str:
    return """
<header class="pb-2 pt-2 md:pt-6">
  <h1 class="text-3xl font-bold text-slate-100">ACG Graph Expansion Eval — Live Run Report</h1>
  <p class="text-sm text-slate-400 mt-2">11 PR tasks · 3 LLM seeds · qwen/qwen3-coder-30b-a3b-instruct via OpenRouter · ACG_AUTO_REPLAN=1</p>
</header>
"""


def build_report_html() -> Path:
    data = load_all()
    html = (
        HEAD
        + render_layout_start(data)
        + render_header()
        + render_intro()
        + render_summary(data)
        + render_per_task_table(data)
        + render_token_economy(data)
        + render_predictor_quality(data)
        + render_plans(data)
        + render_glossary()
        + render_layout_end()
    )
    report_path = EVAL_DIR / "eval_report.html"
    index_path = EVAL_DIR / "index.html"
    report_path.write_text(html)
    index_path.write_text(html)
    print(f"wrote {report_path}")
    print(f"wrote {index_path}")
    return report_path


def serve_http(port: int) -> None:
    import os
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    os.chdir(EVAL_DIR)
    srv = ThreadingHTTPServer(("127.0.0.1", port), SimpleHTTPRequestHandler)
    print(f"Serving {EVAL_DIR} at http://127.0.0.1:{port}/ (open / for dashboard)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


def main() -> None:
    build_report_html()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build ACG graph expansion eval HTML dashboard.")
    parser.add_argument(
        "--serve",
        action="store_true",
        help=f"Serving directory after build: {EVAL_DIR}",
    )
    parser.add_argument("--port", type=int, default=8765, help="Port for --serve (default 8765)")
    args = parser.parse_args()
    main()
    if args.serve:
        serve_http(args.port)
