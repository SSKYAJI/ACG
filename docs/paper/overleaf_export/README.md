# Overleaf Figure Bundle

This folder is a clean upload bundle for your paper figures.

## Files

- `figures_for_overleaf.tex`
  Ready-to-paste LaTeX figure blocks with captions and labels.
- `figures/acg_workflow.png`
  New architecture-style figure matching the paper theme.
- `figures/scaling_breakeven.png`
  Existing cross-codebase token-savings figure.
- `figures/parallelism_sweep_brocoders.png`
  Existing wall-time vs conflict-control tradeoff figure.
- `figures/fastify_scope_decomposition.png`
  New predictor-vs-agent decomposition figure for the Fastify section.

## Minimal preamble additions

```tex
\usepackage{graphicx}
\graphicspath{{figures/}}
```

## Suggested placement

1. `acg_workflow.png`
   After the introduction or at the start of the methodology / empirical setup.
2. `scaling_breakeven.png`
   In `Token Efficiency`.
3. `parallelism_sweep_brocoders.png`
   In `Validator Efficacy and Conflict Control`.
4. `fastify_scope_decomposition.png`
   In `Predictor vs. Agent: Where the Ceiling Really Is`.

## Upload flow for Overleaf

1. Upload the contents of this folder to your Overleaf project root.
2. Copy the preamble lines above into your main `.tex` file.
3. Paste the contents of `figures_for_overleaf.tex` into the body of your paper where needed, or `\input{figures_for_overleaf}`.
