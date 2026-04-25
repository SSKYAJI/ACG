import type { AgentLock, Conflict, LockTask } from "../types";

interface Props {
  lock: AgentLock;
  selectedTaskId: string | null;
}

export function Sidebar({ lock, selectedTaskId }: Props) {
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
        <TaskDetail task={selected} />
      ) : (
        <ConflictsList conflicts={lock.conflicts_detected} />
      )}
    </aside>
  );
}

function TaskDetail({ task }: { task: LockTask }) {
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
