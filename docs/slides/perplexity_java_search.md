# Perplexity / Claude / GPT-5 search prompt — find a richer Java migration target

> Goal: replace or augment the 3-task `spring-attic/greenhouse` Java fixture
> with something substantially larger and more interesting for a Java 6 →
> 17 migration benchmark. Drop the prompt below into Perplexity (or any
> deep-research-style model) and harvest 3–5 strong candidates.

---

## The prompt (paste verbatim)

```
I'm building a benchmark fixture for an agent-coordination tool. I need to
find a real, open-source Java codebase suitable for a Java 6 → 17
modernization benchmark. The candidates must satisfy ALL of the following
hard requirements:

HARD REQUIREMENTS

1. Pinnable to a Java 6 or 7 era commit. I need to be able to check out
   a specific commit hash where the build config genuinely targets Java
   1.6 or 1.7. Concretely, look for:
     - Maven: `<java.version>1.6</java.version>` or
       `<source>1.6</source><target>1.6</target>` in pom.xml at that commit
     - Gradle: `sourceCompatibility = 1.6` / `targetCompatibility = 1.6`
     - Active development period: roughly 2009–2014.

2. Size: 500–3000 Java source files under src/main/java. Larger than
   spring-petclinic, smaller than Hadoop / Kafka / Jenkins / Solr.
   Sweet spot is 800–1500 source files — large enough that a Java 6 → 17
   migration is non-trivial, small enough for a small team to analyze.

3. Production-grade and real. Not a tutorial, demo, sample app, or
   "Hello World" project. It must be something real users used in
   production at some point. A defunct-but-real product is fine; an
   incubation-stage Apache project is fine. A "kitchen sink demo" is not.

4. Permissive license. Apache 2.0, MIT, BSD, or similarly permissive.
   No GPL / LGPL / AGPL.

5. Has a test suite (JUnit 3 / 4, TestNG) — so a migration's correctness
   can be empirically verified by running tests after the migration.

6. NOT a framework or library that other major projects depend on
   (skip Spring core, Apache Commons, Guava, Hibernate ORM core, etc.).
   I want an APPLICATION or SERVICE so the modernization is
   self-contained and doesn't ripple to consumers.

MODERNIZATION RICHNESS

The codebase should expose at least 4 of the following Java 6 → 17
modernization opportunities, ideally in volume:

   a. Anonymous inner classes that should become lambdas
      (Runnable, Comparator, Callable, RowMapper, ResultSetExtractor,
      ActionListener, MouseAdapter, etc.) — count >= 50 occurrences.
   b. Raw generic types or pre-generics collections —
      look for `List foo = new ArrayList()` or `Iterator it`.
   c. Try-finally resource cleanup that should become try-with-resources
      (look for explicit `.close()` in finally blocks for InputStream,
      Connection, Statement, ResultSet).
   d. java.util.Date / java.util.Calendar / SimpleDateFormat usage that
      could move to java.time.*.
   e. Switch statements on enums or constant strings that could become
      Java 14+ switch expressions.
   f. Data-only classes (immutable POJOs with all-final fields,
      constructor + getters) that could become Java 16+ records.
   g. String concatenation that could become text blocks (Java 15+).
   h. Functional interfaces missing @FunctionalInterface annotations.
   i. Old try-catch-Exception patterns that could use multi-catch.

OUTPUT FORMAT

Please return EXACTLY 5 candidates ranked by suitability. For each:

  ## Candidate N: <project name>

  - **GitHub URL:**
  - **Suggested old commit hash** (Java 6/7 era, with year):
  - **License:**
  - **Approx Java source file count** at that commit:
  - **Build system:** (Maven / Gradle / Ant / other)
  - **Test framework:**
  - **Modernization opportunities present** (which letters from a–i above):
  - **Why it's interesting** (2 sentences):
  - **Caveats** (heavy XML config, code generation, native deps, etc.):

WHERE TO LOOK (priority order)

1. Spring Attic — `github.com/spring-attic/*` — many Spring-era apps from
   2010–2013 are pinned at Java 1.6 and were real production code.
   Examples to investigate: greenhouse (already used, too small),
   spring-flex, spring-mobile-samples, spring-social-samples,
   spring-webflow-samples, spring-roo-related apps.

2. Apache Attic — `github.com/apache/<project>` for retired projects:
   Apache OODT, Apache Wink, Apache Tashi, Apache Continuum, Apache River.

3. JBoss Community Archive — older RHQ, Drools 5.x, jBPM 5.x, Seam 2.x.

4. Defunct enterprise OSS — Pentaho 5.x era, Talend Open Studio 5.x era,
   OpenNMS pre-2015, Bonita Open Solution 5.x, Liferay Portal 6.0/6.1
   era (might be too big), OFBiz pre-2015.

5. SpringSource / VMware / Pivotal era apps from 2010–2013.

ANTI-EXAMPLES (do NOT recommend)

- spring-petclinic (too small, ~30 source files)
- Hadoop, Kafka, Cassandra, Jenkins, Solr, Lucene (too large)
- Spring Boot itself, Spring core itself, Apache Commons (frameworks)
- Anything written in Scala / Kotlin / Groovy with Java sprinkles
- Anything that's currently a framework dependency for major projects
- Anything with > 50% generated code (e.g., gRPC stubs, JAXB)

Be specific. Generic suggestions like "any Apache project" are useless.
Cite real commit hashes, real file counts, real licenses.
```

---

## Why this prompt is shaped this way

- **Hard requirements section first** — most LLM "find me an X" prompts
  fail because the model returns plausible but unverified candidates.
  Forcing concrete checkable constraints (commit hash, file count, license)
  filters out 80% of the noise.
- **Modernization-richness checklist** — distinguishes "old Java" from
  "old Java that genuinely benefits from modernization." A 2010-era
  Spring app that already mostly used generics and `try-with-resources`
  (back-ported via guava) is uninteresting.
- **Anti-examples** — LLMs love to suggest spring-petclinic and Hadoop.
  Pre-empting them saves a round-trip.
- **Output format pinned to a table** — lets you skim 5 candidates in
  one minute and pick the one to actually clone and inspect.

## After you have candidates

For each top candidate:

1. `git clone <url> && git checkout <commit>`
2. `find src/main/java -name '*.java' | wc -l` — confirm file count
3. `grep -rn "<java.version>" pom.xml` — confirm Java 1.6/1.7 target
4. `grep -rn "new Runnable() {" src/main/java | wc -l` — confirm at
   least 50 anonymous-class candidates exist
5. `mvn test -DskipTests=false` (or equivalent) — confirm tests run on
   modern Java if you bump `java.version` to 17

If all 5 pass, draft 5–10 migration tasks (one per module / package /
modernization category) and feed them into `acg compile --language java`.

---

## Results from running this prompt on demo day

Perplexity returned 5 ranked candidates. **Top pick** is Apache Continuum.
Full ranked list:

| #   | Project              | Suggested commit                        | Java target | ~Files  | Build   | Tests                | License                                |
| --- | -------------------- | --------------------------------------- | ----------- | ------- | ------- | -------------------- | -------------------------------------- |
| 1   | **Apache Continuum** | `78ee257` (tag `continuum-1.4.3`, 2015) | 1.5 → 1.6   | ~1,000  | Maven 2 | JUnit 3.8.1 / JMock  | **Apache 2.0**                         |
| 2   | Apache Archiva 1.3.x | `908493c` (2014)                        | 1.5         | ~900    | Maven 2 | JUnit 3+4 / EasyMock | Apache 2.0                             |
| 3   | Apache Wink 1.4      | trunk tip (2013)                        | 1.6         | ~650    | Maven 3 | JUnit 4 / TestNG     | Apache 2.0                             |
| 4   | JBoss RHQ 4.4        | tag `RHQ_4_4_0_GA` (2012)               | 1.6         | ~2,000+ | Maven 3 | JUnit 4 / TestNG     | Mixed (LGPL core + Apache 2.0 plugins) |
| 5   | Drools 5.5           | tag `5.5.0.Final` (2012)                | 1.6         | ~1,100  | Maven 3 | JUnit 4              | Apache 2.0                             |

**Recommended next-fixture target: Apache Continuum at `78ee257`.**

Why it's the strongest pick for our benchmark:

- **19 real Maven modules** (`continuum-api`, `continuum-core`,
  `continuum-store`, `continuum-webapp`, `continuum-buildagent`,
  `continuum-distributed`, `continuum-xmlrpc`, `continuum-notifiers`,
  `continuum-purge`, `continuum-release`, etc.) — natural per-module
  task decomposition for `acg compile`.
- **~1,000 Java source files** — sweet spot of "big enough that
  modernization is non-trivial" without becoming Hadoop-scale.
- **Apache 2.0 throughout** — no license audit needed.
- **Heavy modernization opportunities present:** anonymous inner classes
  in build queue / notifier system, raw generics in `continuum-store`,
  `try-finally` `.close()` on JDBC/JDO resources, `java.util.Date` across
  the build-result/scheduling domain, immutable POJOs in `continuum-model`
  that are record candidates, multi-catch opportunities in build/notifier
  exception chains.
- **Production-grade.** Real CI server that shipped to customers 2006–2014,
  not a tutorial.
- **Self-contained.** No downstream projects still tracking the 1.4.x
  branch, so modernization is local.

**Caveats to plan around:**

- Heavy Spring 2.5 XML configuration throughout.
- JDO/JPOX bytecode enhancement runs at build time (extra Maven flag).
- `continuum-model` has Modello-generated sources (exclude from migration
  task counts).
- Struts2 action classes are Plexus-component-annotated.
- Parent POM at the 1.4.3 commit targets `<source>1.5</source>` not 1.6;
  if a hard 1.6 declaration matters, pin to a 2012–2013 trunk commit
  instead.

**Suggested first batch of migration tasks for the new fixture (5–10 tasks):**

1. Convert anonymous `Comparator`/`Runnable`/`Callable` instances in
   `continuum-core` build-queue dispatcher to lambdas. Bump module
   `<java-version>` to 17.
2. Modernize `java.util.Date`/`java.util.Calendar` in
   `continuum-store` scheduling domain → `java.time`.
3. Convert immutable POJOs in `continuum-model` (excluding Modello-
   generated) to Java 17 records.
4. Convert `try-finally` `.close()` blocks in `continuum-store` JDBC/JDO
   layer to `try-with-resources`.
5. Add `@FunctionalInterface` to single-method listener interfaces
   (`BuildResultListener`, `NotificationDispatcher`).
6. Replace raw `Collection`/`List`/`Iterator` with parameterised types in
   `continuum-api` public surface.
7. Modernize `switch` statements on `BuildState`/`ProjectState` enums to
   switch expressions.
8. Multi-catch consolidation in `continuum-notifiers` exception
   handling.

Each is a candidate ACG task with predictable `allowed_paths`. The
pom.xml `<java-version>` bump is the cross-task contention point — this
fixture would _finally_ exercise the contention story with a 6-way
conflict pair set instead of Greenhouse's 3-way.
