import type { AgentLock } from "../types";

const COLUMN_WIDTH = 320;
const ROW_HEIGHT = 200;
const X_OFFSET = 80;
const Y_OFFSET = 80;

/**
 * Top-down layout: each execution group becomes one horizontal row, with
 * tasks within a group spread horizontally. Smaller groups are centred
 * against the largest group so the DAG reads cleanly top-to-bottom.
 */
export function computeLayout(
  lock: AgentLock,
): Record<string, { x: number; y: number }> {
  const positions: Record<string, { x: number; y: number }> = {};
  const groups = [...lock.execution_plan.groups].sort((a, b) => a.id - b.id);
  const maxGroupSize = Math.max(1, ...groups.map((g) => g.tasks.length));

  for (const group of groups) {
    const y = Y_OFFSET + (group.id - 1) * ROW_HEIGHT;
    const tasks = [...group.tasks].sort();
    const xOffset = ((maxGroupSize - tasks.length) * COLUMN_WIDTH) / 2;
    tasks.forEach((id, idx) => {
      positions[id] = { x: X_OFFSET + xOffset + idx * COLUMN_WIDTH, y };
    });
  }

  return positions;
}
