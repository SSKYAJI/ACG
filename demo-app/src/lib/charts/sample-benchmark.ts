/** Fallback data matching the README table when .acg/run_*.json files are absent. */

export interface BenchmarkMetrics {
  overlapping_writes: number;
  blocked_bad_writes: number;
  manual_merge_steps: number;
  tests_passing_first_run: boolean | number;
  wall_time_minutes: number;
}

export const sampleNaive: BenchmarkMetrics = {
  overlapping_writes: 4,
  blocked_bad_writes: 0,
  manual_merge_steps: 4,
  tests_passing_first_run: false,
  wall_time_minutes: 20,
};

export const samplePlanned: BenchmarkMetrics = {
  overlapping_writes: 1,
  blocked_bad_writes: 2,
  manual_merge_steps: 0,
  tests_passing_first_run: true,
  wall_time_minutes: 13,
};
