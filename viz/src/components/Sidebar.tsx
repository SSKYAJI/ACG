import type {
  AgentLock,
  Conflict,
  LockTask,
  TraceProposal,
  TraceWorker,
} from "../types";
import type { WorkerProgress } from "../lib/replay";

interface Props {
  lock: AgentLock;
  selectedTaskId: string | null;
  worker?: TraceWorker;
  workerProgress?: WorkerProgress;
}

export function Sidebar({
  lock,
  selectedTaskId,
  worker,
  workerProgress,
}: Props) {
  const selected = selectedTaskId
    ? lock.tasks.find((t) => t.id === selectedTaskId) ?? null
    : null;

  return (
    <aside className="sidebar">
      <div className="section">
        <h2>Lockfile</h2>
        <div className="meta-line">
          v{lock.version} · {lock.tasks.length} tasks ·{" "}
          {lock.execution_plan.groups.length} groups
        </div>
        <div className="meta-line">repo: {lock.repo.root}</div>
        <div className="meta-line">
          languages: {lock.repo.languages.join(", ") || "—"}
        </div>
        <div className="meta-line">model: {lock.generator.model}</div>
      </div>

      {selected ? (
        <TaskDetail
          task={selected}
          worker={worker}
          workerProgress={workerProgress}
        />
      ) : (
        <ConflictsList conflicts={lock.conflicts_detected} />
      )}
    </aside>
  );
}

function TaskDetail({
  task,
  worker,
  workerProgress,
}: {
  task: LockTask;
  worker?: TraceWorker;
  workerProgress?: WorkerProgress;
}) {
  return (
    <div className="section">
      <h2>Task</h2>
      <h3>{task.id}</h3>

      <h2 style={{ marginTop: 12 }}>Prompt</h2>
      <p>{task.prompt}</p>

      <h2>Predicted writes</h2>
      {task.predicted_writes.length === 0 ? (
        <div className="meta-line">none</div>
      ) : (
        task.predicted_writes.map((w) => (
          <span className="file" key={w.path} title={w.reason}>
            <span>{w.path}</span>
            <span className="conf">{(w.confidence * 100).toFixed(0)}%</span>
          </span>
        ))
      )}

      <h2 style={{ marginTop: 18 }}>Allowed paths</h2>
      {task.allowed_paths.map((p) => (
        <span className="file" key={p}>
          <span>{p}</span>
        </span>
      ))}

      {task.depends_on.length > 0 && (
        <>
          <h2 style={{ marginTop: 18 }}>Depends on</h2>
          {task.depends_on.map((d) => (
            <span className="pill" key={d}>
              {d}
            </span>
          ))}
        </>
      )}

      {worker && <WorkerProposals worker={worker} progress={workerProgress} />}
    </div>
  );
}

function WorkerProposals({
  worker,
  progress,
}: {
  worker: TraceWorker;
  progress?: WorkerProgress;
}) {
  // Reveal only the proposals already surfaced by the replay engine.
  const revealed =
    progress?.isDone || (!progress?.isRunning && (progress?.allowed ?? 0) === 0 && (progress?.blocked ?? 0) === 0)
      ? worker.proposals
      : sliceProposals(worker.proposals, progress);

  return (
    <>
      <h2 style={{ marginTop: 22 }}>
        Proposals (live)
        {worker.error && <span className="pill rejected">error</span>}
      </h2>
      <div className="meta-line">
        {worker.allowed_count} allowed · {worker.blocked_count} blocked ·{" "}
        {worker.wall_s.toFixed(2)}s · {worker.completion_tokens} tokens
      </div>
      {worker.error && (
        <div className="conflict" style={{ marginTop: 8 }}>
          <span className="conflict-files">{worker.error}</span>
        </div>
      )}
      {revealed.length === 0 ? (
        <div className="meta-line" style={{ marginTop: 8 }}>
          {progress?.isRunning ? "(streaming…)" : "no proposals"}
        </div>
      ) : (
        <div className="proposals">
          {revealed.map((p, i) => (
            <ProposalRow key={`${p.file}-${i}`} proposal={p} />
          ))}
        </div>
      )}

      <details className="raw-reply">
        <summary>raw worker reply</summary>
        <pre>{worker.raw_content || "(empty)"}</pre>
      </details>
    </>
  );
}

function sliceProposals(
  all: TraceProposal[],
  progress?: WorkerProgress,
): TraceProposal[] {
  if (!progress) return all;
  const total = (progress.allowed ?? 0) + (progress.blocked ?? 0);
  return all.slice(0, total);
}

function ProposalRow({ proposal }: { proposal: TraceProposal }) {
  return (
    <div
      className={`proposal ${proposal.allowed ? "allowed" : "blocked"}`}
      title={proposal.reason ?? ""}
    >
      <div className="proposal-line">
        <span className="proposal-mark">{proposal.allowed ? "✓" : "✕"}</span>
        <span className="proposal-file">{proposal.file}</span>
      </div>
      {proposal.description && (
        <div className="proposal-desc">{proposal.description}</div>
      )}
      {!proposal.allowed && proposal.reason && (
        <div className="proposal-reason">{proposal.reason}</div>
      )}
    </div>
  );
}

function ConflictsList({ conflicts }: { conflicts: Conflict[] }) {
  if (conflicts.length === 0) {
    return (
      <div className="section">
        <h2>Conflicts</h2>
        <div className="meta-line">No conflicts detected.</div>
      </div>
    );
  }
  return (
    <div className="section">
      <h2>Conflicts ({conflicts.length})</h2>
      {conflicts.map((c, i) => (
        <div className="conflict" key={i}>
          <span className="pill">{c.between_tasks.join(" ⨯ ")}</span>
          {c.files.map((f) => (
            <span className="conflict-files" key={f}>
              {f}
            </span>
          ))}
          <div className="resolution">{c.resolution}</div>
        </div>
      ))}
      <div className="meta-line" style={{ marginTop: 12 }}>
        Click a node to inspect its predicted writes and allowed paths.
      </div>
    </div>
  );
}
