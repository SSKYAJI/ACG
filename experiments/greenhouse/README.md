# Greenhouse experiment

A head-to-head playground for ACG against parallel coding agents on a real
**legacy Java** codebase: Spring's own
[`spring-attic/greenhouse`](https://github.com/spring-attic/greenhouse)
conference app, pinned to commit
[`174c1c320875a66447deb2a15d04fc86afd07f60`](https://github.com/spring-attic/greenhouse/tree/174c1c320875a66447deb2a15d04fc86afd07f60).

The experiment exists to demonstrate that ACG's pre-flight write-set
planning generalizes beyond TypeScript: the same predictor and solver that
power the demo-app benchmark also work against a Java repo, given a
language-appropriate context graph.

## Why this repo

- ~130 Java files with Spring-style domain services
  (`EventService`, `InviteService`, `FriendService`, `AccountRepository`)
  that share configuration in `DatabaseConfig.java`.
- Apache 2.0 licensed; the human author has confirmed `mvn clean test`
  builds at the pinned commit.
- The shared `JdbcTemplate` config is exactly the kind of cross-cutting
  surface where naive parallel agents collide.

## Run it

```bash
make setup-greenhouse compile-greenhouse
```

`setup-greenhouse` clones (or updates) the upstream repo into
`experiments/greenhouse/checkout/` and pins it to the commit above.
`compile-greenhouse` runs `acg compile --language java`, which:

1. Walks the checkout with the tree-sitter Java grammar
   (`graph_builder/scan_java.py`) and emits
   `experiments/greenhouse/checkout/.acg/context_graph.json`.
2. Feeds that graph + `tasks.json` into the standard ACG compile pipeline
   (predictor → solver → enforce).
3. Writes `experiments/greenhouse/agent_lock.json`.

## What to expect in the lockfile

Three refactor tasks, each replacing an anonymous-inner-class with a
Java 8 lambda:

- `lambda-event-comparator` — `Comparator<Event>` → `Comparator.comparing(...)`
- `lambda-rowmapper-account` — `RowMapper<Account>` → lambda
- `lambda-rowmapper-invite` — `RowMapper<Invite>` → lambda

Each task carries `"config"` in `hints.touches` so the topical seed picks
up `DatabaseConfig.java` alongside the per-task service file. The solver
should detect the shared file overlap and serialize at least one task
behind the others, while leaving the remaining tasks parallelizable.

The lockfile's `predicted_writes` should contain at least the per-service
file plus `DatabaseConfig.java` for each task, matching the predictor's
overlap signal.

## Head-to-head harness

The harness that drives parallel coding agents against this lockfile —
ACG-planned vs. naive — lives at `experiments/greenhouse/headtohead.py`
(written by the human author, not by the in-repo Devin sessions).
