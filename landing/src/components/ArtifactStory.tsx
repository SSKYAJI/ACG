type StoryItem = {
  eyebrow: string;
  title: string;
  copy: string;
};

const STORIES: StoryItem[] = [
  {
    eyebrow: "01",
    title: "Scan repo",
    copy:
      "The graph builder walks the codebase and emits a cached context artifact. Symbols, imports, and structure feed the predictor.",
  },
  {
    eyebrow: "02",
    title: "Predict writes",
    copy:
      "Per task, ACG predicts a write-set and allowed path globs, then emits agent_lock.json: predicted_writes, allowed_paths, and depends_on for review.",
  },
  {
    eyebrow: "03",
    title: "Enforce boundary",
    copy:
      "Hooks and MCP callers validate proposed paths against the contract. Matching paths proceed; out-of-scope writes surface as receipts before CI noise.",
  },
  {
    eyebrow: "04",
    title: "Run safer parallelism",
    copy:
      "Tasks group by solver output so disjoint work stays parallel. Sequential edges stay explicit.",
  },
  {
    eyebrow: "05",
    title: "Impact",
    copy:
      "Fewer rollbacks, narrower diffs, and lower token spend per task. Teams ship parallel work without spending review cycles on merge cleanup.",
  },
];

export function ArtifactStory() {
  return (
    <section className="story-section" id="artifacts" aria-labelledby="story-heading">
      <div className="container pipeline-grid">
        <div className="pipeline-anchor">
          <p className="section-heading">Pipeline</p>
          <h2 className="h2 type-section-title" id="story-heading">
            From repo scan to <span className="emphasis-purple">parallel execution</span>
          </h2>
          <p className="muted story-lead">
            Bounded artifacts agents and tooling can reuse. Contracts follow the repo.
          </p>
        </div>

        <div className="pipeline-scroll" aria-labelledby="story-heading">
          <div className="pipeline-track">
            {STORIES.map((s) => (
              <article key={s.title} className="pipeline-card card" data-snap-scroll>
                <p className="story-eyebrow">{s.eyebrow}</p>
                <h3 className="story-title">{s.title}</h3>
                <p className="story-copy">{s.copy}</p>
              </article>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
