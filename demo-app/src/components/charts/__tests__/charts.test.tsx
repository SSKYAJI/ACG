/**
 * Smoke tests for the shadcn chart components.
 *
 * These verify that each chart renders without throwing when given sample data.
 * Run via: npx jest --config jest.config.cjs src/components/charts/__tests__/charts.test.tsx
 */
import React from "react";
import { render, screen } from "@testing-library/react";
import { BenchmarkBarChart } from "../BenchmarkBarChart";
import { ScalingLineChart } from "../ScalingLineChart";
import { GreenhouseComparisonChart } from "../GreenhouseComparisonChart";
import {
  PredictorPrecisionChart,
  PredictorRecallChart,
  PredictorWallTimeChart,
} from "../PredictorPrecisionRecallChart";
import { sampleNaive, samplePlanned } from "~/lib/charts/sample-benchmark";
import { sampleGreenhouse } from "~/lib/charts/sample-scaling";
import { sampleGreenhouseStrategies } from "~/lib/charts/sample-greenhouse";

// recharts requires a DOM container with a non-zero size. Mock
// ResponsiveContainer to sidestep the headless-browser sizing issue.
jest.mock("recharts", () => {
  const OriginalModule =
    jest.requireActual<typeof import("recharts")>("recharts");
  return {
    ...OriginalModule,
    ResponsiveContainer: ({ children }: { children: React.ReactNode }) =>
      React.createElement("div", { style: { width: 800, height: 400 } }, children),
  };
});

const samplePredictor = {
  base: {
    "demo-app": { "precision@5": 0.325, "recall@5": 0.729, wall_s: 0.2 },
    express: { "precision@5": 0.225, "recall@5": 0.521, wall_s: 2.3 },
  },
  with_embeddings: {
    "demo-app": { "precision@5": 0.325, "recall@5": 0.729, wall_s: 1.5 },
    express: { "precision@5": 0.225, "recall@5": 0.521, wall_s: 3.1 },
  },
};

describe("BenchmarkBarChart", () => {
  it("renders the card title", () => {
    render(<BenchmarkBarChart naive={sampleNaive} planned={samplePlanned} />);
    expect(screen.getByText("Agent Coordination Tax")).toBeTruthy();
  });
});

describe("ScalingLineChart", () => {
  it("renders the card title", () => {
    render(<ScalingLineChart codebase={sampleGreenhouse} />);
    expect(screen.getByText(/Token Scaling/)).toBeTruthy();
  });
});

describe("GreenhouseComparisonChart", () => {
  it("renders the card title", () => {
    render(
      <GreenhouseComparisonChart strategies={sampleGreenhouseStrategies} />,
    );
    expect(screen.getByText("Greenhouse Java-6 Comparison")).toBeTruthy();
  });
});

describe("PredictorPrecisionChart", () => {
  it("renders the card title", () => {
    render(<PredictorPrecisionChart results={samplePredictor} />);
    expect(screen.getByText("Predictor Precision@5")).toBeTruthy();
  });
});

describe("PredictorRecallChart", () => {
  it("renders the card title", () => {
    render(<PredictorRecallChart results={samplePredictor} />);
    expect(screen.getByText("Predictor Recall@5")).toBeTruthy();
  });
});

describe("PredictorWallTimeChart", () => {
  it("renders the card title", () => {
    render(<PredictorWallTimeChart results={samplePredictor} />);
    expect(screen.getByText("Predictor Wall Time")).toBeTruthy();
  });
});
