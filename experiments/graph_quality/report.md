# Graph Quality Report

| Codebase | Language | Files | Symbols | Imports | Exports | Hotspots | Density | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Brocoders TS | TypeScript | 163 | 163 | 713 | 161 | 44 | 6.36 | 0.0714 | 1.0000 | 0.1333 |
| Greenhouse Java | Java | 208 | 534 | 1097 | 180 | 23 | 8.71 | 0.2500 | 1.0000 | 0.4000 |
| RealWorld TS | TypeScript | 39 | 39 | 139 | 40 | 12 | 5.59 | 0.5128 | 0.8696 | 0.6452 |
| demo-app TS | TypeScript | 19 | 24 | 28 | 23 | 4 | 3.95 | 0.5909 | 0.8667 | 0.7027 |

Across these four codebases, predictor quality is best read as a function of the upstream graph rather than only the downstream contract: the contract can block out-of-scope writes, but its precision depends on whether the graph captures the files, symbols, imports, exports, and hotspots that make a change set predictable. This small sample should not be treated as a causal proof; it supports the position-paper thesis that hardening the graph is the bottleneck to making write-set contracts reliable.
