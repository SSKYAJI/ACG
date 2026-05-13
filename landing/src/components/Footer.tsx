import { LINKS } from "../constants";

export function Footer() {
  return (
    <footer className="site-footer">
      <div className="container">
        <div className="footer-grid">
          <div>
            <p className="footer-brand">ACG</p>
            <p className="footer-tag muted">
              Pre-execution write contracts for parallel coding agents.
            </p>
          </div>

          <div>
            <p className="footer-heading">Product</p>
            <ul className="footer-list">
              <li>
                <a href="#artifacts">Pipeline</a>
              </li>
              <li>
                <a href="#demo">Demo</a>
              </li>
              <li>
                <a href="#calculator">Estimate</a>
              </li>
            </ul>
          </div>

          <div>
            <p className="footer-heading">Resources</p>
            <ul className="footer-list">
              <li>
                <a href={LINKS.docsHome} target="_blank" rel="noreferrer noopener">
                  Docs <span className="ar">↗</span>
                </a>
              </li>
              <li>
                <a href={LINKS.githubRepo} target="_blank" rel="noreferrer noopener">
                  GitHub <span className="ar">↗</span>
                </a>
              </li>
            </ul>
          </div>

          <div>
            <p className="footer-heading">Team</p>
            <ul className="footer-list">
              <li>
                <a href={LINKS.linkedin.prajit} target="_blank" rel="noreferrer noopener">
                  Prajit <span className="ar">↗</span>
                </a>
              </li>
              <li>
                <a href={LINKS.linkedin.shashank} target="_blank" rel="noreferrer noopener">
                  Shashank <span className="ar">↗</span>
                </a>
              </li>
            </ul>
          </div>
        </div>

        <hr className="footer-rule" />
        <p className="footer-micro muted">© {new Date().getFullYear()} ACG. All rights reserved.</p>
      </div>
    </footer>
  );
}
