import type { RunTrace, TraceWorker } from "../types";

// Visual timeline budgets (in seconds). The recorded run takes ~30 s for the
// orchestrator's thinking pass; a 60-second demo video can't afford that, so
// we cap the per-segment visual durations and let a speed multiplier in the
// toolbar shrink things further.
const ORCH_VISUAL_S = 12;
const GROUP_VISUAL_S = 4;
// Inter-segment pauses so the user has time to read.
const ORCH_TO_GROUP_PAUSE_S = 0.6;
const GROUP_PAUSE_S = 0.4;
// Lower bound so a 0-second mock orchestrator still has a visible thinking phase.
const MIN_ORCH_VISUAL_S = 1.5;

// Length of the "blockedJustNow" pulse window that triggers the shake animation.
const BLOCKED_PULSE_S = 0.45;

export interface WorkerProgress {
  /** Cumulative count of ALLOWED proposals revealed by `t`. */
  allowed: number;
  /** Cumulative count of BLOCKED proposals revealed by `t`. */
  blocked: number;
  /** True while this worker's group is the running group. */
  isRunning: boolean;
  /** True once this worker's group has finished. */
  isDone: boolean;
  /** True for a short window after a BLOCKED proposal lands; drives the shake. */
  blockedJustNow: boolean;
}

export interface ReplayState {
  phase: "idle" | "orchestrator" | "groups" | "done";
  /** Number of characters of `reasoning_content` to render so far. */
  orchTokensVisible: number;
  /** Total characters in the reasoning string (so the UI can show progress). */
  orchTotalChars: number;
  /** Currently-running group id, or null when not in `groups` phase. */
  runningGroupId: number | null;
  /** Group ids that have finished. */
  completedGroupIds: Set<number>;
  /** Per-task progress keyed by task_id. */
  workerProgress: Record<string, WorkerProgress>;
  /** Total replay duration in seconds (post-cap, pre-speed). */
  totalDuration: number;
  /** Current logical time in seconds (post-speed). */
  tSeconds: number;
}

interface Segment {
  kind: "orch" | "group";
  startS: number;
  endS: number;
  groupId?: number;
  // For "group" segments: per-worker reveal schedule.
  reveals?: Map<string, { perProposalS: number; total: number }>;
}

interface Timeline {
  segments: Segment[];
  total: number;
  orchEnd: number;
  reasoningChars: number;
}

function buildTimeline(trace: RunTrace): Timeline {
  const segments: Segment[] = [];
  let cursor = 0;

  // Orchestrator segment.
  const orchVisual = Math.max(
    MIN_ORCH_VISUAL_S,
    Math.min(ORCH_VISUAL_S, trace.orchestrator.wall_s || MIN_ORCH_VISUAL_S),
  );
  segments.push({ kind: "orch", startS: cursor, endS: cursor + orchVisual });
  cursor += orchVisual;
  const orchEnd = cursor;
  cursor += ORCH_TO_GROUP_PAUSE_S;

  // Group segments — process in trace order (groups_executed is sorted).
  const workersByGroup = new Map<number, TraceWorker[]>();
  for (const w of trace.workers) {
    const arr = workersByGroup.get(w.group_id) ?? [];
    arr.push(w);
    workersByGroup.set(w.group_id, arr);
  }

  const sortedGroups = [...trace.groups_executed].sort((a, b) => a.id - b.id);
  for (let i = 0; i < sortedGroups.length; i++) {
    const g = sortedGroups[i];
    const workers = workersByGroup.get(g.id) ?? [];
    const visual = Math.min(GROUP_VISUAL_S, Math.max(g.wall_s || 1, 1));
    const reveals = new Map<string, { perProposalS: number; total: number }>();
    for (const w of workers) {
      const total = w.proposals.length;
      // Distribute reveals over 70% of the group's visual duration so the
      // last proposal lands a beat before the group completes.
      const window = visual * 0.7;
      const perProposalS = total > 0 ? window / total : 0;
      reveals.set(w.task_id, { perProposalS, total });
    }
    segments.push({
      kind: "group",
      startS: cursor,
      endS: cursor + visual,
      groupId: g.id,
      reveals,
    });
    cursor += visual;
    if (i < sortedGroups.length - 1) cursor += GROUP_PAUSE_S;
  }

  return {
    segments,
    total: cursor,
    orchEnd,
    reasoningChars: trace.orchestrator.reasoning_content.length,
  };
}

let _cachedTrace: RunTrace | null = null;
let _cachedTimeline: Timeline | null = null;

function getTimeline(trace: RunTrace): Timeline {
  if (trace !== _cachedTrace) {
    _cachedTrace = trace;
    _cachedTimeline = buildTimeline(trace);
  }
  return _cachedTimeline!;
}

export function totalReplayDuration(trace: RunTrace): number {
  return getTimeline(trace).total;
}

function blankProgress(trace: RunTrace): Record<string, WorkerProgress> {
  const out: Record<string, WorkerProgress> = {};
  for (const w of trace.workers) {
    out[w.task_id] = {
      allowed: 0,
      blocked: 0,
      isRunning: false,
      isDone: false,
      blockedJustNow: false,
    };
  }
  return out;
}

function fullProgress(trace: RunTrace): Record<string, WorkerProgress> {
  const out: Record<string, WorkerProgress> = {};
  for (const w of trace.workers) {
    out[w.task_id] = {
      allowed: w.allowed_count,
      blocked: w.blocked_count,
      isRunning: false,
      isDone: true,
      blockedJustNow: false,
    };
  }
  return out;
}

/**
 * Compute the visible state at logical time `tSeconds` (already speed-scaled).
 * Pure function — no React state, no DOM. The toolbar's speed selector
 * multiplies its raw RAF delta by `speed` before passing it in.
 */
export function computeReplayState(
  trace: RunTrace,
  tSeconds: number,
): ReplayState {
  const timeline = getTimeline(trace);

  if (tSeconds <= 0) {
    return {
      phase: "idle",
      orchTokensVisible: 0,
      orchTotalChars: timeline.reasoningChars,
      runningGroupId: null,
      completedGroupIds: new Set(),
      workerProgress: blankProgress(trace),
      totalDuration: timeline.total,
      tSeconds: 0,
    };
  }

  if (tSeconds >= timeline.total) {
    return {
      phase: "done",
      orchTokensVisible: timeline.reasoningChars,
      orchTotalChars: timeline.reasoningChars,
      runningGroupId: null,
      completedGroupIds: new Set(trace.groups_executed.map((g) => g.id)),
      workerProgress: fullProgress(trace),
      totalDuration: timeline.total,
      tSeconds: timeline.total,
    };
  }

  // Locate the active segment.
  const active = timeline.segments.find(
    (s) => tSeconds >= s.startS && tSeconds < s.endS,
  );

  // Orchestrator phase: typewriter the reasoning_content over the orch segment.
  if (active?.kind === "orch") {
    const segLen = active.endS - active.startS;
    const segT = (tSeconds - active.startS) / segLen;
    return {
      phase: "orchestrator",
      orchTokensVisible: Math.floor(segT * timeline.reasoningChars),
      orchTotalChars: timeline.reasoningChars,
      runningGroupId: null,
      completedGroupIds: new Set(),
      workerProgress: blankProgress(trace),
      totalDuration: timeline.total,
      tSeconds,
    };
  }

  // Either we're in a group segment, or in a pause between segments.
  const completed = new Set<number>();
  let runningGroupId: number | null = null;
  const progress = blankProgress(trace);

  for (const seg of timeline.segments) {
    if (seg.kind !== "group") continue;
    const groupId = seg.groupId!;
    if (tSeconds >= seg.endS) {
      // This group already finished.
      completed.add(groupId);
      for (const w of trace.workers) {
        if (w.group_id !== groupId) continue;
        progress[w.task_id] = {
          allowed: w.allowed_count,
          blocked: w.blocked_count,
          isRunning: false,
          isDone: true,
          blockedJustNow: false,
        };
      }
    } else if (tSeconds >= seg.startS) {
      // This is the running group.
      runningGroupId = groupId;
      const localT = tSeconds - seg.startS;
      for (const w of trace.workers) {
        if (w.group_id !== groupId) continue;
        const reveal = seg.reveals!.get(w.task_id);
        const perS = reveal?.perProposalS ?? 0;
        const revealedCount =
          perS > 0 ? Math.min(w.proposals.length, Math.floor(localT / perS)) : 0;

        let allowed = 0;
        let blocked = 0;
        let lastBlockedAt = -Infinity;
        for (let i = 0; i < revealedCount; i++) {
          const p = w.proposals[i];
          if (p.allowed) allowed += 1;
          else {
            blocked += 1;
            lastBlockedAt = seg.startS + (i + 1) * perS;
          }
        }
        const blockedJustNow = tSeconds - lastBlockedAt < BLOCKED_PULSE_S;
        progress[w.task_id] = {
          allowed,
          blocked,
          isRunning: true,
          isDone: false,
          blockedJustNow,
        };
      }
    }
  }

  // Orchestrator phase has finished; reasoning is fully revealed.
  return {
    phase: "groups",
    orchTokensVisible: timeline.reasoningChars,
    orchTotalChars: timeline.reasoningChars,
    runningGroupId,
    completedGroupIds: completed,
    workerProgress: progress,
    totalDuration: timeline.total,
    tSeconds,
  };
}
