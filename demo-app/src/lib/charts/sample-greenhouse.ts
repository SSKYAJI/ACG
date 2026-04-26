/** Fallback Greenhouse comparison data when eval_run files are absent. */

export interface GreenhouseStrategy {
  strategy: string;
  tasks_completed: number;
  tasks_completed_per_hour: number;
  overlapping_write_pairs: number;
  out_of_bounds_write_count: number;
}

export const sampleGreenhouseStrategies: GreenhouseStrategy[] = [
  {
    strategy: "naive_parallel",
    tasks_completed: 3,
    tasks_completed_per_hour: 6.0,
    overlapping_write_pairs: 2,
    out_of_bounds_write_count: 1,
  },
  {
    strategy: "acg_planned",
    tasks_completed: 3,
    tasks_completed_per_hour: 4.8,
    overlapping_write_pairs: 0,
    out_of_bounds_write_count: 0,
  },
];
