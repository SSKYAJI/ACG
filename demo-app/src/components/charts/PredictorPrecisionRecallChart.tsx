"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  XAxis,
  YAxis,
} from "recharts";
import type { ChartConfig } from "~/components/ui/chart";
import { ChartTooltip, ChartTooltipContent } from "~/components/ui/chart";
import { MetricCard, type TrendDirection } from "./MetricCard";

export interface PredictorResults {
  base: Record<string, { "precision@5": number; "recall@5": number; wall_s: number }>;
  with_embeddings: Record<
    string,
    { "precision@5": number; "recall@5": number; wall_s: number }
  >;
}

interface PredictorPrecisionRecallChartProps {
  results: PredictorResults;
}

const chartConfig: ChartConfig = {
  base: {
    label: "Base",
    color: "hsl(var(--chart-1))",
  },
  with_embeddings: {
    label: "With Embeddings",
    color: "hsl(var(--chart-2))",
  },
};

function buildData(
  results: PredictorResults,
  metricKey: "precision@5" | "recall@5" | "wall_s",
) {
  const fixtures = [
    ...new Set([
      ...Object.keys(results.base),
      ...Object.keys(results.with_embeddings),
    ]),
  ];
  return fixtures.map((fixture) => ({
    fixture,
    base: Number((results.base[fixture]?.[metricKey] ?? 0).toFixed(3)),
    with_embeddings: Number(
      (results.with_embeddings[fixture]?.[metricKey] ?? 0).toFixed(3),
    ),
  }));
}

function computeTrend(results: PredictorResults): {
  direction: TrendDirection;
  text: string;
} {
  const fixtures = Object.keys(results.base);
  const avgBaseRecall =
    fixtures.reduce(
      (s, f) => s + (results.base[f]?.["recall@5"] ?? 0),
      0,
    ) / (fixtures.length || 1);
  const avgEmbRecall =
    fixtures.reduce(
      (s, f) => s + (results.with_embeddings[f]?.["recall@5"] ?? 0),
      0,
    ) / (fixtures.length || 1);
  const diff = avgEmbRecall - avgBaseRecall;
  if (Math.abs(diff) < 0.01) {
    return {
      direction: "neutral",
      text: `Avg recall@5 is ${(avgBaseRecall * 100).toFixed(0)}% across ${fixtures.length} fixtures`,
    };
  }
  return {
    direction: diff > 0 ? "up" : "down",
    text: `Embeddings ${diff > 0 ? "boost" : "reduce"} avg recall@5 by ${(Math.abs(diff) * 100).toFixed(1)}pp`,
  };
}

export function PredictorPrecisionChart({
  results,
}: PredictorPrecisionRecallChartProps) {
  const data = buildData(results, "precision@5");
  const avgPrecision =
    data.reduce((s, d) => s + d.base, 0) / (data.length || 1);

  return (
    <MetricCard
      title="Predictor Precision@5"
      description="Write-set prediction precision across fixtures"
      trendText={`Avg precision@5: ${(avgPrecision * 100).toFixed(0)}%`}
      trendDirection="neutral"
      config={chartConfig}
    >
      <BarChart accessibilityLayer data={data}>
        <CartesianGrid vertical={false} />
        <XAxis
          dataKey="fixture"
          tickLine={false}
          tickMargin={10}
          axisLine={false}
        />
        <YAxis
          tickLine={false}
          axisLine={false}
          domain={[0, 1]}
          tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`}
        />
        <ChartTooltip cursor={false} content={<ChartTooltipContent />} />
        <Bar dataKey="base" fill="var(--color-base)" radius={4} />
        <Bar
          dataKey="with_embeddings"
          fill="var(--color-with_embeddings)"
          radius={4}
        />
      </BarChart>
    </MetricCard>
  );
}

export function PredictorRecallChart({
  results,
}: PredictorPrecisionRecallChartProps) {
  const data = buildData(results, "recall@5");
  const { direction, text } = computeTrend(results);

  return (
    <MetricCard
      title="Predictor Recall@5"
      description="Write-set prediction recall across fixtures"
      trendText={text}
      trendDirection={direction}
      config={chartConfig}
    >
      <BarChart accessibilityLayer data={data}>
        <CartesianGrid vertical={false} />
        <XAxis
          dataKey="fixture"
          tickLine={false}
          tickMargin={10}
          axisLine={false}
        />
        <YAxis
          tickLine={false}
          axisLine={false}
          domain={[0, 1]}
          tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`}
        />
        <ChartTooltip cursor={false} content={<ChartTooltipContent />} />
        <Bar dataKey="base" fill="var(--color-base)" radius={4} />
        <Bar
          dataKey="with_embeddings"
          fill="var(--color-with_embeddings)"
          radius={4}
        />
      </BarChart>
    </MetricCard>
  );
}

export function PredictorWallTimeChart({
  results,
}: PredictorPrecisionRecallChartProps) {
  const data = buildData(results, "wall_s");
  const avgBase =
    data.reduce((s, d) => s + d.base, 0) / (data.length || 1);
  const avgEmb =
    data.reduce((s, d) => s + d.with_embeddings, 0) / (data.length || 1);
  const diff = avgEmb - avgBase;

  return (
    <MetricCard
      title="Predictor Wall Time"
      description="Seconds per prediction run across fixtures"
      trendText={`Embeddings add ~${Math.abs(diff).toFixed(1)}s avg overhead`}
      trendDirection={diff > 0 ? "up" : "down"}
      config={chartConfig}
    >
      <BarChart accessibilityLayer data={data}>
        <CartesianGrid vertical={false} />
        <XAxis
          dataKey="fixture"
          tickLine={false}
          tickMargin={10}
          axisLine={false}
        />
        <YAxis
          tickLine={false}
          axisLine={false}
          tickFormatter={(v: number) => `${v.toFixed(1)}s`}
        />
        <ChartTooltip cursor={false} content={<ChartTooltipContent />} />
        <Bar dataKey="base" fill="var(--color-base)" radius={4} />
        <Bar
          dataKey="with_embeddings"
          fill="var(--color-with_embeddings)"
          radius={4}
        />
      </BarChart>
    </MetricCard>
  );
}
