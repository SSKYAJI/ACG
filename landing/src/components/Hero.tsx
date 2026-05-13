import { CommandBlock } from "./CommandBlock";

export function Hero() {
  return (
    <section className="hero" aria-labelledby="hero-title">
      <div className="container hero-center">
        <h1 className="h1 hero-h1 type-display" id="hero-title">
          <span className="hero-line">Ship parallel agents</span>
          <span className="hero-line emphasis-orange">without merge chaos.</span>
        </h1>
        <p className="lead hero-lead">
          ACG cuts review noise and wasted tokens by giving every coding agent a committable{" "}
          <code className="inline-code">agent_lock.json</code> — so out-of-scope writes are blocked
          before they reach your PR.
        </p>

        <div className="hero-install">
          <p className="install-label">Try it in your repo</p>
          <CommandBlock command="npx acg init" />
        </div>
      </div>
    </section>
  );
}
