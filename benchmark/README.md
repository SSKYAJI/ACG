# `benchmark/`

Recall/precision evaluation harness for the deterministic indexer fusion in
`acg.index.aggregate`. Run with:

```bash
./.venv/bin/python benchmark/predictor_eval.py
```

This will (re-)evaluate five fixture datasets — `demo-app`, `t3-app`,
`express`, `fastapi-template`, and `click` — and overwrite
`benchmark/results.json`.

## `results.json` schema (PR 7+)

Starting with the local-embeddings indexer ablation, `results.json` is split
into two top-level keys:

```json
{
  "base": { "demo-app": { "recall@5": ..., "precision@5": ..., "wall_s": ... }, ... },
  "with_embeddings": { "demo-app": { ... }, ... }
}
```

`base` is the four-indexer fusion (`framework + pagerank + bm25 + cochange`)
that ships in `_default_indexers()` without the `ACG_INDEX_EMBEDDINGS=1`
opt-in.  `with_embeddings` adds `EmbeddingsIndexer` to the first pass and is
populated only when the optional `index-vector` extra is installed
(`pip install -e '.[index-vector]'`); otherwise the harness leaves it as an
empty object and prints a `# embeddings extra not installed` note instead of
crashing.

`predictor_eval.py main()` prints two markdown tables (Base / With
embeddings), and the latter includes a per-dataset `Δrecall@5` column so
regressions are obvious at a glance. The "Δrecall@5 ≥ 0 on ≥ 2/3 fixtures"
gate is the correctness floor for the embeddings indexer — see
`docs/plans/acg-index-rewrite.md` §5 for the roadmap entry.

To generate a figure from the current benchmark JSON:

```bash
./.venv/bin/python benchmark/plot_results.py \
  --results benchmark/results.json \
  --out docs/benchmark_predictor.png
```

The output plot includes:

- recall / precision / F1 for every dataset
- Python-only secondary metrics (`conflicts_detected`,
  `blocked_bad_write_rate`) for `fastapi-template` and `click`
