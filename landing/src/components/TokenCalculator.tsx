import { useMemo, useState } from "react";

import { CALCULATOR_FIXED_REDUCTION, CALCULATOR_FIXED_TOKENS_PER_TASK } from "../constants";
import { ComparisonChart } from "./ComparisonChart";

export function TokenCalculator() {
  const [tasks, setTasks] = useState(6);

  const tokensPerTask = CALCULATOR_FIXED_TOKENS_PER_TASK;

  const metrics = useMemo(() => {
    const naive = Math.round(tasks * tokensPerTask);
    const scoped = Math.round(naive * (1 - CALCULATOR_FIXED_REDUCTION));
    const saved = Math.max(0, naive - scoped);
    const pctSaved = naive === 0 ? 0 : Math.round((saved / naive) * 100);
    const barScoped = naive === 0 ? 0 : (scoped / naive) * 100;
    const barSaved = naive === 0 ? 0 : (saved / naive) * 100;
    return { naive, scoped, saved, pctSaved, barScoped, barSaved };
  }, [tasks, tokensPerTask]);

  return (
    <section className="calc-section" id="calculator" aria-labelledby="calc-heading">
      <div className="container calc-center">
        <p className="section-heading">Estimate</p>
        <h2 className="h2 type-section-title" id="calc-heading">
          Context budget vs <span className="emphasis-orange">scoped</span> prompts
        </h2>
        <p className="muted calc-lede">
          Illustrative totals: <strong>{tasks} tasks</strong> ×{" "}
          <span className="mono emphasis-teal">{tokensPerTask.toLocaleString()}</span> planner tokens each. Scoped totals
          assume a fixed <strong>{Math.round(CALCULATOR_FIXED_REDUCTION * 100)}%</strong> narrower context versus naive
          full-repo framing.
        </p>

        <div className="card calc-card-outer">
          <div className="calc-toolbar">
            <div className="field field-single">
              <div className="field-row">
                <label className="field-label field-label-strong" htmlFor="tasks-input">
                  Number of tasks
                </label>
                <output className="field-value mono" htmlFor="tasks-input">
                  {tasks}
                </output>
              </div>
              <input
                id="tasks-input"
                className="range"
                type="range"
                min={2}
                max={20}
                step={1}
                value={tasks}
                aria-valuemin={2}
                aria-valuemax={20}
                aria-valuenow={tasks}
                aria-label="Number of parallel tasks"
                onChange={(e) => setTasks(Number.parseInt(e.target.value, 10))}
              />
            </div>
          </div>

          <div className="calc-grid-inner">
            <div className="calc-output text-left" aria-live="polite">
              <div className="kpi-grid">
                <div className="kpi card kpi-mini">
                  <p className="kpi-label">Naive tokens</p>
                  <p className="kpi-num mono">{metrics.naive.toLocaleString()}</p>
                </div>
                <div className="kpi card kpi-mini">
                  <p className="kpi-label">ACG-scoped tokens</p>
                  <p className="kpi-num mono">{metrics.scoped.toLocaleString()}</p>
                </div>
                <div className="kpi card kpi-mini kpi-accent">
                  <p className="kpi-label">Tokens saved</p>
                  <p className="kpi-num mono">{metrics.saved.toLocaleString()}</p>
                </div>
                <div className="kpi card kpi-mini">
                  <p className="kpi-label">Percent saved</p>
                  <p className="kpi-num mono">{metrics.pctSaved}%</p>
                </div>
              </div>

              <ComparisonChart metrics={metrics} labelledBy="calc-heading" />
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
