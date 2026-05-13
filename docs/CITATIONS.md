# Citations (external references used in Cognition-integration docs)

This file backs short references in [`COGNITION_INTEGRATION.md`](COGNITION_INTEGRATION.md). **Paraphrase only** appears here unless a primary source excerpt has been audited in-tree; contributors may add verbatim block quotes after verifying wording against originals.

---

## CodeCRDT (parallel multi-agent task coupling)

- **Citation**: Daniil Pugachev, Timofey Kutasov, Egor Kutovoy, Igor Udus *et al.*, "CodeCRDT: Conflict-free Replicated Data Types Approach for Multi-Agent Collaboration in Shared Codebases." arXiv:2510.18893 (Oct 2025).
- **URL**: https://arxiv.org/abs/2510.18893
- **Use in repo docs**: CodeCRDT is cited as adjacent *runtime* coordination work; ACG emphasizes static pre-flight contracts and deterministic validation paths rather than character-level merging.

---

## OpenCode — per-file locking / multi-agent coordination (issue discussion)

- **Reference**: OpenCode GitHub repository **anomalyco/opencode**, issue **#4278**, "File locks / Vim-style locks" (opened Nov 2025; cited in Cognition-integration docs as representative demand for coordination primitives).
- **URL**: https://github.com/anomalyco/opencode/issues/4278
- **Use in repo docs**: Illustrative public appetite for narrowing parallel agents' simultaneous write footprint; ACG supplies a complementary pre-flight contract + validator substrate.

---

## Walden Yan — Cognition × context engineering notes (Jason Liu)

- **Reference**: Jason Liu (jxnl.co), notes from a Walden Yan (Cognition) conversation on implicit decisions / context fragmentation in autonomous coding workflows — **2025-09-11**.
- **URL**: https://jxnl.co/writing/2025/09/11/why-cognition-does-not-use-multi-agent-systems/
- **Use in repo docs**: Motivation for making write-set and scope decisions explicit, reviewable, and committable rather than latent in parallel agent chatter.
