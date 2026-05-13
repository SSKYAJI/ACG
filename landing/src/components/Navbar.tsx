import { useState } from "react";

import { LINKS } from "../constants";

export function Navbar() {
  const [open, setOpen] = useState(false);

  return (
    <header className="nav-header">
      <div className="container nav-shell">
        <a className="brand" href="#top">
          <span className="brand-mark">ACG</span>
          <span className="brand-sub">Agent Context Graph</span>
        </a>

        <div className="nav-trailing">
          <button
            type="button"
            className="nav-toggle"
            aria-expanded={open}
            aria-controls="primary-nav"
            aria-label={open ? "Close menu" : "Open menu"}
            onClick={() => setOpen((v) => !v)}
          >
            {open ? "✕" : "☰"}
          </button>
        </div>

        <nav id="primary-nav" className={`nav-links${open ? " is-open" : ""}`} aria-label="Primary">
          <a className="nav-link nav-link-internal" href="#demo" onClick={() => setOpen(false)}>
            Demo
          </a>
          <a className="nav-link nav-link-internal" href="#artifacts" onClick={() => setOpen(false)}>
            Pipeline
          </a>
          <a className="nav-link nav-link-internal" href="#impact" onClick={() => setOpen(false)}>
            Impact
          </a>
          <a
            className="nav-link ext-link"
            href={LINKS.docsHome}
            target="_blank"
            rel="noreferrer noopener"
          >
            Docs <span className="ar">↗</span>
          </a>
          <a
            className="nav-link ext-link"
            href={LINKS.githubRepo}
            target="_blank"
            rel="noreferrer noopener"
          >
            GitHub <span className="ar">↗</span>
          </a>
        </nav>
      </div>
    </header>
  );
}
