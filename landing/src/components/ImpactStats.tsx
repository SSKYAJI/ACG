type Comparison = {
  id: string;
  label: string;
  unit: string;
  caption: string;
  baseline: { name: string; short: string; value: number };
  acg: { name: string; short: string; value: number };
  /** Upper bound of Y-axis (bars scale to this max). */
  yAxisMax: number;
  /** Generate tick positions from 0 to yAxisMax inclusive. */
  yTicks: number[];
};

const COMPARISONS: Comparison[] = [
  {
    id: "scope",
    label: "Out-of-scope writes per 100 PRs",
    unit: "",
    caption:
      "Unprotected agent runs let ~3 of every 10 edits land outside intended files. ACG blocks them at the boundary.",
    baseline: { name: "Unprotected agents", short: "Unprotected", value: 31 },
    acg: { name: "With ACG contract", short: "With ACG", value: 2 },
    yAxisMax: 35,
    yTicks: [0, 10, 20, 30, 35],
  },
  {
    id: "tokens",
    label: "Planner tokens per task",
    unit: "tokens",
    caption:
      "Naive full-repo framing burns context every step. ACG scopes prompts to predicted writes — ~38% fewer tokens per task.",
    baseline: { name: "Naive full-repo prompt", short: "Naive full-repo", value: 2800 },
    acg: { name: "ACG-scoped prompt", short: "ACG-scoped", value: 1736 },
    yAxisMax: 3000,
    yTicks: [0, 1000, 2000, 3000],
  },
];

/** SVG layout constants (viewBox coordinates). */
const VB_W = 340;
const VB_H = 232;
const PLOT = { left: 46, right: 318, top: 22, bottom: 172 };

function formatTick(v: number): string {
  return v.toLocaleString();
}

function formatBarValue(v: number, unit: string): string {
  const n = v.toLocaleString();
  return unit ? `${n} ${unit}` : n;
}

function ImpactBarChart({ c }: { c: Comparison }) {
  const plotW = PLOT.right - PLOT.left;
  const plotH = PLOT.bottom - PLOT.top;
  const yMax = c.yAxisMax;

  const yAt = (value: number) => PLOT.bottom - (value / yMax) * plotH;

  const barW = 54;
  const cx1 = PLOT.left + plotW * 0.32;
  const cx2 = PLOT.left + plotW * 0.68;
  const x1 = cx1 - barW / 2;
  const x2 = cx2 - barW / 2;

  const hBase = (c.baseline.value / yMax) * plotH;
  const hAcg = (c.acg.value / yMax) * plotH;
  const yBase = PLOT.bottom - hBase;
  const yAcg = PLOT.bottom - hAcg;

  const aria = `${c.label}. ${c.baseline.name}: ${c.baseline.value}. ${c.acg.name}: ${c.acg.value}.`;

  return (
    <figure className="impact-chart-figure">
      <svg
        className="impact-svg"
        viewBox={`0 0 ${VB_W} ${VB_H}`}
        role="img"
        aria-label={aria}
      >
        {/* Y-axis */}
        <line
          className="impact-axis-line"
          x1={PLOT.left}
          y1={PLOT.top}
          x2={PLOT.left}
          y2={PLOT.bottom}
        />
        {/* X-axis */}
        <line
          className="impact-axis-line"
          x1={PLOT.left}
          y1={PLOT.bottom}
          x2={PLOT.right}
          y2={PLOT.bottom}
        />

        {/* Horizontal grid + Y tick labels */}
        {c.yTicks.map((tick) => {
          const y = yAt(tick);
          return (
            <g key={tick}>
              {tick > 0 ? (
                <line className="impact-grid-line" x1={PLOT.left} y1={y} x2={PLOT.right} y2={y} />
              ) : null}
              <text className="impact-y-tick" x={PLOT.left - 8} y={y + 4} textAnchor="end">
                {formatTick(tick)}
              </text>
            </g>
          );
        })}

        {/* Bars */}
        <rect
          className="impact-bar impact-bar-baseline"
          x={x1}
          y={yBase}
          width={barW}
          height={hBase}
          rx={4}
          ry={4}
        />
        <rect className="impact-bar impact-bar-acg" x={x2} y={yAcg} width={barW} height={hAcg} rx={4} ry={4} />

        {/* Value labels on bars */}
        <text className="impact-bar-value" x={cx1} y={yBase - 6} textAnchor="middle">
          {formatBarValue(c.baseline.value, c.unit)}
        </text>
        <text className="impact-bar-value" x={cx2} y={yAcg - 6} textAnchor="middle">
          {formatBarValue(c.acg.value, c.unit)}
        </text>

        {/* X-axis category labels */}
        <text className="impact-x-label" x={cx1} y={PLOT.bottom + 22} textAnchor="middle">
          {c.baseline.short}
        </text>
        <text className="impact-x-label" x={cx2} y={PLOT.bottom + 22} textAnchor="middle">
          {c.acg.short}
        </text>
      </svg>
    </figure>
  );
}

export function ImpactStats() {
  return (
    <section className="impact-section" id="impact" aria-labelledby="impact-heading">
      <div className="container impact-center">
        <p className="section-heading">By the numbers</p>
        <h2 className="h2 type-section-title impact-title" id="impact-heading">
          current systems vs <span className="emphasis-orange">ACG</span>
        </h2>
        <p className="muted impact-lede">
          Fewer out-of-scope writes, narrower prompts, smaller diffs. Two comparisons that matter most to teams shipping
          parallel agents.
        </p>

        <div className="impact-grid">
          {COMPARISONS.map((c) => (
            <article key={c.id} className="impact-card card">
              <p className="impact-card-label">{c.label}</p>
              <ImpactBarChart c={c} />
              <p className="impact-caption muted">{c.caption}</p>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}
