# RETRACTED

Data retracted on 2026-05-12. ACG_WORKER_MAX_TOKENS defaulted to 8192/4096, causing 7 TRUNCATED_BY_MAX_TOKENS failures on pr3166-session-middleware. Comparison metrics are biased downward by truncation. The corrected baseline lives in `experiments/real_repos/starlette/runs_sonnet_v2_n5/`. See git log around the audit response doc for full chain of evidence.
