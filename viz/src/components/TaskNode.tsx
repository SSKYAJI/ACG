import { Handle, Position, type NodeProps, type Node } from "@xyflow/react";
import type { TaskNodeData } from "../types";

export type TaskFlowNode = Node<TaskNodeData, "task">;

export function TaskNode({ data }: NodeProps<TaskFlowNode>) {
  const { task, groupType, status, isSelected } = data;
  const classes = [
    "task-node",
    groupType,
    status,
    isSelected ? "selected" : "",
  ]
    .filter(Boolean)
    .join(" ");

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
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
}
