import { Handle, Position, type NodeProps, type Node } from "@xyflow/react";
import type { TaskNodeData } from "../types";

export type TaskFlowNode = Node<TaskNodeData, "task">;

export function TaskNode({ data }: NodeProps<TaskFlowNode>) {
  const {
    task,
    groupType,
    status,
    isSelected,
    liveAllowed,
    liveBlocked,
    blockedJustNow,
  } = data;
  const classes = [
    "task-node",
    groupType,
    status,
    isSelected ? "selected" : "",
    blockedJustNow ? "shake" : "",
  ]
    .filter(Boolean)
    .join(" ");

  const showLive = status !== "idle" && (liveAllowed > 0 || liveBlocked > 0);

  return (
    <div className={classes}>
      <Handle type="target" position={Position.Top} />
      <div className="header">
        <span className="id">{task.id}</span>
        <span className="group-badge">G{task.parallel_group ?? "?"}</span>
      </div>
      <div className="stats">
        <span>
          <strong>{task.predicted_writes.length}</strong> writes
        </span>
        <span>
          <strong>{task.allowed_paths.length}</strong> paths
        </span>
        {task.depends_on.length > 0 && (
          <span>
            <strong>↑ {task.depends_on.length}</strong> deps
          </span>
        )}
      </div>
      {showLive && (
        <div className="badges">
          <span className="badge allowed" title="ALLOWED proposals">
            ✓ {liveAllowed}
          </span>
          <span
            className={`badge blocked${liveBlocked > 0 ? " active" : ""}`}
            title="BLOCKED proposals (writes outside allowed_paths)"
          >
            ✕ {liveBlocked}
          </span>
        </div>
      )}
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
}
