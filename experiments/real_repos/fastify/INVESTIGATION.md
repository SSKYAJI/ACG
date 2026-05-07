# Fastify Ground-Truth Investigation

### pr-6653: feat: add request.mediaType

**Task prompt** (from manifest): Add a readonly request.mediaType property that exposes the parsed request Content-Type media type, cache the parsed ContentType on the request, and update validation and request handling to reuse the parsed value.

**Ground truth files** (what the human PR actually changed):
- lib/handle-request.js
- lib/request.js
- lib/symbols.js
- lib/validation.js
- types/request.d.ts

**Predictor `predicted_writes`** (from eval_run_combined.json):
- types/content-type-parser.d.ts
- test/content-parser.test.js
- lib/content-type-parser.js
- test/internals/validation.test.js
- test/logger/logger-test-utils.js

**Misses** (in ground truth but NOT in predicted_writes):
- lib/handle-request.js
- lib/request.js
- lib/symbols.js
- lib/validation.js
- types/request.d.ts

**False positives** (in predicted_writes but NOT in ground truth):
- types/content-type-parser.d.ts
- test/content-parser.test.js
- lib/content-type-parser.js
- test/internals/validation.test.js
- test/logger/logger-test-utils.js

**Per-strategy agent proposals** (from ground_truth_score.json `agent_match_to_human_by_strategy`):
| Strategy | Proposed files | TP | FP | FN | Status |
| --- | --- | ---: | ---: | ---: | --- |
| acg_planned | lib/content-type-parser.js, test/content-parser.test.js, test/internals/validation.test.js | 0 | 3 | 5 | completed |
| acg_planned_full_context | lib/content-type-parser.js | 0 | 1 | 5 | completed |
| naive_parallel | fastify.js, lib/config-validator.js, lib/content-type-parser.js, lib/content-type.js | 0 | 4 | 5 | completed_unsafe |

**Failure-mode hypothesis** (one paragraph, evidence-grounded): The prompt did not name repo-relative files, but it did name `request.mediaType`, validation, request handling, and caching the parsed `ContentType` on the request. The predictor followed the `ContentType` topic to `lib/content-type-parser.js`, `types/content-type-parser.d.ts`, and content-parser tests, but missed every human-changed request/validation/API file: `lib/request.js`, `lib/handle-request.js`, `lib/validation.js`, `types/request.d.ts`, and `lib/symbols.js`. The visible failure is a topical/API-surface inference miss: mapping "readonly request.mediaType property" and "request handling" to the request implementation and request type declaration, plus mapping "cache the parsed ContentType on the request" to the internal symbol file. ambiguous: cannot determine from current artifacts whether an import-chain traversal would have found those files.

### pr-6692: perf: defer ContentType parsing in getSchemaSerializer until needed

**Task prompt** (from manifest): Avoid unnecessary ContentType object creation in getSchemaSerializer by only parsing the response content type for content-type-keyed response schemas, including status-code, fallback, and default lookup paths.

**Ground truth files** (what the human PR actually changed):
- lib/content-type-parser.js
- lib/schemas.js

**Predictor `predicted_writes`** (from eval_run_combined.json):
- lib/schemas.js
- types/content-type-parser.d.ts
- test/internals/reply-serialize.test.js

**Misses** (in ground truth but NOT in predicted_writes):
- lib/content-type-parser.js

**False positives** (in predicted_writes but NOT in ground truth):
- types/content-type-parser.d.ts
- test/internals/reply-serialize.test.js

**Per-strategy agent proposals** (from ground_truth_score.json `agent_match_to_human_by_strategy`):
| Strategy | Proposed files | TP | FP | FN | Status |
| --- | --- | ---: | ---: | ---: | --- |
| acg_planned | lib/schemas.js | 1 | 0 | 1 | completed |
| acg_planned_full_context | (none) | 0 | 0 | 2 | completed |
| naive_parallel | lib/content-type.js | 0 | 1 | 2 | completed_unsafe |

**Failure-mode hypothesis** (one paragraph, evidence-grounded): The predictor caught `lib/schemas.js`, which is the file most directly suggested by the prompt's `getSchemaSerializer` and response-schema wording, but it missed `lib/content-type-parser.js`, even though the prompt also names `ContentType object creation` and parsing the response content type. The missed file required associating schema serialization with the parser implementation, not just the schema lookup location. ambiguous: cannot determine from current artifacts whether that association should have come from an import chain, a symbol edge, or a topical keyword seed.

### pr-6694: perf: cache parsed ContentType objects in ContentTypeParser

**Task prompt** (from manifest): Improve Content-Type parsing performance by caching parsed ContentType objects in ContentTypeParser and reusing those parsed objects from request handling and request.mediaType without changing external behavior.

**Ground truth files** (what the human PR actually changed):
- lib/content-type-parser.js
- lib/handle-request.js
- lib/request.js

**Predictor `predicted_writes`** (from eval_run_combined.json):
- types/content-type-parser.d.ts
- test/content-parser.test.js
- lib/content-type-parser.js
- test/logger/logger-test-utils.js

**Misses** (in ground truth but NOT in predicted_writes):
- lib/handle-request.js
- lib/request.js

**False positives** (in predicted_writes but NOT in ground truth):
- types/content-type-parser.d.ts
- test/content-parser.test.js
- test/logger/logger-test-utils.js

**Per-strategy agent proposals** (from ground_truth_score.json `agent_match_to_human_by_strategy`):
| Strategy | Proposed files | TP | FP | FN | Status |
| --- | --- | ---: | ---: | ---: | --- |
| acg_planned | lib/content-type-parser.js, test/content-parser.test.js, types/content-type-parser.d.ts | 1 | 2 | 2 | completed |
| acg_planned_full_context | lib/content-type-parser.js | 1 | 0 | 2 | completed |
| naive_parallel | lib/content-type-parser.js, lib/content-type.js | 1 | 1 | 2 | completed_unsafe |

**Failure-mode hypothesis** (one paragraph, evidence-grounded): The predictor found `lib/content-type-parser.js`, the prompt's directly anchored `ContentTypeParser` file, but missed the two consumer/integration files named conceptually by the same prompt: `lib/handle-request.js` for "request handling" and `lib/request.js` for `request.mediaType`. This suggests static or lexical anchoring worked for the parser component but did not expand to the request-side reuse points. ambiguous: cannot determine from current artifacts whether the missed request files were one-hop or multi-hop graph neighbors of the parser file.

## Cross-PR Pattern

Across the three PRs, the most common miss pattern is not random file drift; it clusters around integration files implied by Fastify request and content-type concepts. `lib/handle-request.js` and `lib/request.js` were missed in both `pr-6653` and `pr-6694`; `lib/validation.js`, `lib/symbols.js`, and `types/request.d.ts` were also missed when `pr-6653` asked for validation reuse and a readonly `request.mediaType` API; `pr-6692` caught `lib/schemas.js` but missed the associated `lib/content-type-parser.js`. The hypothesis is that the predictor is strongest on the most directly anchored parser/schema files and weaker when the human PR requires mapping prompt concepts to request-side or parser-side integration files. ambiguous: cannot determine from current artifacts whether those integration misses are specifically multi-hop import-chain misses.

## Implications For The Paper

- Strength: the predictor can anticipate directly anchored Fastify files, such as `lib/schemas.js` for `getSchemaSerializer` in `pr-6692` and `lib/content-type-parser.js` for `ContentTypeParser` in `pr-6694`.
- Weakness: the predictor missed implied integration/API files, especially `lib/handle-request.js`, `lib/request.js`, `lib/validation.js`, `lib/symbols.js`, `types/request.d.ts`, and the `pr-6692` parser partner file `lib/content-type-parser.js`; this is the natural place to frame graph-aware or topical-seed under-performance, while keeping the claim as a hypothesis.
