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
import type { GreenhouseStrategy } from "~/lib/charts/sample-greenhouse";

interface GreenhouseComparisonChartProps {
  strategies: GreenhouseStrategy[];
}

const METRICS: { key: keyof GreenhouseStrategy; label: string }[] = [
  { key: "tasks_completed", label: "Tasks completed" },
  { key: "tasks_completed_per_hour", label: "Tasks/hour" },
  { key: "overlapping_write_pairs", label: "Overlap pairs" },
  { key: "out_of_bounds_write_count", label: "OOB writes" },
];

const STRATEGY_COLORS: Record<string, string> = {
  naive_parallel: "hsl(var(--chart-1))",
  acg_planned: "hsl(var(--chart-2))",
  devin: "hsl(var(--chart-3))",
};

function strategyLabel(s: string): string {
  return s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function GreenhouseComparisonChart({
  strategies,
}: GreenhouseComparisonChartProps) {
  const chartConfig: ChartConfig = Object.fromEntries(
    strategies.map((s, i) => [
      s.strategy,
      {
        label: strategyLabel(s.strategy),
        color:
          STRATEGY_COLORS[s.strategy] ?? `hsl(var(--chart-${(i % 5) + 1}))`,
      },
    ]),
  );

  const data = METRICS.map(({ key, label }) => {
    const row: Record<string, string | number> = { metric: label };
    for (const s of strategies) {
      row[s.strategy] = Number(s[key]) || 0;
    }
    return row;
  });

  const naive = strategies.find((s) => s.strategy === "naive_parallel");
  const planned = strategies.find((s) => s.strategy === "acg_planned");
  let trendDirection: TrendDirection = "neutral";
  let trendText = "Compare strategies on the Greenhouse Java-6 benchmark";
  if (naive && planned) {
    const naiveOverlaps = naive.overlapping_write_pairs;
    const plannedOverlaps = planned.overlapping_write_pairs;
    if (naiveOverlaps > plannedOverlaps) {
      trendDirection = "down";
      trendText = `ACG eliminates ${naiveOverlaps - plannedOverlaps} overlapping write pair${naiveOverlaps - plannedOverlaps !== 1 ? "s" : ""}`;
    }
  }

  return (
    <MetricCard
      title="Greenhouse Java-6 Comparison"
      description="ACG vs naive parallel — modernization benchmark"
      trendText={trendText}
      trendDirection={trendDirection}
      config={chartConfig}
    >
      <BarChart accessibilityLayer data={data}>
        <CartesianGrid vertical={false} />
        <XAxis
          dataKey="metric"
          tickLine={false}
          tickMargin={10}
          axisLine={false}
        />
        <YAxis tickLine={false} axisLine={false} />
        <ChartTooltip cursor={false} content={<ChartTooltipContent />} />
        {strategies.map((s) => (
          <Bar
            key={s.strategy}
            dataKey={s.strategy}
            fill={`var(--color-${s.strategy})`}
            radius={4}
          />
        ))}
      </BarChart>
    </MetricCard>
  );
}
