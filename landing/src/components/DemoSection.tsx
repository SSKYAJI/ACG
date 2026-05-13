import { LINKS } from "../constants";

export function DemoSection() {
  const hasEmbed = Boolean(LINKS.demoVideoEmbed);

  return (
    <section id="demo" className="demo-section" aria-labelledby="demo-heading">
      <div className="container demo-grid">
        <div className="demo-copy">
          <h2 className="h2 type-section-title" id="demo-heading">
            See it in action
          </h2>
          <p className="muted demo-subtitle">
            Compile → run → block out-of-scope writes.
          </p>
        </div>

        <div className="demo-video-wrap card" role="region" aria-label="Demo video player">
          {hasEmbed ? (
            <div className="video-ratio">
              <iframe
                className="video-iframe"
                title="ACG demo video"
                src={LINKS.demoVideoEmbed}
                allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                allowFullScreen
              />
            </div>
          ) : (
            <div className="video-placeholder">
              <p className="video-placeholder-title">Embed URL not set</p>
              <p className="video-placeholder-copy">
                Set <code className="inline-code">demoVideoEmbed</code> in constants.
              </p>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
