"use client";

import {
  Line,
  LineChart,
  CartesianGrid,
  XAxis,
  YAxis,
  ReferenceLine,
} from "recharts";
import type { ChartConfig } from "~/components/ui/chart";
import { ChartTooltip, ChartTooltipContent } from "~/components/ui/chart";
import { MetricCard, type TrendDirection } from "./MetricCard";
import type { CodebaseScaling } from "~/lib/charts/sample-scaling";

interface ScalingLineChartProps {
  codebase: CodebaseScaling;
}

const chartConfig: ChartConfig = {
  naive: {
    label: "naive_parallel",
    color: "hsl(var(--chart-1))",
  },
  planned: {
    label: "acg_planned",
    color: "hsl(var(--chart-2))",
  },
};

export function ScalingLineChart({ codebase }: ScalingLineChartProps) {
  const { label, data, breakevenN, orchestratorOverhead } = codebase;
  const perTaskSave = codebase.naivePerTask - codebase.plannedPerTask;

  const trendDirection: TrendDirection = perTaskSave > 0 ? "down" : "neutral";
  const trendText =
    perTaskSave > 0
      ? `Saves ${Math.round(perTaskSave)} tokens/task${orchestratorOverhead > 0 ? `; breakeven at N=${Math.round(breakevenN)}` : ""}`
      : "No per-task savings observed";

  return (
    <MetricCard
      title={`Token Scaling — ${label}`}
      description={`Worker prompt tokens vs number of tasks${orchestratorOverhead > 0 ? ` (overhead: ${orchestratorOverhead} tok)` : ""}`}
      trendText={trendText}
      trendDirection={trendDirection}
      config={chartConfig}
    >
      <LineChart accessibilityLayer data={data}>
        <CartesianGrid vertical={false} />
        <XAxis
          dataKey="n"
          tickLine={false}
          axisLine={false}
          tickMargin={10}
          label={{ value: "Tasks (N)", position: "insideBottom", offset: -5 }}
        />
        <YAxis
          tickLine={false}
          axisLine={false}
          tickFormatter={(v: number) =>
            v >= 1000 ? `${(v / 1000).toFixed(0)}k` : String(v)
          }
        />
        <ChartTooltip cursor={false} content={<ChartTooltipContent />} />
        {isFinite(breakevenN) && breakevenN > 0 && breakevenN <= 40 && (
          <ReferenceLine
            x={Math.round(breakevenN)}
            stroke="hsl(var(--chart-3))"
            strokeDasharray="5 5"
            label={{
              value: `Breakeven N=${Math.round(breakevenN)}`,
              position: "top",
              fill: "hsl(var(--chart-3))",
              fontSize: 11,
            }}
          />
        )}
        <Line
          type="natural"
          dataKey="naive"
          stroke="var(--color-naive)"
          strokeWidth={2}
          dot={{ fill: "var(--color-naive)" }}
          activeDot={{ r: 6 }}
        />
        <Line
          type="natural"
          dataKey="planned"
          stroke="var(--color-planned)"
          strokeWidth={2}
          dot={{ fill: "var(--color-planned)" }}
          activeDot={{ r: 6 }}
        />
      </LineChart>
    </MetricCard>
  );
}
