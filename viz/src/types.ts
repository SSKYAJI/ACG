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
  liveAllowed: number;
  liveBlocked: number;
  blockedJustNow: boolean;
}

// ---------------------------------------------------------------------------
// Run trace — mirrors `acg.runtime.RunResult` (see schema/run_trace.schema.json).
// ---------------------------------------------------------------------------

export interface TraceProposal {
  file: string;
  description: string;
  allowed: boolean;
  reason: string | null;
}

export interface TraceWorker {
  task_id: string;
  group_id: number;
  url: string;
  model: string;
  wall_s: number;
  completion_tokens: number;
  finish_reason: string;
  raw_content: string;
  proposals: TraceProposal[];
  allowed_count: number;
  blocked_count: number;
  error: string | null;
}

export interface TraceOrchestrator {
  url: string;
  model: string;
  wall_s: number;
  completion_tokens: number;
  finish_reason: string;
  content: string;
  reasoning_content: string;
  parsed: {
    approved?: boolean;
    concerns?: string[];
    dispatch_order?: number[];
    [key: string]: unknown;
  } | null;
}

export interface TraceGroup {
  id: number;
  type: "parallel" | "serial";
  started_at: string;
  wall_s: number;
  worker_ids: string[];
}

export interface RunTrace {
  version: string;
  generated_at: string;
  lockfile: string;
  config: {
    orch_url: string;
    orch_model: string;
    sub_url: string;
    sub_model: string;
  };
  orchestrator: TraceOrchestrator;
  workers: TraceWorker[];
  groups_executed: TraceGroup[];
  started_at: string;
  finished_at: string;
  total_wall_s: number;
}
