# Parallel Coding Agents Should Use Pre-Flight Write-Contract Lockfiles

## Abstract

This position paper argues that parallel coding agents should use pre-flight write-contract lockfiles to determine safe parallel execution before any agent writes code. The core problem is not merely that multiple agents can edit the same repository at once; it is that they often do so without an explicit, reviewable account of which files each task is expected to touch, which overlaps are acceptable, and which tasks must serialize. Current multi-agent coding workflows often rely on post-hoc merge resolution, dynamic orchestration, or broader per-agent context windows. Those approaches can still leave task coupling implicit until conflicts have already been introduced into the working tree or into competing pull requests. We argue instead for a compile-time coordination artifact: a lockfile that predicts likely write-sets, assigns explicit write authority, and produces a conflict-aware execution plan before any worker begins. We use Agent Context Graph (ACG) and its `agent_lock.json` artifact as an existence proof that this approach is feasible with current tooling. Across small-scale directional experiments in Java and TypeScript repositories, the approach reduced unnecessary per-worker context, preserved partial parallelism where tasks were disjoint, and produced contract-compliant behavior in live black-box agent runs. The broader claim is normative rather than benchmark-driven: pre-flight write coordination should become standard infrastructure for parallel coding agents.

## 1. Introduction

Parallel coding agents are attractive because they promise wall-time gains on multi-task software work, but they also make a familiar software engineering problem easier to trigger and harder to inspect: conflicting changes across a shared repository. A coordinator can spawn several workers quickly, yet that speed hides a decision that is often left implicit until too late: which tasks are actually safe to run in parallel?

**This paper argues that parallel coding agents should use pre-flight write-contract lockfiles to decide parallelism before execution.** In our view, the central failure in many multi-agent coding workflows is not simply imperfect merging. It is the absence of an explicit, inspectable artifact that declares predicted write boundaries, expected conflicts, and execution order before workers start modifying code.

This position is motivated by a gap between current orchestration practice and the realities of coupled code changes. Runtime coordination systems can reconcile concurrent edits after the fact, and workflow orchestrators can fan tasks out efficiently, but neither necessarily answers the more basic question of whether two coding tasks should have been launched together in the first place. For code-modifying agents, that omission is costly: file overlaps create merge tax, hidden coupling creates retries, and large undifferentiated context windows create avoidable prompt burden.

The rest of the paper makes four claims. First, file-level contention is common enough in realistic multi-task coding workloads that leaving parallelism implicit is an engineering mistake. Second, a useful coordination layer should exist before execution and should be reviewable like any other infrastructure artifact. Third, predicted write-sets are a practical basis for that layer even if they are imperfect. Fourth, ACG shows that this design is already implementable as a compile-time lockfile plus a lightweight runtime validator, and that it provides useful directional benefits even in small-scale tests.

## 2. Problem Setting: Why Parallel Coding Agents Collide

Parallel coding agents collide because software tasks rarely map cleanly onto disjoint files. Two feature requests that sound independent in natural language may still converge on the same schema file, shared configuration, routing layer, dependency injection module, environment template, or test scaffold. This is especially common in modern application repositories where authentication, billing, settings, tests, and deployment concerns meet in a handful of infrastructure hotspots.

Several specific failure modes follow from this. The first is overlapping writes: two agents edit the same file, whether or not they do so on the same lines. The second is hidden coupling: two agents edit different files but rely on incompatible assumptions about APIs, schema shape, or configuration state. The third is merge tax: even when textual conflicts are resolvable, reviewers must reconstruct intent across competing changes that were never planned together. The fourth is retry tax: when an agent lands work against an outdated assumption, the team pays again in reruns, manual repair, or cleanup prompts.

These failures are not well handled by simply giving every worker more context. Larger context windows may help an agent see more of the repository, but they do not create a contract about what the agent is supposed to change. They also do not tell the coordinator which tasks should serialize. More context can reduce blindness without reducing contention.

Nor is post-hoc conflict handling sufficient as a default strategy. If the system discovers contention only after workers have already diverged, then time has already been spent on mutually interfering work. In human teams, engineers do not wait until merge time to decide whether two developers should both redesign the same subsystem simultaneously. Parallel coding agents deserve the same discipline.

## 3. Core Position

Our position is that task coordination for code-writing agents should happen before execution and should produce a durable artifact. In practice, that means a pre-flight lockfile with at least four properties.

First, it should make predicted write authority explicit. Each task should carry a machine-readable list of likely touched files or globs, together with enough explanation to make the prediction reviewable. The point is not perfect prophecy. The point is to replace implicit guesses with inspectable claims.

Second, it should plan parallelism from predicted write-sets rather than from task count alone. If two tasks are write-disjoint, a planner should be able to run them together confidently. If they overlap on shared infrastructure, the planner should serialize them by default. Parallelism should be earned by disjointness rather than assumed by enthusiasm.

Third, it should be committable and reviewable. A coding plan that only exists inside a coordinator prompt is hard to audit, hard to diff, and hard to reuse across tools. A lockfile makes coordination legible. It can be checked into version control, inspected in code review, and consumed by different runtimes without re-explaining the intended boundaries.

Fourth, it should support enforcement or at least audit. The lockfile should not be a decorative hint. Local agents can be stopped when they attempt writes outside their scope, and black-box hosted agents can at minimum be audited against the contract after they produce a diff or pull request.

This position does not require strong claims about perfect prediction. A useful write-contract layer can be conservative. Over-prediction may reduce some parallelism, but under most real workflows it is still preferable to silent overlapping execution on shared files. The right comparison is not against omniscient planning; it is against the status quo of implicit coordination.

## 4. ACG as an Existence Proof

Agent Context Graph (ACG) is a concrete example of the mechanism we advocate. It treats task coordination as a compile-time problem. Given a repository and a task list, ACG scans the codebase, predicts each task's likely write-set, detects cross-task overlaps, and emits a lockfile (`agent_lock.json`) that includes predicted writes, allowed write paths, and an execution plan grouping tasks into parallel-safe and serialized stages.

The design has six relevant pieces. The first is repository scanning. ACG builds a structured view of the repository using language-aware analysis and file-level graph signals so that predictions are grounded in actual project structure rather than only task wording. The second is write-set prediction. It combines deterministic seeds with language-model reranking to estimate which files a task is likely to modify. The third is lockfile generation. The resulting artifact records each task, its predicted writes, its permitted write scope, and any detected contention with other tasks. The fourth is allowed-path assignment. This narrows each task's write authority into explicit file paths or globs that can be inspected and revised. The fifth is conflict-aware execution grouping. Tasks predicted to overlap are placed into later groups so that workers operating in the same stage are as disjoint as possible. The sixth is runtime validation. Local workers can have proposed writes checked immediately, while black-box agents can be audited post-hoc from their diffs.

The significance of ACG is not that it is the only possible implementation. Its significance is that it demonstrates that pre-flight write coordination is already practical. The artifact is lightweight enough to review, specific enough to enforce, and general enough to work across multiple execution backends. That is enough to support the normative claim of this paper: the ecosystem should treat pre-flight write planning as infrastructure, not as an optional heuristic.

## 5. Supporting Evidence

The empirical evidence for ACG should be interpreted as directional support rather than as a benchmark. The experiments are small-scale, task-specific, and not designed to settle broad performance claims. They are still useful because they show that the proposed mechanism can produce nontrivial coordination decisions and can survive contact with real repositories and live agents.

In a four-task TypeScript demo application, the lockfile identified shared hotspots and scheduled only the disjoint tasks together. In that setting, scoped planning reduced average per-worker prompt tokens from 224 to 128, a reduction of roughly 43%. That result matters less as a token-efficiency headline than as evidence that task-specific write scopes can materially narrow the context each worker needs.

In a Java modernization fixture run against a local model, scoped planning reduced per-worker prompt burden by about 11%. The smaller gain on that fixture is also informative: when allowed scopes are broad, the benefit of planning is modest. This suggests that lockfile utility scales with scope tightness rather than appearing automatically in every repository.

In a larger NestJS backend fixture, scoped planning reduced worker prompt tokens from 3721 to 1700, or about 54%, while preserving partial parallelism through grouped execution. Again, the key point is not that token counts alone justify the approach. It is that explicit scope assignment can shrink worker context substantially in repositories where a whole-repo prompt would otherwise be wasteful.

Finally, the contract model held up in live black-box execution. In a small smoke test using hosted agent runs on a real Java repository, 6 out of 6 observed pull requests stayed within their assigned `allowed_paths`. That does not prove that the lockfile prevented a conflict that would otherwise have happened. It does show that an external agent can be meaningfully guided and audited against a pre-flight contract without changing the agent itself.

Taken together, these results support a restrained claim: pre-flight write-contract planning is plausible, operational, and useful enough to deserve first-class treatment in multi-agent coding systems.

## 6. Alternative Views

One alternative view is that post-hoc merge resolution is enough. On this view, the cost of planning exceeds the cost of cleaning up overlaps later, especially when version control can auto-merge simple conflicts. This argument is reasonable for trivial or highly independent tasks. It is weaker for repositories with shared infrastructure hotspots, where the cost of discovering contention late is not only textual merge work but also reviewer confusion and task retries. Pre-flight planning is valuable precisely when the work is not trivial.

A second alternative is that runtime coordination layers are the right abstraction. Systems such as CRDT-based coordination can guarantee convergence of concurrent edits and can be more flexible than compile-time planning when tasks evolve during execution. We agree that runtime coordination is useful, and we do not treat it as an opponent to static planning. The disagreement is about sufficiency. Runtime coordination can reconcile edits after they begin; it does not by itself decide which tasks should have been parallelized at all. A compile-time lockfile and a runtime coordination substrate can coexist, with the former narrowing the need for the latter.

A third alternative is that stronger agents with larger context windows make write contracts unnecessary. Better models may indeed predict contention implicitly more often than weaker ones. But private competence is not the same as shared coordination. A team cannot review an agent's unspoken guess about its write boundary. An explicit lockfile is valuable because it externalizes the decision, not because agents are incapable of making private inferences.

A fourth alternative is that static write prediction is too brittle to justify operational use. This concern is real. Predictions can miss files or over-predict scope. But imperfect prediction does not make the artifact useless. It means the artifact should be conservative, reviewable, and paired with validation. In practice, many infrastructure tools are valuable despite being approximate. The relevant standard is whether the planner improves coordination over the baseline of implicit, unaudited parallelism. We believe it does.

## 7. Limitations

This position paper does not claim that file-level write contracts solve every coordination problem. First, file-level disjointness is an incomplete proxy for semantic independence. Two tasks can edit different files and still make incompatible assumptions about interfaces or behavior. Second, the supporting evidence here is small-scale and should not be read as a benchmark. Further research is needed across model architectures, model sizes, and broader benchmarks in different domains before these results can be generalized. The current results are better suited to showing feasibility than to establishing general effect sizes. Third, the current mechanism depends on language-aware scanning and write-set heuristics, so language coverage remains incomplete. Fourth, black-box hosted agents can often be audited only after they produce a diff, which weakens enforcement compared with local pre-write validation. Fifth, conservative planning can sacrifice some parallelism in exchange for safety.

These limitations matter, but they do not weaken the paper's main claim. They locate the proper scope of the claim: pre-execution write coordination is useful infrastructure, not a total theory of agent correctness.

## 8. Conclusion

Parallel coding agents should not treat write coordination as an afterthought. Before agents start editing code, the system should make an explicit decision about which tasks are write-disjoint, which tasks contend on shared files, and what each worker is permitted to change. A pre-execution write-contract lockfile is a practical way to make that decision legible, reviewable, and enforceable.

ACG shows that this is already possible with current tooling, and that its benefits compound on top of the existing advantages of modern agent stacks rather than competing with them. It is not the only implementation path, and it does not remove the need for runtime coordination or semantic review. What it shows, instead, is that pre-flight write planning can already be made concrete enough to inspect, portable enough to use across backends, and lightweight enough to integrate into real workflows. If parallel coding agents are to become reliable engineering systems rather than impressive demos, explicit write contracts should become part of their standard infrastructure.

## References

[1] Sergey Pugachev. *CodeCRDT: Observation-Driven Coordination for Multi-Agent LLM Code Generation*. arXiv:2510.18893v1, 2025.

[2] LangChain. *LangGraph*. Project documentation and source repository. https://github.com/langchain-ai/langgraph

[3] Temporal Technologies. *Temporal Documentation*. https://temporal.io/

[4] Apache Software Foundation. *Apache Airflow Documentation*. https://airflow.apache.org/

[5] Paul Gauthier. *Aider is AI Pair Programming in Your Terminal*. https://aider.chat/2023/10/22/repomap.html
