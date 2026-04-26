"use client";

import type { ReactNode } from "react";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "~/components/ui/card";
import {
  type ChartConfig,
  ChartContainer,
} from "~/components/ui/chart";
import { TrendingUp, TrendingDown, Minus } from "lucide-react";

export type TrendDirection = "up" | "down" | "neutral";

interface MetricCardProps {
  title: string;
  description: string;
  trendText: string;
  trendDirection: TrendDirection;
  config: ChartConfig;
  children: ReactNode;
  className?: string;
}

const trendIcons: Record<TrendDirection, typeof TrendingUp> = {
  up: TrendingUp,
  down: TrendingDown,
  neutral: Minus,
};

export function MetricCard({
  title,
  description,
  trendText,
  trendDirection,
  config,
  children,
  className,
}: MetricCardProps) {
  const Icon = trendIcons[trendDirection];
  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent>
        <ChartContainer config={config}>{children}</ChartContainer>
      </CardContent>
      <CardFooter className="flex-col items-start gap-2 text-sm">
        <div className="flex gap-2 font-medium leading-none">
          {trendText} <Icon className="h-4 w-4" />
        </div>
      </CardFooter>
    </Card>
  );
}
