import { useState } from "react";

interface Section {
  title: string;
  rows: { label: string; value: string; note?: string }[];
}

const SECTIONS: Section[] = [
  {
    title: "RealWorld NestJS — Blind Tasks (N=5 OpenRouter seeds)",
    rows: [
      {
        label: "Prompt-token reduction (scoped vs naive)",
        value: "49.8%",
        note: "deterministic, zero variance across seeds",
      },
      {
        label: "Blocked invalid writes (planned full-context)",
        value: "2.6 / run",
        note: "95% CI 2.2–3.0",
      },
      {
        label: "Out-of-bounds proposals (naive parallel)",
        value: "2.8 / run",
        note: "95% CI 2.2–3.4",
      },
      {
        label: "Scoped planned safety",
        value: "0 OOB · 0 blocked",
        note: "across all 5 seeds",
      },
    ],
  },
  {
    title: "Greenhouse Java — Scope Ablation (N=5)",
    rows: [
      {
        label: "Prompt-token reduction",
        value: "9.71%",
        note: "deterministic, zero variance",
      },
      {
        label: "Safety",
        value: "0 OOB · 0 blocked",
        note: "all strategies",
      },
    ],
  },
  {
    title: "Fastify Production Repo — Ground Truth (3 PRs)",
    rows: [
      {
        label: "Qwen3-coder-30b planned / naive F1",
        value: "0.333 / 0.133",
        note: "2.50× scope-effect ratio",
      },
      {
        label: "Kimi K2 0905 planned / naive F1",
        value: "0.356 / 0.133",
        note: "2.67× scope-effect ratio",
      },
      {
        label: "Predictor-scope bound",
        value: "8/10 GT files out of scope",
        note: "recall ceiling is predictor, not model",
      },
    ],
  },
  {
    title: "Predictor Accuracy — Full Pipeline (10 runs, 48 tasks)",
    rows: [
      { label: "Precision", value: "0.82", note: "measured vs agent actual_changed_files" },
      { label: "Recall", value: "1.00", note: "saturated on most fixtures" },
      { label: "F1", value: "0.90", note: "" },
      {
        label: "Java precision",
        value: "0.25",
        note: "over-predicts 4× — key refinement target",
      },
    ],
  },
  {
    title: "Production Repo Aggregate (6 PRs, 2 repos)",
    rows: [
      { label: "Fastify recall / precision", value: "0.278 / 0.194", note: "" },
      { label: "Starlette recall / precision", value: "0.889 / 0.556", note: "" },
      { label: "End-to-end apply-and-test", value: "0 / 6 PRs", note: "skipped" },
    ],
  },
];

export function ResultsPanel() {
  const [open, setOpen] = useState<Set<number>>(new Set([0]));

  const toggle = (i: number) => {
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });
  };

  return (
    <div className="results-panel">
      <h2 className="results-header">Latest Results</h2>
      <div className="results-disclaimer">
        Numbers from the v2 megaplan artifacts (May 2026). See{" "}
        <code>experiments/PAPER_NUMBERS.md</code> for citations and caveats.
      </div>
      {SECTIONS.map((sec, i) => (
        <div key={i} className="results-section">
          <button
            className="results-section-title"
            onClick={() => toggle(i)}
            aria-expanded={open.has(i)}
          >
            <span>{open.has(i) ? "▼" : "▶"}</span>
            <span>{sec.title}</span>
          </button>
          {open.has(i) && (
            <div className="results-table-wrap">
              <table className="results-table">
                <tbody>
                  {sec.rows.map((r, j) => (
                    <tr key={j}>
                      <td className="results-label">{r.label}</td>
                      <td className="results-value">{r.value}</td>
                      <td className="results-note">{r.note}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
