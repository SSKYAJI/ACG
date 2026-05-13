import { LINKS } from "../constants";

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
];

export function ArtifactStory() {
  const embed = LINKS.demoVideoEmbed;

  return (
    <section className="story-section" id="artifacts" aria-labelledby="story-heading">
      <div className="container pipeline-grid">
        <div className="pipeline-anchor">
          <h2 className="h2 type-section-title" id="story-heading">
            From repo scan to <span className="emphasis-orange">safer parallel execution</span>
          </h2>
          <p className="muted story-lead">
            Scan → predict writes → enforce boundaries → run agents in parallel.
          </p>
        </div>

        <div className="pipeline-stack" aria-labelledby="story-heading">
          {STORIES.map((s) => (
            <article key={s.title} className="pipeline-block card">
              <div className="pipeline-block-head">
                <p className="story-eyebrow">{s.eyebrow}</p>
                <h3 className="story-title">{s.title}</h3>
                <p className="story-copy">{s.copy}</p>
              </div>
              <div className="pipeline-block-video" role="region" aria-label={`${s.title} demo placeholder`}>
                {embed ? (
                  <div className="video-ratio">
                    <iframe
                      className="video-iframe"
                      title={`${s.title} demo placeholder`}
                      src={embed}
                      allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                      allowFullScreen
                    />
                  </div>
                ) : (
                  <div className="video-placeholder">
                    <p className="video-placeholder-copy">Demo video coming soon.</p>
                  </div>
                )}
              </div>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}
