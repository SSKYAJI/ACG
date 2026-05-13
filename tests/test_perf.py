"""Perf tracing tests for the baseline-vs-optimized demo."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import jsonschema

from acg.perf import GpuSampler, PerfRecorder


class NoopSampler:
    def __init__(self) -> None:
        self.starts = 0
        self.stops = 0

    def start(self) -> None:
        self.starts += 1

    def stop(self) -> None:
        self.stops += 1

    def peak_mem_gib(self) -> float:
        return 0.0

    def avg_sm_pct(self) -> float:
        return 0.0

    def avg_cpu_pct(self) -> float:
        return 0.0


def _perf_config() -> dict[str, object]:
    return {
        "engine": "mock",
        "dtype": "q4_0",
        "parallel": 1,
        "kv_cache_quant": "fp16",
        "flash_attn": False,
        "worker_concurrency": 1,
        "grace_overlap": False,
        "model_id": "gemma",
        "model_sha": "",
    }


def test_perf_recorder_writes_schema_valid_trace(tmp_path: Path) -> None:
    recorder = PerfRecorder(
        config=_perf_config(),
        lockfile="demo-app/agent_lock.json",
        gpu_sampler=NoopSampler(),  # type: ignore[arg-type]
        cpu_sampler=NoopSampler(),  # type: ignore[arg-type]
    )
    recorder.start()
    recorder.mark_task_start("oauth", 1)
    time.sleep(0.01)
    recorder.mark_first_token("oauth")
    recorder.mark_task_end("oauth", input_tokens=100, output_tokens=25)
    recorder.stop()

    out = tmp_path / "perf_trace.json"
    recorder.dump(out)
    payload = json.loads(out.read_text())
    schema = json.loads(
        (Path(__file__).resolve().parent.parent / "schema" / "perf_trace.schema.json").read_text()
    )

    jsonschema.validate(payload, schema)
    assert payload["tasks"][0]["tokens_per_s_out"] > 0
    assert payload["global"]["total_output_tokens"] == 25


def test_perf_recorder_start_stop_is_idempotent() -> None:
    gpu = NoopSampler()
    cpu = NoopSampler()
    recorder = PerfRecorder(
        config=_perf_config(),
        lockfile="demo-app/agent_lock.json",
        gpu_sampler=gpu,  # type: ignore[arg-type]
        cpu_sampler=cpu,  # type: ignore[arg-type]
    )

    recorder.start()
    recorder.start()
    recorder.stop()
    recorder.stop()

    assert gpu.starts == 1
    assert gpu.stops == 1
    assert cpu.starts == 1
    assert cpu.stops == 1


def test_gpu_sampler_noops_without_hardware(monkeypatch) -> None:
    def fake_run(*args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        raise OSError("nvidia-smi unavailable")

    monkeypatch.setattr("subprocess.run", fake_run)
    sampler = GpuSampler(interval_s=0.01)

    before = threading.active_count()
    sampler.start()
    sampler.start()
    time.sleep(0.03)
    sampler.stop()
    sampler.stop()

    deadline = time.time() + 2.0
    while threading.active_count() > before and time.time() < deadline:
        time.sleep(0.01)

    assert sampler.peak_mem_gib() == 0.0
    assert sampler.avg_sm_pct() == 0.0
    assert threading.active_count() <= before
