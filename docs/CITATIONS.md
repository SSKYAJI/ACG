# Citations

Every citation must be verified verbatim before the demo video is recorded. Open each URL, find the quoted text, and update the **Status** column to one of:

- **verified** — quote matches the source exactly (or trivially up to whitespace).
- **paraphrased** — close enough to be defensible but not identical; soften the README and video language for this claim before recording.
- **missing** — URL 404 / quote not findable; remove the claim entirely.

Retrieved date is the day you opened the URL. Owner is whoever did the verification.

| #   | Source URL                                                                             | Claim / quote                                                                                                                                                                                                                                                               | Status                  | Retrieved  | Owner  |
| --- | -------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------- | ---------- | ------ |
| 1   | https://arxiv.org/abs/2510.18893 (CodeCRDT, Oct 2025)                                  | _"Future work should use static analysis (data-flow graphs, shared variable access patterns) for objective coupling measurement."_ Also reports preliminary 5–10% semantic conflicts and lists semantic conflict detection via LSP / AST-level coordination as future work. | verified from local PDF | 2026-04-25 | Prajit |
| 2   | https://github.com/anomalyco/opencode/issues/4278                                      | _"Multiple OpenCode clients and/or agents don't stomp on each other's changes… Running multiple agents/tools in parallel that all use OpenCode can easily end up overwriting each other's changes."_ (closed "completed" but unimplemented)                                 | unverified              | —          | Prajit |
| 3   | https://jxnl.co/ (Walden Yan interview, Sep 11 2025)                                   | _"With any agentic system, lots of actions carry these implicit decisions… you almost always have to make sure this decision is shared with everyone else, or else you might just get these conflicting decisions."_                                                        | unverified              | —          | Prajit |
| 4   | https://cognition.ai/blog/devin-can-now-manage-devins                                  | "Devin Manage Devins coordinator resolves conflicts" — verify exact wording; locate any sentence describing the conflict-resolution mechanism.                                                                                                                              | unverified              | —          | Prajit |
| 5   | https://cognition.ai/blog/deepwiki-mcp-server                                          | DeepWiki MCP three-tool list (`read_wiki_structure`, `read_wiki_contents`, `ask_question`).                                                                                                                                                                                 | unverified              | —          | Prajit |
| 6   | https://la-hacks-2026.devpost.com/                                                     | LA Hacks 2026 Cognition track prizes (1st = $3,000 + 1,000 ACUs + Windsurf Pro 1yr; 2nd = $2,000 + 1,000 ACUs; 3rd = $1,000 + 1,000 ACUs).                                                                                                                                  | unverified              | —          | Prajit |
| 7   | https://docs.windsurf.com/                                                             | Cascade `pre_write_code` hook contract (exit codes, file path env var, behavior on non-zero exit). Local implementation: `scripts/precheck_write.sh` + `docs/CASCADE_INTEGRATION.md`.                                                                                       | unverified              | —          | Prajit |
| 8   | https://pypi.org/project/fastmcp/                                                      | FastMCP package; cited as the MCP transport for the four ACG primitives. Local implementation: `acg/mcp/server.py`, installed via `mcp = ["fastmcp>=2.0,<3.0"]`.                                                                                                            | unverified              | —          | Prajit |
| 9   | https://innovationlab.fetch.ai/ (Agentverse / `uagents-adapter` MCPServerAdapter docs) | uagents-adapter MCPServerAdapter wrapping for Agentverse submission.                                                                                                                                                                                                        | unverified              | —          | Prajit |
| 10  | https://www.asus.com/us/business/ (or LA Hacks sponsor page)                           | ASUS GX10 LA Hacks 2026 sponsorship + spec (128 GB unified memory, NVIDIA GB10).                                                                                                                                                                                            | unverified              | —          | Prajit |
| 11  | https://arxiv.org/abs/2511.19635 (Agint, Nov 2025)                                     | Adjacent runtime DAG compiler for SE agents — cited as related work, distinguished as runtime-only vs. our pre-flight verification.                                                                                                                                         | unverified              | —          | Prajit |

If any **paraphrased** entry stays paraphrased at submission time, do this in order:

1. In `README.md`, soften the language ("our reading of …" rather than direct quote).
2. In the demo video script, drop the quote and reword the claim to first-person observation.
3. In the Devpost page, link the URL but don't claim a verbatim quote.

Cited but not directly quoted in our materials (no verification work needed; just keep the URLs alive):

- LangGraph: https://github.com/langchain-ai/langgraph
- Temporal: https://temporal.io/
- Apache Airflow: https://airflow.apache.org/
- Anthropic multi-agent essays: https://www.anthropic.com/news/ (whichever post we name)
