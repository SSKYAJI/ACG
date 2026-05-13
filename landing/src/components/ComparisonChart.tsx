type Metrics = {
  naive: number;
  scoped: number;
  saved: number;
  pctSaved: number;
  barScoped: number;
  barSaved: number;
};

type Props = {
  metrics: Metrics;
  labelledBy?: string;
};

export function ComparisonChart({ metrics, labelledBy }: Props) {
  return (
    <figure className="chart-figure" aria-labelledby={labelledBy}>
      <figcaption className="chart-caption muted">Per-task planner tokens compared.</figcaption>
      <div className="bar-chart-compact" role="img" aria-label="Naive vs ACG-scoped vs saved tokens">
        <div className="bar-row-chart">
          <div className="bar-meta-chart">
            <span>Naive</span>
            <span className="bar-num-chart mono">{metrics.naive.toLocaleString()}</span>
          </div>
          <div className="bar-track-chart">
            <div className="bar-fill-chart bar-chart-teal" style={{ width: "100%" }} />
          </div>
        </div>

        <div className="bar-row-chart">
          <div className="bar-meta-chart">
            <span>ACG scoped</span>
            <span className="bar-num-chart mono">{metrics.scoped.toLocaleString()}</span>
          </div>
          <div className="bar-track-chart">
            <div
              className="bar-fill-chart bar-chart-purple"
              style={{ width: `${metrics.barScoped}%` }}
            />
          </div>
        </div>

        <div className="bar-row-chart">
          <div className="bar-meta-chart">
            <span>Saved delta</span>
            <span className="bar-num-chart mono">{metrics.saved.toLocaleString()}</span>
          </div>
          <div className="bar-track-chart">
            <div
              className="bar-fill-chart bar-chart-orange"
              style={{ width: `${metrics.barSaved}%` }}
            />
          </div>
        </div>
      </div>
    </figure>
  );
}
