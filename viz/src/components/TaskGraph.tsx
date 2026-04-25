import { useEffect, useMemo } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  MarkerType,
  type Edge,
  useNodesState,
  useEdgesState,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import type { AgentLock, TaskNodeData, TaskStatus } from "../types";
import type { WorkerProgress } from "../lib/replay";
import { computeLayout } from "../lib/layout";
import { TaskNode, type TaskFlowNode } from "./TaskNode";

const nodeTypes = { task: TaskNode };

interface Props {
  lock: AgentLock;
  selectedTaskId: string | null;
  runningGroup: number | null;
  completedGroups: Set<number>;
  workerProgress: Record<string, WorkerProgress>;
  onSelect: (id: string | null) => void;
}

export function TaskGraph({
  lock,
  selectedTaskId,
  runningGroup,
  completedGroups,
  workerProgress,
  onSelect,
}: Props) {
  const positions = useMemo(() => computeLayout(lock), [lock]);

  const computedNodes: TaskFlowNode[] = useMemo(() => {
    return lock.tasks.map((task) => {
      const group = lock.execution_plan.groups.find((g) =>
        g.tasks.includes(task.id),
      );
      const groupType: "parallel" | "serial" = group?.type ?? "parallel";
      let status: TaskStatus = "idle";
      if (group && completedGroups.has(group.id)) status = "done";
      else if (group && runningGroup === group.id) status = "running";

      const wp = workerProgress[task.id];
      const data: TaskNodeData = {
        task,
        groupType,
        status,
        isSelected: task.id === selectedTaskId,
        liveAllowed: wp?.allowed ?? 0,
        liveBlocked: wp?.blocked ?? 0,
        blockedJustNow: wp?.blockedJustNow ?? false,
      };

      return {
        id: task.id,
        type: "task",
        position: positions[task.id] ?? { x: 0, y: 0 },
        data,
      };
    });
  }, [
    lock,
    positions,
    selectedTaskId,
    runningGroup,
    completedGroups,
    workerProgress,
  ]);

  const computedEdges: Edge[] = useMemo(() => {
    const edges: Edge[] = [];

    // Dependency edges (depends_on).
    for (const task of lock.tasks) {
      for (const dep of task.depends_on) {
        edges.push({
          id: `dep-${dep}-${task.id}`,
          source: dep,
          target: task.id,
          type: "smoothstep",
          style: { stroke: "#7d8590", strokeWidth: 2 },
          markerEnd: { type: MarkerType.ArrowClosed, color: "#7d8590" },
        });
      }
    }

    // Conflict edges (dashed, animated, red).
    for (const conflict of lock.conflicts_detected) {
      const [a, b] = conflict.between_tasks;
      const id = `conf-${a}-${b}`;
      if (edges.some((e) => e.id === id)) continue;
      edges.push({
        id,
        source: a,
        target: b,
        type: "smoothstep",
        animated: true,
        label: conflict.files.join(", "),
        labelStyle: { fill: "#ff7b72", fontSize: 10, fontWeight: 600 },
        labelBgStyle: { fill: "#0b0d12" },
        labelBgPadding: [4, 4],
        labelBgBorderRadius: 4,
        style: {
          stroke: "#f85149",
          strokeWidth: 2,
          strokeDasharray: "6 4",
        },
        markerEnd: { type: MarkerType.ArrowClosed, color: "#f85149" },
      });
    }

    return edges;
  }, [lock]);

  const [nodes, setNodes, onNodesChange] = useNodesState<TaskFlowNode>(
    computedNodes,
  );
  const [edges, , onEdgesChange] = useEdgesState<Edge>(computedEdges);

  // Re-sync nodes when external state changes (selection / execution status).
  useEffect(() => {
    setNodes(computedNodes);
  }, [computedNodes, setNodes]);

  return (
    <div className="canvas">
      <ReactFlow
        nodes={nodes as TaskFlowNode[]}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        nodeTypes={nodeTypes}
        onNodeClick={(_, node) => onSelect(node.id)}
        onPaneClick={() => onSelect(null)}
        fitView
        fitViewOptions={{ padding: 0.25 }}
        minZoom={0.2}
        maxZoom={1.5}
        proOptions={{ hideAttribution: true }}
      >
        <Background color="#1f242c" gap={24} />
        <MiniMap
          nodeColor={(n) => {
            const data = n.data as TaskNodeData | undefined;
            if (data?.status === "running") return "#d29922";
            if (data?.status === "done") return "#2ea043";
            return "#1f6feb";
          }}
          maskColor="rgba(11,13,18,0.85)"
          pannable
          zoomable
        />
        <Controls />
      </ReactFlow>
    </div>
  );
}
