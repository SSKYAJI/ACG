"""Greenhouse Java 6-era modernization eval (megaplan v0.1).

This package wires the head-to-head harness that compares ``naive_parallel``
agents against ``acg_planned`` agents on Spring Greenhouse. The single
output artifact is ``eval_run.json`` (see :mod:`eval_schema`).

Backends:

- ``mock`` — deterministic, derives "actual" writes from the lockfile's
  ``predicted_writes`` so the artifact is CI-friendly.
- ``local`` — fans out via :func:`acg.runtime.run_worker` against whatever
  ``ACG_LLM_URL`` points at (a local LLM server in the canonical setup).
- ``applied-diff`` — reads a generic sidecar that identifies per-task
  branches, heads, or worktrees, then records ``git diff --name-only`` as
  actual changed files.
- ``devin-manual`` — reads a sidecar JSON of human-collected Devin session
  outputs (used when API extraction is partial).
- ``devin-api`` — direct Devin API integration; stubbed until credentials
  and endpoint contract are confirmed.

The ``mock`` and ``local`` backends never mutate files in
``experiments/greenhouse/checkout/``; they are propose-and-validate
evaluations. ``applied-diff`` / Devin backends consume externally applied
patches and score their real diff files against the same lockfile contract.
"""
