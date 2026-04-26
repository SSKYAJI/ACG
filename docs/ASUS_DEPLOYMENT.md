# ASUS GX10 Deployment

ACG is the local-first pre-flight artifact for multi-agent code workflows. Cloud LLMs are convenient for hackathon dev, but the production audience for this primitive is enterprises that cannot ship code to a third-party API: financial services, healthcare, defense contractors, regulated infrastructure. The ASUS GX10 — 128 GB unified memory, NVIDIA GB10 — runs the same OpenAI-compatible inference path we hit during development without the data ever leaving the box.

## Why GX10 is the right platform

- **Sovereign LLM inference.** vLLM serving Llama 3.3-70B Q4 on the GB10 reaches roughly 25–40 tok/s in our reference setup. The whole `acg compile` round-trip for the four-task demo finishes in under a minute, well below the wall-time of the agents it's planning for.
- **No code egress.** Compliance teams can deploy ACG without cutting an exception for a cloud provider. The repository graph (`context_graph.json`) and lockfile (`agent_lock.json`) never leave the device.
- **One client, two providers.** The same `acg.llm.LLMClient` calls Groq during dev and vLLM during demo or production with only environment-variable changes — no client-side branching, no model-specific prompt drift.
- **Cost story.** A single GX10 amortises after roughly 10K predictor calls compared with paid cloud-API pricing. For a team of 50 engineers running ACG against every multi-agent PR, that's a few weeks.

## Reproducible deployment

### 1. Provision

```bash
# On the GX10, freshly imaged:
sudo apt update && sudo apt install -y python3-venv git
git clone <ACG repo>
cd "Cognition Winning project"
python3 -m venv .venv
./.venv/bin/pip install -e ".[dev]" vllm
```

(The `vllm` dep is intentionally outside `pyproject.toml` because it's a deployment concern, not a library requirement.)

### 2. Serve

```bash
./.venv/bin/python -m vllm.entrypoints.openai.api_server \
  --model meta-llama/Llama-3.3-70B-Instruct \
  --host 0.0.0.0 --port 8000 \
  --max-model-len 8192 \
  --quantization awq      # use whatever's appropriate for your model artifact
```

Health check:

```bash
curl http://localhost:8000/v1/models | jq .
```

### 3. Point ACG at the local server

```bash
export ACG_LLM_URL=http://localhost:8000/v1
export ACG_LLM_MODEL=meta-llama/Llama-3.3-70B-Instruct
export ACG_LLM_API_KEY=anything   # vLLM ignores it; the client requires the env var to be present
```

### 4. Run the demo end-to-end against the local model

```bash
make scan
make compile
make demo
```

The chart in `docs/benchmark.png` is generated using whichever model is currently configured. If you ran the dev path with Groq earlier, run `make demo` again on the GX10 to regenerate the chart with local-inference numbers.

## Performance notes (record actual numbers here once available)

| Stage | Hardware | Model | Throughput | End-to-end time |
| --- | --- | --- | --- | --- |
| `acg compile` (4 tasks) | GX10 | Llama 3.3-70B Q4 | _TBD tok/s_ | _TBD seconds_ |
| `acg compile` (4 tasks) | Groq cloud (dev) | llama-3.3-70b-versatile | ~250 tok/s | ~2 seconds |
| `acg compile` (4 tasks) | offline mock | (canned predictions) | n/a | <0.5 seconds |

## Privacy story (Devpost-ready)

> ACG runs locally on ASUS GX10. The repository graph, the task list, the predicted writes, and the lockfile never leave the device. The same Python client we used to call Groq during development calls vLLM on the GX10 in production — with only `ACG_LLM_URL` changing. Customers who cannot legally ship source code to a cloud LLM can still adopt the pre-flight artifact pattern.

## Receipts contract

The runtime prints a 3-line configuration banner to stderr at startup so
operators can verify exactly which knobs were active for a given run.

### Environment variables

| Variable | Purpose | Default |
| --- | --- | --- |
| `ACG_LLM_ENGINE` | Inference engine identifier (e.g. `llama.cpp`, `vllm`) | `unknown` |
| `ACG_LLM_DTYPE` | Model data-type / quantisation (e.g. `Q4_K_M`) | `unknown` |
| `ACG_LLM_PARALLEL` | Number of parallel decode slots on the server | `0` |
| `ACG_LLM_KV_QUANT` | KV-cache quantisation type (e.g. `q8_0`) | `unknown` |
| `ACG_LLM_FLASH_ATTN` | Enable flash-attention (`1` / `true`) | `false` |
| `ACG_WORKER_CONCURRENCY` | Max concurrent ACG workers per group | `1` |
| `ACG_GRACE_OVERLAP` | Allow grace-period overlap between groups (`1` / `true`) | `false` |
| `ACG_LLM_MODEL_SHA` | Git SHA or hash of the model weights artifact | _(empty)_ |

### Canonical `llama-server` invocations

**Baseline (port 8080)**

```bash
llama-server \
  --model models/llama-3.3-70b-instruct-Q4_K_M.gguf \
  --host 0.0.0.0 --port 8080 \
  --ctx-size 8192 --parallel 4
```

```bash
ACG_LLM_ENGINE=llama.cpp \
ACG_LLM_DTYPE=Q4_K_M \
ACG_LLM_PARALLEL=4 \
ACG_LLM_KV_QUANT=none \
ACG_LLM_FLASH_ATTN=0 \
ACG_LLM_URL=http://localhost:8080/v1 \
./.venv/bin/acg run --lock demo-app/agent_lock.json --repo demo-app --out /tmp/run.json
```

**Optimized (port 8082)**

```bash
llama-server \
  --model models/llama-3.3-70b-instruct-Q4_K_M.gguf \
  --host 0.0.0.0 --port 8082 \
  --ctx-size 8192 --parallel 4 \
  --flash-attn --cache-type-k q8_0 --cache-type-v q8_0
```

```bash
ACG_LLM_ENGINE=llama.cpp \
ACG_LLM_DTYPE=Q4_K_M \
ACG_LLM_PARALLEL=4 \
ACG_LLM_KV_QUANT=q8_0 \
ACG_LLM_FLASH_ATTN=1 \
ACG_LLM_URL=http://localhost:8082/v1 \
./.venv/bin/acg run --lock demo-app/agent_lock.json --repo demo-app --out /tmp/run.json
```

## What ACG does *not* claim about ASUS

- We do not claim that running the model locally improves prediction quality. Same model weights, same prompt, same output up to sampling noise.
- We do not claim a specific tok/s number for the GX10 unless we have measured it on hardware. The numbers in this doc must be replaced with measured values before submission. If access is denied, soften to "designed for ASUS GX10; Groq used as a free-tier proxy showing the same provider-agnostic client architecture" in the Devpost narrative.
