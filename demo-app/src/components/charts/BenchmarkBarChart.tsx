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

interface BenchmarkBarChartProps {
  naive: Record<string, number | boolean>;
  planned: Record<string, number | boolean>;
}

const METRICS: { key: string; label: string }[] = [
  { key: "overlapping_writes", label: "Overlapping writes" },
  { key: "blocked_bad_writes", label: "Blocked bad writes" },
  { key: "manual_merge_steps", label: "Manual merge steps" },
  { key: "tests_passing_first_run", label: "Tests pass 1st run" },
  { key: "wall_time_minutes", label: "Wall time (min)" },
];

function coerce(v: unknown): number {
  if (typeof v === "boolean") return v ? 1 : 0;
  if (typeof v === "number") return v;
  return 0;
}

const chartConfig: ChartConfig = {
  naive: {
    label: "Naive parallel",
    color: "hsl(var(--chart-1))",
  },
  planned: {
    label: "ACG-planned",
    color: "hsl(var(--chart-2))",
  },
};

export function BenchmarkBarChart({ naive, planned }: BenchmarkBarChartProps) {
  const data = METRICS.map(({ key, label }) => ({
    metric: label,
    naive: coerce(naive[key]),
    planned: coerce(planned[key]),
  }));

  const naiveTotal = data.reduce((s, d) => s + d.naive, 0);
  const plannedTotal = data.reduce((s, d) => s + d.planned, 0);
  const diff = naiveTotal - plannedTotal;
  const trendDirection: TrendDirection = diff > 0 ? "down" : diff < 0 ? "up" : "neutral";
  const pct = naiveTotal > 0 ? Math.round((Math.abs(diff) / naiveTotal) * 100) : 0;

  return (
    <MetricCard
      title="Agent Coordination Tax"
      description="Naive parallel vs ACG-planned — 5 headline metrics"
      trendText={`ACG reduces coordination overhead by ~${pct}%`}
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
          tickFormatter={(value: string) =>
            value.length > 12 ? value.slice(0, 12) + "…" : value
          }
        />
        <YAxis tickLine={false} axisLine={false} />
        <ChartTooltip cursor={false} content={<ChartTooltipContent />} />
        <Bar dataKey="naive" fill="var(--color-naive)" radius={4} />
        <Bar dataKey="planned" fill="var(--color-planned)" radius={4} />
      </BarChart>
    </MetricCard>
  );
}
