"""Perf trace sampling for baseline-vs-optimized runtime demos."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SAMPLE_INTERVAL_S = 0.25
JIFFIES_PER_SECOND = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


class GpuSampler:
    """Background sampler for GPU memory and SM utilization."""

    def __init__(self, interval_s: float = SAMPLE_INTERVAL_S) -> None:
        self._interval_s = interval_s
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._mem_mib: list[float] = []
        self._sm_pct: list[float] = []

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="acg-gpu-sampler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if not self._thread:
            return
        self._stop_event.set()
        self._thread.join(timeout=max(1.0, self._interval_s * 4))
        self._thread = None

    def peak_mem_gib(self) -> float:
        with self._lock:
            if not self._mem_mib:
                return 0.0
            return round(max(self._mem_mib) / 1024.0, 3)

    def avg_sm_pct(self) -> float:
        with self._lock:
            if not self._sm_pct:
                return 0.0
            return round(sum(self._sm_pct) / len(self._sm_pct), 3)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            mem_mib, sm_pct = self._sample_once()
            with self._lock:
                self._mem_mib.append(mem_mib)
                self._sm_pct.append(sm_pct)
            self._stop_event.wait(self._interval_s)

    def _sample_once(self) -> tuple[float, float]:
        nvml_sample = self._sample_nvml()
        if nvml_sample is not None:
            return nvml_sample
        smi_sample = self._sample_nvidia_smi()
        if smi_sample is not None:
            return smi_sample
        return 0.0, 0.0

    def _sample_nvml(self) -> tuple[float, float] | None:
        try:
            import pynvml  # type: ignore[import-not-found]
        except ImportError:
            return None
        try:
            pynvml.nvmlInit()
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
            util = pynvml.nvmlDeviceGetUtilizationRates(handle)
            return float(mem.used) / 1024.0 / 1024.0, float(util.gpu)
        except Exception:
            return None

    def _sample_nvidia_smi(self) -> tuple[float, float] | None:
        try:
            proc = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=memory.used,utilization.gpu",
                    "--format=csv,noheader,nounits",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=1.0,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if proc.returncode != 0 or not proc.stdout.strip():
            return None
        line = proc.stdout.strip().splitlines()[0]
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            return None
        try:
            return float(parts[0]), float(parts[1])
        except ValueError:
            return None


class GraceCpuSampler:
    """Background Linux process-tree CPU sampler."""

    def __init__(self, pid: int | None = None, interval_s: float = SAMPLE_INTERVAL_S) -> None:
        self._pid = pid or os.getpid()
        self._interval_s = interval_s
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._samples: list[float] = []
        self._last_cpu_jiffies: int | None = None
        self._last_wall: float | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._last_cpu_jiffies = self._read_process_tree_jiffies()
        self._last_wall = time.perf_counter()
        self._thread = threading.Thread(target=self._run, name="acg-grace-cpu-sampler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if not self._thread:
            return
        self._stop_event.set()
        self._thread.join(timeout=max(1.0, self._interval_s * 4))
        self._thread = None

    def avg_cpu_pct(self) -> float:
        with self._lock:
            if not self._samples:
                return 0.0
            return round(sum(self._samples) / len(self._samples), 3)

    def _run(self) -> None:
        while not self._stop_event.wait(self._interval_s):
            self._record_sample()

    def _record_sample(self) -> None:
        current_jiffies = self._read_process_tree_jiffies()
        current_wall = time.perf_counter()
        if current_jiffies is None:
            return
        if self._last_cpu_jiffies is not None and self._last_wall is not None:
            delta_cpu = max(0, current_jiffies - self._last_cpu_jiffies)
            delta_wall = max(0.0, current_wall - self._last_wall)
            if delta_wall > 0:
                pct = (delta_cpu / JIFFIES_PER_SECOND) / delta_wall * 100.0
                with self._lock:
                    self._samples.append(pct)
        self._last_cpu_jiffies = current_jiffies
        self._last_wall = current_wall

    def _read_process_tree_jiffies(self) -> int | None:
        if not Path("/proc").exists():
            return None
        total = 0
        for pid in self._process_tree_pids():
            jiffies = self._read_pid_jiffies(pid)
            if jiffies is not None:
                total += jiffies
        return total

    def _process_tree_pids(self) -> set[int]:
        root = self._pid
        pids = {root}
        try:
            for entry in Path("/proc").iterdir():
                if not entry.name.isdigit():
                    continue
                pid = int(entry.name)
                parent = self._read_ppid(pid)
                if parent in pids:
                    pids.add(pid)
        except OSError:
            return {root}
        return pids

    def _read_ppid(self, pid: int) -> int | None:
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
        except OSError:
            return None
        parts = stat.rsplit(")", 1)
        if len(parts) != 2:
            return None
        fields = parts[1].strip().split()
        if len(fields) < 2:
            return None
        try:
            return int(fields[1])
        except ValueError:
            return None

    def _read_pid_jiffies(self, pid: int) -> int | None:
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
        except OSError:
            return None
        parts = stat.rsplit(")", 1)
        if len(parts) != 2:
            return None
        fields = parts[1].strip().split()
        if len(fields) < 15:
            return None
        try:
            utime = int(fields[11])
            stime = int(fields[12])
            cutime = int(fields[13])
            cstime = int(fields[14])
        except ValueError:
            return None
        return utime + stime + cutime + cstime


@dataclass
class _TaskSpan:
    task_id: str
    group_id: int
    started_at: datetime
    completed_at: datetime | None = None
    first_token_at: datetime | None = None
    input_tokens: int = 0
    output_tokens: int = 0


class PerfRecorder:
    """Collect runtime perf metrics and dump the perf_trace schema."""

    def __init__(
        self,
        *,
        config: dict[str, Any],
        lockfile: str,
        gpu_sampler: GpuSampler | None = None,
        cpu_sampler: GraceCpuSampler | None = None,
    ) -> None:
        self._config = dict(config)
        self._lockfile = lockfile
        self._gpu = gpu_sampler or GpuSampler()
        self._cpu = cpu_sampler or GraceCpuSampler()
        self._tasks: dict[str, _TaskSpan] = {}
        self._started_at: datetime | None = None
        self._stopped_at: datetime | None = None
        self._running = False

    def start(self) -> None:
        if self._running:
            return
        self._started_at = _utc_now()
        self._running = True
        self._gpu.start()
        self._cpu.start()

    def stop(self) -> None:
        if not self._running:
            return
        self._gpu.stop()
        self._cpu.stop()
        self._stopped_at = _utc_now()
        self._running = False

    def mark_task_start(self, task_id: str, group_id: int) -> None:
        self._tasks[task_id] = _TaskSpan(task_id=task_id, group_id=group_id, started_at=_utc_now())

    def mark_first_token(self, task_id: str) -> None:
        span = self._tasks.get(task_id)
        if span and span.first_token_at is None:
            span.first_token_at = _utc_now()

    def mark_task_end(self, task_id: str, *, input_tokens: int = 0, output_tokens: int = 0) -> None:
        span = self._tasks.get(task_id)
        if not span:
            return
        span.completed_at = _utc_now()
        if span.first_token_at is None:
            span.first_token_at = span.completed_at
        span.input_tokens = max(0, input_tokens)
        span.output_tokens = max(0, output_tokens)

    def dump(self, path: Path) -> None:
        payload = self.to_dict()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, default=str) + "\n")

    def to_dict(self) -> dict[str, Any]:
        started = self._started_at or _utc_now()
        stopped = self._stopped_at or _utc_now()
        tasks = [self._task_to_dict(span) for span in self._tasks.values()]
        total_input = sum(int(t["input_tokens"]) for t in tasks)
        total_output = sum(int(t["output_tokens"]) for t in tasks)
        total_wall_s = max(0.0, (stopped - started).total_seconds())
        return {
            "version": "1.0",
            "generated_at": _iso(_utc_now()),
            "lockfile": self._lockfile,
            "config": self._config,
            "tasks": tasks,
            "global": {
                "total_wall_s": round(total_wall_s, 3),
                "total_input_tokens": total_input,
                "total_output_tokens": total_output,
                "tokens_per_s_overall": _rate(total_output, total_wall_s),
                "peak_gpu_mem_gib": self._gpu.peak_mem_gib(),
                "grace_cpu_pct_avg": self._cpu.avg_cpu_pct(),
            },
        }

    def _task_to_dict(self, span: _TaskSpan) -> dict[str, Any]:
        completed = span.completed_at or _utc_now()
        first_token = span.first_token_at or completed
        wall_s = max(0.0, (completed - span.started_at).total_seconds())
        first_token_s = max(0.0, (first_token - span.started_at).total_seconds())
        return {
            "task_id": span.task_id,
            "group_id": span.group_id,
            "started_at": _iso(span.started_at),
            "completed_at": _iso(completed),
            "wall_s": round(wall_s, 3),
            "first_token_s": round(first_token_s, 3),
            "input_tokens": span.input_tokens,
            "output_tokens": span.output_tokens,
            "tokens_per_s_in": _rate(span.input_tokens, wall_s),
            "tokens_per_s_out": _rate(span.output_tokens, wall_s),
            "peak_gpu_mem_gib": self._gpu.peak_mem_gib(),
            "blackwell_sm_pct_avg": self._gpu.avg_sm_pct(),
            "grace_cpu_pct_avg": self._cpu.avg_cpu_pct(),
        }


def _rate(count: int, wall_s: float) -> float:
    if count <= 0 or wall_s <= 0:
        return 0.0
    return round(count / wall_s, 3)
