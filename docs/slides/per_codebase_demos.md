# Per-codebase demo summary (slide-ready, fresh re-runs)

> Compact reference for the "what we actually ran" slides. Drop these blocks
> into your slide tool.
>
> **All numbers below are from re-runs executed on demo day** against the
> live ACG harness:
>
> - Greenhouse + Brocoders → Gemma Q4 (`gemma-4-26B-A4B-it-UD-Q4_K_XL.gguf`)
>   on `gx10-f2c9` (port 8080 sub-agents, port 8081 orchestrator).
> - demo-app → deterministic mock backend.
>
> Source artifacts:
>
> - `experiments/greenhouse/runs/eval_run_combined.json`
> - `experiments/microservice/runs_brocoders_local/eval_run_combined.json`
> - `experiments/demo-app/runs/eval_run_combined.json`

---

## 1. Greenhouse — Java legacy modernization

**Repo / pin**

- Source: `spring-attic/greenhouse`
- Commit: `174c1c320875a66447deb2a15d04fc86afd07f60`
- `<java-version>` in `pom.xml`: **1.6** (genuine Java-6 era checkout)
- Files: ~200 Java sources, Spring stack

**The actual prompts the agent received** (verbatim from `experiments/greenhouse/agent_lock.json`)

- **`lambda-rowmapper-account`** —

  > _"In src/main/java/com/springsource/greenhouse/account/JdbcAccountRepository.java, replace the anonymous RowMapper&lt;PasswordProtectedAccount&gt; inner class (around line 110) with a Java 8 lambda. The mapping logic must remain identical. Bump &lt;java-version&gt; in pom.xml from 1.6 to 1.8 so the lambda compiles. Update imports as needed."_
  > **Allowed paths:** `pom.xml`, `src/main/java/com/springsource/greenhouse/{account,invite,members}/**`.

- **`lambda-rowmapper-invite`** —

  > _"In src/main/java/com/springsource/greenhouse/invite/JdbcInviteRepository.java, replace the anonymous RowMapper&lt;Invite&gt; inner class (around line 87) with a Java 8 lambda. The mapping logic must remain identical. Bump &lt;java-version&gt; in pom.xml from 1.6 to 1.8 so the lambda compiles."_
  > **Allowed paths:** `pom.xml`, `src/main/java/com/springsource/greenhouse/invite/**`, `src/main/java/com/springsource/greenhouse/invite/mail/**`, `src/test/java/com/springsource/greenhouse/database/**`.

- **`lambda-rowmapper-app`** —
  > _"In src/main/java/com/springsource/greenhouse/develop/JdbcAppRepository.java, replace the four anonymous RowMapper inner classes (around lines 113, 158, 166, 172) with Java 8 lambdas. Mapping logic must remain identical. Bump &lt;java-version&gt; in pom.xml from 1.6 to 1.8 so the lambdas compile."_
  > **Allowed paths:** `pom.xml`, `src/main/java/com/springsource/greenhouse/develop/**`.

**ACG output**

- 3 conflict pairs predicted, **all on `pom.xml`** (every task bumps the version).
- Execution plan: 3 serial groups (each task waits for the previous).
- Allowed paths per task ≈ 3 directories.

**Run results (fresh on demo day)**

| Strategy         | Total prompt tok | Per-task prompt tok | Total completion tok | Wall time | OOB | Blocked | Pred. overlap pairs |
| ---------------- | ---------------: | ------------------: | -------------------: | --------: | --: | ------: | ------------------: |
| `naive_parallel` |             2159 |               719.7 |                 1062 |   12.47 s |   0 |       0 |                   3 |
| `acg_planned`    |             1922 |               640.7 |                  912 |   22.13 s |   0 |       0 |                   3 |

**Per-task prompt-token savings: 79 tok / task (~11.0%).**

**Other backends previously run on this fixture**

- **Devin v3 API** (live, real PRs to a fork): 6/6 PRs in scope, 0 OOB, naive 277s vs ACG planned 854s. Carry-over from earlier session.
- **Mock** (deterministic, CI-friendly): same lockfile shape.
- **Tightened-fixture variant** (`agent_lock_tight.json`): mock run produces 18 blocked write events on the original predictor's over-eager Java false positives. CI test enforces this regression.

---

## 2. Brocoders NestJS — modern microservice context-scaling

**Repo / pin**

- Source: `brocoders/nestjs-boilerplate`
- Commit: `dd0034750fc7f6ec15712afbecf50fa9828018a2` (main branch)
- Stack: NestJS + TypeORM + PostgreSQL relational + Mongoose document
- Files: 156 TypeScript files under `src/`

**The actual prompts the agent received** (verbatim from `experiments/microservice/agent_lock_brocoders.json`)

- **`products-domain`** —

  > _"Add a products domain to the NestJS API. Create src/products/products.module.ts, src/products/products.controller.ts, src/products/products.service.ts, src/products/domain/product.ts, src/products/dto/create-product.dto.ts, src/products/dto/update-product.dto.ts, src/products/infrastructure/persistence/product.repository.ts, src/products/infrastructure/persistence/relational/entities/product.entity.ts, and src/products/infrastructure/persistence/relational/repositories/product.repository.ts. Register ProductsModule in src/app.module.ts and add a TypeORM migration under src/database/migrations."_

- **`api-key-auth`** —

  > _"Add API key authentication for service-to-service requests. Create src/auth-api-key/auth-api-key.module.ts, src/auth-api-key/auth-api-key.guard.ts, src/auth-api-key/auth-api-key.service.ts, src/auth-api-key/config/api-key.config.ts, and src/auth-api-key/config/api-key-config.type.ts. Register the config in src/app.module.ts and document API_KEY in .env.example."_

- **`users-search`** —

  > _"Add search and email-domain filtering to the users list endpoint. Update src/users/users.controller.ts, src/users/users.service.ts, src/users/dto/query-user.dto.ts, and src/users/infrastructure/persistence/relational/repositories/user.repository.ts. Do not change app.module.ts."_

- **`files-e2e-tests`** —

  > _"Add e2e coverage for file upload configuration and authorization. Create test/files.e2e-spec.ts and update test/jest-e2e.json only if needed. Do not touch src/app.module.ts or database files."_

- **`registration-email-job`** —

  > _"Add a Bull-backed background job for post-registration emails. Create src/jobs/jobs.module.ts, src/jobs/registration-email.processor.ts, and src/jobs/registration-email.service.ts. Wire it into src/auth/auth.service.ts and src/mail/mail.module.ts, register JobsModule in src/app.module.ts, and document REDIS_URL in .env.example and docker-compose.yml."_

- **`notifications-webhook`** —

  > _"Add a notifications webhook endpoint. Create src/notifications/notifications.module.ts, src/notifications/notifications.controller.ts, src/notifications/notifications.service.ts, and src/notifications/dto/notification-webhook.dto.ts. Register NotificationsModule in src/app.module.ts. Do not change users or files modules."_

- **`deployment-config`** —
  > _"Harden local deployment configuration. Update .env.example, docker-compose.yml, src/config/app.config.ts, src/config/app-config.type.ts, src/database/config/database.config.ts, and src/database/config/database-config.type.ts with REDIS_URL, API_KEY, and HEALTHCHECK_TIMEOUT_MS settings. Do not change feature controllers."_

**ACG output**

- **11 conflict pairs predicted**: 10 on `src/app.module.ts`, 1 on `docker-compose.yml`.
- **Execution plan:**
  - Group 1 (parallel): `deployment-config`, `products-domain`, `users-search`
  - Group 2 (serial): `api-key-auth`
  - Group 3 (serial): `files-e2e-tests`
  - Group 4 (serial): `registration-email-job`
  - Group 5 (serial): `notifications-webhook`
- Preserves **partial parallelism** — not total serialization.

**Run results (fresh on demo day)**

| Strategy         | Total prompt tok | Per-task prompt tok | Total completion tok | Wall time | OOB | Blocked | Conflict pairs |
| ---------------- | ---------------: | ------------------: | -------------------: | --------: | --: | ------: | -------------: |
| `naive_parallel` |             3721 |               531.6 |                 4900 |   48.14 s |   0 |       0 |             11 |
| `acg_planned`    |             1700 |               242.9 |                 4775 |   74.47 s |   0 |       0 |             11 |

**Per-task prompt-token savings: 288.7 tok / task (~54.3%).**

**Caveat:** the local Q4 model under-proposed concrete writes for several tasks (most workers hit the 700-token completion cap with zero diffs emitted). This run is a **planning / context-scaling** demo, not a generated-code-quality demo. Lockfile shape, allowed-paths enforcement, and prompt-token savings are real measurements; the agent's code-quality is _not_ what this fixture is measuring.

---

## 3. demo-app — T3 / Next.js cross-language demo

**Repo / pin**

- Located at `demo-app/` in this monorepo
- Stack: Next.js 14 (App Router) + Prisma + tRPC + NextAuth
- Files: ~50 TypeScript / TSX

**The actual prompts the agent received** (verbatim from `demo-app/agent_lock.json`)

- **`oauth`** —

  > _"Add Google OAuth login. Use NextAuth. Update Prisma schema with required fields."_

- **`billing`** —

  > _"Add a billing dashboard tab at /dashboard/billing with Stripe integration. Add a sidebar entry. Update Prisma with subscription model."_

- **`settings`** —

  > _"Redesign the user settings page at /settings. Reorganize sections. Update sidebar entry styling."_

- **`tests`** —
  > _"Write end-to-end Playwright tests for the checkout flow."_

**ACG output**

- **4 conflict pairs predicted**: on `.env.example`, `prisma/schema.prisma`, `src/components/Sidebar.tsx`, `src/app/dashboard/page.tsx`, `src/server/db.ts`.
- **Execution plan:** group 1 (parallel) `{oauth, settings}`, then serial `billing`, then serial `tests`.

**Run results (fresh on demo day, mock backend)**

| Strategy         | Total prompt tok | Per-task prompt tok | Total completion tok | Wall time | OOB | Blocked | Conflict pairs |
| ---------------- | ---------------: | ------------------: | -------------------: | --------: | --: | ------: | -------------: |
| `naive_parallel` |              897 |               224.2 |                   88 |   0.006 s |   0 |       0 |              4 |
| `acg_planned`    |              513 |               128.2 |                   88 |   0.005 s |   0 |       0 |              4 |

**Per-task prompt-token savings: 96 tok / task (~42.8%).**

**Why this codebase matters:** the _cross-language_ demo. Same harness, narrower `allowed_paths`, larger per-task savings than Greenhouse because scope is tighter. The variance between codebases is itself the finding — savings scale with scope tightness, which the lockfile compiler controls.
