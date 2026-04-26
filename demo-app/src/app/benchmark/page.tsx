import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { BenchmarkBarChart } from "~/components/charts/BenchmarkBarChart";
import { ScalingLineChart } from "~/components/charts/ScalingLineChart";
import { GreenhouseComparisonChart } from "~/components/charts/GreenhouseComparisonChart";
import {
  PredictorPrecisionChart,
  PredictorRecallChart,
  PredictorWallTimeChart,
  type PredictorResults,
} from "~/components/charts/PredictorPrecisionRecallChart";
import type { BenchmarkMetrics } from "~/lib/charts/sample-benchmark";
import {
  sampleNaive,
  samplePlanned,
} from "~/lib/charts/sample-benchmark";
import {
  sampleGreenhouse as sampleGH,
  sampleDemoApp as sampleDA,
  type CodebaseScaling,
} from "~/lib/charts/sample-scaling";
import {
  sampleGreenhouseStrategies,
  type GreenhouseStrategy,
} from "~/lib/charts/sample-greenhouse";

export const metadata = { title: "ACG Benchmark Suite" };

/** Repo root is one level above demo-app/. */
const REPO_ROOT = join(process.cwd(), "..");

async function readJson<T>(path: string): Promise<T | null> {
  try {
    const text = await readFile(path, "utf-8");
    return JSON.parse(text) as T;
  } catch {
    return null;
  }
}

function coerce(v: unknown): number {
  if (typeof v === "boolean") return v ? 1 : 0;
  if (typeof v === "number") return v;
  return 0;
}

// ---------------------------------------------------------------------------
// Data loaders
// ---------------------------------------------------------------------------

async function loadBenchmark(): Promise<{
  naive: Record<string, number | boolean>;
  planned: Record<string, number | boolean>;
}> {
  const naive = await readJson<BenchmarkMetrics>(
    join(REPO_ROOT, ".acg", "run_naive.json"),
  );
  const planned = await readJson<BenchmarkMetrics>(
    join(REPO_ROOT, ".acg", "run_acg.json"),
  );
  return {
    naive: (naive as unknown as Record<string, number | boolean>) ?? sampleNaive,
    planned: (planned as unknown as Record<string, number | boolean>) ?? samplePlanned,
  };
}

interface EvalRunCombined {
  strategies: Record<
    string,
    {
      summary_metrics: {
        tokens_prompt_total?: number | null;
        tokens_orchestrator_overhead?: number | null;
        tasks_total?: number | null;
        tasks_completed?: number;
        tasks_completed_per_hour?: number;
        overlapping_write_pairs?: number;
        out_of_bounds_write_count?: number;
        [k: string]: unknown;
      };
      strategy?: string;
      [k: string]: unknown;
    }
  >;
}

function extractScaling(
  combined: EvalRunCombined | null,
  label: string,
  fallback: CodebaseScaling,
): CodebaseScaling {
  if (!combined) return fallback;
  const naive = combined.strategies?.naive_parallel?.summary_metrics;
  const planned = combined.strategies?.acg_planned?.summary_metrics;
  const nTotal = naive?.tokens_prompt_total;
  const pTotal = planned?.tokens_prompt_total;
  const nTasks = naive?.tasks_total ?? planned?.tasks_total ?? 0;
  if (!nTotal || !pTotal || !nTasks) return fallback;
  const naivePerTask = nTotal / nTasks;
  const plannedPerTask = pTotal / nTasks;
  const overhead = planned?.tokens_orchestrator_overhead ?? 0;
  const savings = naivePerTask - plannedPerTask;
  const bN = overhead > 0 && savings > 0 ? overhead / savings : 0;
  const data = Array.from({ length: 40 }, (_, i) => {
    const n = i + 1;
    return { n, naive: naivePerTask * n, planned: overhead + plannedPerTask * n };
  });
  return { label, naivePerTask, plannedPerTask, orchestratorOverhead: overhead, breakevenN: bN, data };
}

function extractGreenhouse(
  combined: EvalRunCombined | null,
  fallback: GreenhouseStrategy[],
): GreenhouseStrategy[] {
  if (!combined) return fallback;
  return Object.entries(combined.strategies).map(([strategy, val]) => {
    const sm = val.summary_metrics;
    return {
      strategy,
      tasks_completed: sm.tasks_completed ?? 0,
      tasks_completed_per_hour: sm.tasks_completed_per_hour ?? 0,
      overlapping_write_pairs: sm.overlapping_write_pairs ?? 0,
      out_of_bounds_write_count: sm.out_of_bounds_write_count ?? 0,
    };
  });
}

async function loadScaling(): Promise<{
  greenhouse: CodebaseScaling;
  demoApp: CodebaseScaling;
}> {
  const [ghCombined, daCombined] = await Promise.all([
    readJson<EvalRunCombined>(
      join(REPO_ROOT, "experiments", "greenhouse", "runs_model_gemma_local", "eval_run_combined.json"),
    ),
    readJson<EvalRunCombined>(
      join(REPO_ROOT, "experiments", "demo-app", "runs", "eval_run_combined.json"),
    ),
  ]);
  return {
    greenhouse: extractScaling(ghCombined, "greenhouse", sampleGH),
    demoApp: extractScaling(daCombined, "demo-app", sampleDA),
  };
}

async function loadGreenhouse(): Promise<GreenhouseStrategy[]> {
  const combined = await readJson<EvalRunCombined>(
    join(REPO_ROOT, "experiments", "greenhouse", "runs_model_gemma_local", "eval_run_combined.json"),
  );
  return extractGreenhouse(combined, sampleGreenhouseStrategies);
}

async function loadPredictor(): Promise<PredictorResults> {
  const results = await readJson<PredictorResults>(
    join(REPO_ROOT, "benchmark", "results.json"),
  );
  return (
    results ?? {
      base: {},
      with_embeddings: {},
    }
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default async function BenchmarkPage() {
  const [benchmarkData, scalingData, greenhouseData, predictorData] =
    await Promise.all([
      loadBenchmark(),
      loadScaling(),
      loadGreenhouse(),
      loadPredictor(),
    ]);

  return (
    <main className="min-h-screen bg-background p-6 md:p-10">
      <div className="mx-auto max-w-7xl space-y-8">
        <header>
          <h1 className="text-3xl font-bold tracking-tight text-foreground">
            ACG Benchmark Suite
          </h1>
          <p className="mt-1 text-muted-foreground">
            Interactive charts powered by{" "}
            <a
              href="https://ui.shadcn.com/charts"
              className="underline"
              target="_blank"
              rel="noopener noreferrer"
            >
              shadcn/ui charts
            </a>
            .{" "}
            <a
              href="https://github.com/SSKYAJI/cognition/blob/main/docs/CITATIONS.md"
              className="underline"
              target="_blank"
              rel="noopener noreferrer"
            >
              Citations
            </a>
          </p>
        </header>

        {/* Headline benchmark */}
        <section>
          <h2 className="mb-4 text-xl font-semibold text-foreground">
            Headline Metrics
          </h2>
          <div className="grid grid-cols-1 gap-6">
            <BenchmarkBarChart
              naive={benchmarkData.naive}
              planned={benchmarkData.planned}
            />
          </div>
        </section>

        {/* Token scaling */}
        <section>
          <h2 className="mb-4 text-xl font-semibold text-foreground">
            Token Scaling Breakeven
          </h2>
          <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
            <ScalingLineChart codebase={scalingData.greenhouse} />
            <ScalingLineChart codebase={scalingData.demoApp} />
          </div>
        </section>

        {/* Greenhouse comparison */}
        <section>
          <h2 className="mb-4 text-xl font-semibold text-foreground">
            Greenhouse Java-6 Comparison
          </h2>
          <div className="grid grid-cols-1 gap-6">
            <GreenhouseComparisonChart strategies={greenhouseData} />
          </div>
        </section>

        {/* Predictor precision / recall */}
        <section>
          <h2 className="mb-4 text-xl font-semibold text-foreground">
            Predictor Evaluation
          </h2>
          <div className="grid grid-cols-1 gap-6 md:grid-cols-2 lg:grid-cols-3">
            <PredictorPrecisionChart results={predictorData} />
            <PredictorRecallChart results={predictorData} />
            <PredictorWallTimeChart results={predictorData} />
          </div>
        </section>
      </div>
    </main>
  );
}
