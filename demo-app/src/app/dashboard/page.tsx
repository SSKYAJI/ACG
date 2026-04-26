import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { auth } from "~/server/auth";
import { db } from "~/server/db";
import { Sidebar } from "~/components/Sidebar";
import { BenchmarkBarChart } from "~/components/charts/BenchmarkBarChart";
import {
  PredictorPrecisionChart,
  PredictorRecallChart,
  type PredictorResults,
} from "~/components/charts/PredictorPrecisionRecallChart";
import type { BenchmarkMetrics } from "~/lib/charts/sample-benchmark";
import {
  sampleNaive,
  samplePlanned,
} from "~/lib/charts/sample-benchmark";

export const metadata = { title: "Dashboard" };

const REPO_ROOT = join(process.cwd(), "..");

async function readJson<T>(path: string): Promise<T | null> {
  try {
    const text = await readFile(path, "utf-8");
    return JSON.parse(text) as T;
  } catch {
    return null;
  }
}

export default async function DashboardPage() {
  const session = await auth();
  void db;

  const [naive, planned, predictorResults] = await Promise.all([
    readJson<BenchmarkMetrics>(join(REPO_ROOT, ".acg", "run_naive.json")),
    readJson<BenchmarkMetrics>(join(REPO_ROOT, ".acg", "run_acg.json")),
    readJson<PredictorResults>(join(REPO_ROOT, "benchmark", "results.json")),
  ]);

  const benchNaive = (naive as unknown as Record<string, number | boolean>) ?? sampleNaive;
  const benchPlanned = (planned as unknown as Record<string, number | boolean>) ?? samplePlanned;
  const predictor: PredictorResults = predictorResults ?? {
    base: {},
    with_embeddings: {},
  };

  return (
    <div className="flex">
      <Sidebar />
      <main className="flex-1 space-y-8 p-6">
        <div>
          <h1 className="text-2xl font-semibold">Dashboard</h1>
          <p className="text-muted-foreground">
            Signed in as {session?.user?.name ?? "guest"}.
          </p>
        </div>

        <section>
          <h2 className="mb-4 text-lg font-semibold">Benchmark Overview</h2>
          <BenchmarkBarChart naive={benchNaive} planned={benchPlanned} />
        </section>

        {Object.keys(predictor.base).length > 0 && (
          <section>
            <h2 className="mb-4 text-lg font-semibold">Predictor Metrics</h2>
            <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
              <PredictorPrecisionChart results={predictor} />
              <PredictorRecallChart results={predictor} />
            </div>
          </section>
        )}
      </main>
    </div>
  );
}
