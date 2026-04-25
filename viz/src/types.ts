// Mirrors acg.schema (Pydantic) and schema/agent_lock.schema.json.

export interface PredictedWrite {
  path: string;
  confidence: number;
  reason: string;
}

export interface LockTask {
  id: string;
  prompt: string;
  predicted_writes: PredictedWrite[];
  allowed_paths: string[];
  depends_on: string[];
  parallel_group: number | null;
  rationale: string | null;
}

export interface LockGroup {
  id: number;
  tasks: string[];
  type: "parallel" | "serial";
  waits_for: number[];
}

export interface Conflict {
  files: string[];
  between_tasks: string[];
  resolution: string;
}

export interface Generator {
  tool: string;
  version: string;
  model: string;
}

export interface Repo {
  root: string;
  languages: string[];
  git_url?: string | null;
  commit?: string | null;
}

export interface AgentLock {
  version: string;
  generated_at: string;
  generator: Generator;
  repo: Repo;
  tasks: LockTask[];
  execution_plan: { groups: LockGroup[] };
  conflicts_detected: Conflict[];
}

export type TaskStatus = "idle" | "running" | "done";

export interface TaskNodeData extends Record<string, unknown> {
  task: LockTask;
  groupType: "parallel" | "serial";
  status: TaskStatus;
  isSelected: boolean;
}
