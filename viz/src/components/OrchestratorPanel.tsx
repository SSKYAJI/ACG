import { useState } from "react";
import type { TraceOrchestrator } from "../types";
import type { ReplayState } from "../lib/replay";

interface Props {
  orchestrator: TraceOrchestrator;
  replay: ReplayState;
}

/**
 * Floats over the canvas. Three visual modes keyed off `replay.phase`:
 *
 *  - idle:         hidden
 *  - orchestrator: expanded; typewriter `reasoning_content` up to `orchTokensVisible`
 *  - groups/done:  collapsed pill summarising wall_s + tokens + approved/concerns;
 *                  click to re-expand and inspect the full reasoning + parsed JSON.
 */
export function OrchestratorPanel({ orchestrator, replay }: Props) {
  const [manualExpand, setManualExpand] = useState(false);

  if (replay.phase === "idle") return null;

  const isThinking = replay.phase === "orchestrator";
  const expanded = isThinking || manualExpand;
  const reasoning = orchestrator.reasoning_content || "";
  const visibleReasoning = reasoning.slice(
    0,
    Math.max(0, replay.orchTokensVisible),
  );
  const parsed = orchestrator.parsed;
  const approved = parsed?.approved;
  const concerns = parsed?.concerns ?? [];

  // Fall back to `content` when the server ran without --reasoning-budget
  // (i.e. reasoning_content is empty). Lets the panel still show *something*.
  const fallbackContent =
    !reasoning && orchestrator.content ? orchestrator.content : "";

  return (
    <div className={`orch-panel ${expanded ? "expanded" : "collapsed"}`}>
      {!expanded ? (
        <button
          className="orch-pill"
          onClick={() => setManualExpand(true)}
          title="Click to inspect orchestrator reasoning"
        >
          <span className="brain">🧠</span>
          <span className="meta">
            orchestrator · {orchestrator.wall_s.toFixed(1)}s ·{" "}
            {orchestrator.completion_tokens} tok ·{" "}
            <span className={approved ? "approved" : "rejected"}>
              {approved === undefined ? "?" : approved ? "approved" : "rejected"}
            </span>
          </span>
        </button>
      ) : (
        <div className="orch-card">
          <div className="orch-header">
            <span className="brain">🧠</span>
            <span className="title">orchestrator</span>
            <span className="meta">
              {orchestrator.model || "?"} · {orchestrator.wall_s.toFixed(1)}s ·{" "}
              {orchestrator.completion_tokens} tokens
              {isThinking ? " · thinking…" : ""}
            </span>
            {!isThinking && (
              <button
                className="collapse"
                onClick={() => setManualExpand(false)}
              >
                ✕
              </button>
            )}
          </div>

          {reasoning ? (
            <pre className="reasoning">
              {visibleReasoning}
              {isThinking && visibleReasoning.length < reasoning.length && (
                <span className="caret" />
              )}
            </pre>
          ) : fallbackContent ? (
            <pre className="reasoning">
              <span className="muted">
                (no reasoning trace captured — showing content)
              </span>
              {"\n"}
              {fallbackContent}
            </pre>
          ) : (
            <pre className="reasoning muted">
              (no reasoning content; orchestrator likely served by --reasoning-budget 0)
            </pre>
          )}

          {!isThinking && parsed && (
            <div className="dispatch">
              <span className={`pill ${approved ? "approved" : "rejected"}`}>
                {approved ? "✓ approved" : "✕ rejected"}
              </span>
              {(parsed.dispatch_order ?? []).length > 0 && (
                <span className="order">
                  order:{" "}
                  {(parsed.dispatch_order ?? [])
                    .map((g) => `G${g}`)
                    .join(" → ")}
                </span>
              )}
              {concerns.length > 0 && (
                <ul className="concerns">
                  {concerns.map((c, i) => (
                    <li key={i}>{c}</li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
