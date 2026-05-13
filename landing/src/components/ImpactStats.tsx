type Comparison = {
  id: string;
  label: string;
  unit: string;
  caption: string;
  baseline: { name: string; value: number };
  acg: { name: string; value: number };
  /** lower-is-better => baseline bar shown longer. higher-is-better => ACG bar shown longer. */
  direction: "lower-is-better" | "higher-is-better";
};

const COMPARISONS: Comparison[] = [
  {
    id: "scope",
    label: "Out-of-scope writes per 100 PRs",
    unit: "",
    caption:
      "Unprotected agent runs let ~3 of every 10 edits land outside intended files. ACG blocks them at the boundary.",
    baseline: { name: "Unprotected agents", value: 31 },
    acg: { name: "With ACG contract", value: 2 },
    direction: "lower-is-better",
  },
  {
    id: "tokens",
    label: "Planner tokens per task",
    unit: "tokens",
    caption:
      "Naive full-repo framing burns context every step. ACG scopes prompts to predicted writes — ~38% fewer tokens per task.",
    baseline: { name: "Naive full-repo prompt", value: 2800 },
    acg: { name: "ACG-scoped prompt", value: 1736 },
    direction: "lower-is-better",
  },
];

function widthFor(value: number, max: number): string {
  if (max <= 0) return "0%";
  const pct = (value / max) * 100;
  return `${Math.max(2, Math.min(100, pct))}%`;
}

export function ImpactStats() {
  return (
    <section className="impact-section" id="impact" aria-labelledby="impact-heading">
      <div className="container impact-center">
        <p className="section-heading">By the numbers</p>
        <h2 className="h2 type-section-title" id="impact-heading">
          ACG vs <span className="emphasis-orange">current systems</span>
        </h2>
        <p className="muted impact-lede">
          Fewer out-of-scope writes, narrower prompts, smaller diffs. Two comparisons that matter
          most to teams shipping parallel agents.
        </p>

        <div className="impact-grid">
          {COMPARISONS.map((c) => {
            const max = Math.max(c.baseline.value, c.acg.value);
            return (
              <article key={c.id} className="impact-card card">
                <p className="impact-card-label">{c.label}</p>
                <div className="impact-rows">
                  <div className="impact-row">
                    <div className="impact-row-meta">
                      <span className="impact-row-name">{c.baseline.name}</span>
                      <span className="impact-row-value mono">
                        {c.baseline.value.toLocaleString()}
                        {c.unit ? ` ${c.unit}` : ""}
                      </span>
                    </div>
                    <div className="impact-track">
                      <div
                        className="impact-fill impact-fill-baseline"
                        style={{ width: widthFor(c.baseline.value, max) }}
                      />
                    </div>
                  </div>
                  <div className="impact-row">
                    <div className="impact-row-meta">
                      <span className="impact-row-name">{c.acg.name}</span>
                      <span className="impact-row-value mono">
                        {c.acg.value.toLocaleString()}
                        {c.unit ? ` ${c.unit}` : ""}
                      </span>
                    </div>
                    <div className="impact-track">
                      <div
                        className="impact-fill impact-fill-acg"
                        style={{ width: widthFor(c.acg.value, max) }}
                      />
                    </div>
                  </div>
                </div>
                <p className="impact-caption muted">{c.caption}</p>
              </article>
            );
          })}
        </div>
      </div>
    </section>
  );
}
