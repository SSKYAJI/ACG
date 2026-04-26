/** Fallback scaling data when eval_run_combined.json files are absent. */

export interface ScalingDataPoint {
  n: number;
  naive: number;
  planned: number;
}

export interface CodebaseScaling {
  label: string;
  naivePerTask: number;
  plannedPerTask: number;
  orchestratorOverhead: number;
  breakevenN: number;
  data: ScalingDataPoint[];
}

function generateCurve(
  naivePerTask: number,
  plannedPerTask: number,
  overhead: number,
  maxN: number,
): ScalingDataPoint[] {
  return Array.from({ length: maxN }, (_, i) => {
    const n = i + 1;
    return {
      n,
      naive: naivePerTask * n,
      planned: overhead + plannedPerTask * n,
    };
  });
}

function breakevenN(naivePerTask: number, plannedPerTask: number, overhead: number): number {
  const savings = naivePerTask - plannedPerTask;
  if (savings <= 0) return Infinity;
  if (overhead <= 0) return 0;
  return overhead / savings;
}

export const sampleGreenhouse: CodebaseScaling = (() => {
  const naivePerTask = 641;
  const plannedPerTask = 513;
  const overhead = 0;
  return {
    label: "greenhouse",
    naivePerTask,
    plannedPerTask,
    orchestratorOverhead: overhead,
    breakevenN: breakevenN(naivePerTask, plannedPerTask, overhead),
    data: generateCurve(naivePerTask, plannedPerTask, overhead, 40),
  };
})();

export const sampleDemoApp: CodebaseScaling = (() => {
  const naivePerTask = 620;
  const plannedPerTask = 513;
  const overhead = 0;
  return {
    label: "demo-app",
    naivePerTask,
    plannedPerTask,
    orchestratorOverhead: overhead,
    breakevenN: breakevenN(naivePerTask, plannedPerTask, overhead),
    data: generateCurve(naivePerTask, plannedPerTask, overhead, 40),
  };
})();
