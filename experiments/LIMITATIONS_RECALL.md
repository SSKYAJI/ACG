# Recall — Honest Caveats (Paper Limitations Paragraph)

These four points belong in the limitations section. Each is grounded in a specific artifact.

## 1. Saturation hides predictor differences
Three of four codebases report recall = 1.00 in report_v2.md. With saturated recall we cannot distinguish "good predictor" from "great predictor"; the only informative datapoint is Brocoders TS at 0.89.
Citation: experiments/graph_quality/report_v2.md lines 7-10.

## 2. Filename hints inflate recall
Our explicit task prompts (e.g. RealWorld tasks_explicit.json) name target files inside the prompt, which the predictor's static-seed regex captures directly. On the blind RealWorld task suite (no filename hints), overall recall drops to 0.87. The blind number is the more honest one.
Citation: experiments/realworld/runs_blind_openrouter/claim_audit.md line 42.

## 3. Recall is measured against agent proposals, not ground truth
We compute recall against actual_changed_files — the files the agent chose to write — not against a hand-curated oracle of "what the task semantically requires." If the agent under-touches files relative to a correct solution, recall looks artificially high. Round 3's real-repo evaluation (Lanes O2/O3/O4) addresses this by computing recall against historical PR ground truth.
Citation: experiments/graph_quality/report_v2.md line 31.

## 4. N=4 codebases is descriptive, not causal
Density-vs-F1 came out at Pearson -0.76, but with N=4 we cannot draw causal conclusions about graph structure and predictor quality. The figure is descriptive; we explicitly retract the original "graph quality drives plan quality" claim in Round 2.
Citation: experiments/graph_quality/report_v2_diff.md lines 22, 25.
