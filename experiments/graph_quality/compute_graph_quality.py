#!/usr/bin/env python3
"""Compute graph-quality summary artifacts for the hardening lane.

The script is intentionally self-contained so it can read existing context
graphs and run/analyzer JSON without modifying any experiment artifacts.
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Codebase:
    key: str
    label: str
    graph_path: str
    preferred_analysis: str | None = None
    fallback_eval_run: str | None = None
    task_id_source_eval_run: str | None = None
    realworld_analysis_candidates: tuple[str, ...] = ()


CODEBASES: tuple[Codebase, ...] = (
    Codebase(
        key="greenhouse_java",
        label="Greenhouse Java",
        graph_path="experiments/greenhouse/checkout/.acg/context_graph.json",
        preferred_analysis="experiments/greenhouse/runs/_analysis/report.json",
        task_id_source_eval_run="experiments/greenhouse/runs/eval_run_combined.json",
        fallback_eval_run="experiments/greenhouse/runs/eval_run_combined.json",
    ),
    Codebase(
        key="demo_app_ts",
        label="demo-app TS",
        graph_path="demo-app/.acg/context_graph.json",
        fallback_eval_run="experiments/demo-app/runs_gx10/eval_run_combined.json",
    ),
    Codebase(
        key="brocoders_ts",
        label="Brocoders TS",
        graph_path="experiments/microservice/nestjs-boilerplate/.acg/context_graph.json",
        preferred_analysis="experiments/microservice/runs_brocoders_local/analysis_report.json",
        fallback_eval_run=(
            "experiments/microservice/runs_brocoders_local/eval_run_combined.json"
        ),
    ),
    Codebase(
        key="realworld_ts",
        label="RealWorld TS",
        graph_path="experiments/realworld/checkout/.acg/context_graph.json",
        realworld_analysis_candidates=(
            "experiments/realworld/runs/analysis_report.json",
            "experiments/realworld/runs_blind_openrouter/analysis_report.json",
        ),
    ),
)


def rel(path: str) -> Path:
    return ROOT / path


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_language(value: str | None) -> str:
    if not value:
        return "Unknown"
    lowered = value.lower()
    if lowered == "java":
        return "Java"
    if lowered in {"typescript", "ts"}:
        return "TypeScript"
    return value


def count_file_items(files: list[Any], key: str) -> int:
    total = 0
    for item in files:
        if isinstance(item, dict):
            value = item.get(key, [])
            if isinstance(value, list):
                total += len(value)
    return total


def count_graph_mapping(graph: dict[str, Any], files: list[Any], key: str) -> int:
    value = graph.get(key)
    if isinstance(value, dict):
        return sum(len(items or []) for items in value.values() if isinstance(items, list))
    if isinstance(value, list):
        return len(value)
    return count_file_items(files, key)


def graph_stats(path: Path) -> dict[str, Any]:
    graph = load_json(path)
    files = graph.get("files", [])
    if not isinstance(files, list):
        files = []

    symbols_index = graph.get("symbols_index")
    if isinstance(symbols_index, dict):
        symbols_total = len(symbols_index)
    elif isinstance(symbols_index, list):
        symbols_total = len(symbols_index)
    else:
        symbols_total = count_file_items(files, "symbols")

    imports_total = count_graph_mapping(graph, files, "imports")
    exports_total = count_graph_mapping(graph, files, "exports")
    hotspots = graph.get("hotspots")
    if isinstance(hotspots, list):
        hotspots_total = len(hotspots)
    else:
        hotspots_total = sum(
            1 for item in files if isinstance(item, dict) and item.get("is_hotspot")
        )

    files_total = len(files)
    density = (
        (symbols_total + imports_total + exports_total) / files_total
        if files_total
        else 0.0
    )
    return {
        "language": normalize_language(graph.get("language")),
        "files_total": files_total,
        "symbols_total": symbols_total,
        "imports_total": imports_total,
        "exports_total": exports_total,
        "hotspots_total": hotspots_total,
        "graph_density": density,
    }


def flatten_eval_runs(run_doc: dict[str, Any], source_path: str) -> list[dict[str, Any]]:
    strategies = run_doc.get("strategies")
    if isinstance(strategies, dict):
        runs: list[dict[str, Any]] = []
        for name, nested in strategies.items():
            if isinstance(nested, dict):
                copied = dict(nested)
                copied.setdefault("strategy", name)
                copied["_source_path"] = source_path
                runs.append(copied)
        return runs
    copied = dict(run_doc)
    copied["_source_path"] = source_path
    return [copied]


def task_ids_from_eval_run(path: Path) -> set[str]:
    run_doc = load_json(path)
    task_ids: set[str] = set()
    for run in flatten_eval_runs(run_doc, str(path.relative_to(ROOT))):
        for task in run.get("tasks", []) or []:
            task_id = task.get("task_id")
            if task_id:
                task_ids.add(str(task_id))
    return task_ids


def metrics_from_eval_run(path: Path) -> dict[str, Any]:
    run_doc = load_json(path)
    tasks: dict[str, dict[str, set[str]]] = {}
    runs_seen = 0
    for run in flatten_eval_runs(run_doc, str(path.relative_to(ROOT))):
        runs_seen += 1
        for task in run.get("tasks", []) or []:
            task_id = str(task.get("task_id", "?"))
            item = tasks.setdefault(
                task_id, {"predicted": set(), "actual": set(), "oob": set()}
            )
            item["predicted"].update(task.get("predicted_write_files", []) or [])
            item["actual"].update(task.get("actual_changed_files", []) or [])
            item["oob"].update(task.get("out_of_bounds_files", []) or [])

    tp = fp = fn = 0
    for item in tasks.values():
        predicted = item["predicted"]
        actual = item["actual"]
        tp += len(predicted & actual)
        fp += len(predicted - actual)
        fn += len(actual - predicted)

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "source": str(path.relative_to(ROOT)),
        "source_kind": "computed_from_eval_run",
        "runs_seen": runs_seen,
        "tasks_seen": len(tasks),
    }


def metrics_from_analysis_report(
    path: Path, task_ids: set[str] | None = None
) -> dict[str, Any]:
    report = load_json(path)
    if task_ids:
        tasks = report.get("tasks", {})
        missing = sorted(task_ids - set(tasks))
        if missing:
            raise ValueError(
                f"{path.relative_to(ROOT)} is missing task metrics for {missing}"
            )
        tp = fp = fn = 0
        for task_id in sorted(task_ids):
            task = tasks[task_id]
            tp += int(task.get("true_positives", 0))
            fp += int(task.get("false_positives", 0))
            fn += int(task.get("false_negatives", 0))
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision + recall
            else 0.0
        )
        return {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "true_positives": tp,
            "false_positives": fp,
            "false_negatives": fn,
            "source": str(path.relative_to(ROOT)),
            "source_kind": "analysis_report_filtered_tasks",
            "task_ids": sorted(task_ids),
        }

    overall = report.get("overall", {})
    return {
        "precision": float(overall["precision"]),
        "recall": float(overall["recall"]),
        "f1": float(overall["f1"]),
        "source": str(path.relative_to(ROOT)),
        "source_kind": "analysis_report_overall",
    }


def rg_analysis_hits() -> list[str]:
    try:
        proc = subprocess.run(
            ["rg", "--files"],
            cwd=ROOT,
            check=True,
            text=True,
            capture_output=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        hits = [
            str(path.relative_to(ROOT))
            for path in ROOT.rglob("*")
            if path.is_file()
            and (
                path.name == "analysis_report.json"
                or str(path).endswith("_analysis/report.json")
            )
        ]
        return sorted(hits)

    return sorted(
        line
        for line in proc.stdout.splitlines()
        if "analysis_report" in line or line.endswith("_analysis/report.json")
    )


def predictor_metrics(codebase: Codebase) -> dict[str, Any]:
    if codebase.realworld_analysis_candidates:
        candidates = [
            metrics_from_analysis_report(rel(path))
            for path in codebase.realworld_analysis_candidates
            if rel(path).exists()
        ]
        if not candidates:
            raise FileNotFoundError(
                "missing RealWorld analysis: "
                + ", ".join(codebase.realworld_analysis_candidates)
            )
        selected = min(candidates, key=lambda item: (item["f1"], item["precision"]))
        selected["selection"] = "lowest_f1_harder_case"
        selected["candidates"] = [
            {
                "source": item["source"],
                "precision": item["precision"],
                "recall": item["recall"],
                "f1": item["f1"],
            }
            for item in candidates
        ]
        return selected

    preferred = rel(codebase.preferred_analysis) if codebase.preferred_analysis else None
    if preferred and preferred.exists():
        task_ids = None
        if codebase.task_id_source_eval_run:
            task_source = rel(codebase.task_id_source_eval_run)
            if task_source.exists():
                task_ids = task_ids_from_eval_run(task_source)
        return metrics_from_analysis_report(preferred, task_ids=task_ids)

    fallback = rel(codebase.fallback_eval_run) if codebase.fallback_eval_run else None
    if fallback and fallback.exists():
        metrics = metrics_from_eval_run(fallback)
        if codebase.preferred_analysis:
            metrics["missing_preferred_analysis"] = codebase.preferred_analysis
        return metrics

    missing = codebase.preferred_analysis or codebase.fallback_eval_run or "unknown"
    raise FileNotFoundError(f"missing predictor analysis for {codebase.label}: {missing}")


def write_failure(lines: list[str]) -> None:
    body = ["# Graph Quality Failure", "", *lines, ""]
    (OUT_DIR / "FAILURE.md").write_text("\n".join(body), encoding="utf-8")


def make_report_json(rows: list[dict[str, Any]], analysis_hits: list[str]) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "codebases": rows,
        "analysis_search_hits": analysis_hits,
    }


def make_markdown(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Graph Quality Report",
        "",
        "| Codebase | Language | Files | Symbols | Imports | Exports | Hotspots | Density | Precision | Recall | F1 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        predictor = row["predictor"]
        lines.append(
            "| {label} | {language} | {files_total} | {symbols_total} | "
            "{imports_total} | {exports_total} | {hotspots_total} | "
            "{density:.2f} | {precision:.4f} | {recall:.4f} | {f1:.4f} |".format(
                label=row["label"],
                language=row["language"],
                files_total=row["files_total"],
                symbols_total=row["symbols_total"],
                imports_total=row["imports_total"],
                exports_total=row["exports_total"],
                hotspots_total=row["hotspots_total"],
                density=row["graph_density"],
                precision=predictor["precision"],
                recall=predictor["recall"],
                f1=predictor["f1"],
            )
        )
    lines.extend(
        [
            "",
            "Across these four codebases, predictor quality is best read as a function of the upstream graph rather than only the downstream contract: the contract can block out-of-scope writes, but its precision depends on whether the graph captures the files, symbols, imports, exports, and hotspots that make a change set predictable. This small sample should not be treated as a causal proof; it supports the position-paper thesis that hardening the graph is the bottleneck to making write-set contracts reliable.",
            "",
        ]
    )
    return "\n".join(lines)


def load_font(size: int = 16) -> ImageFont.ImageFont:
    candidates = (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Helvetica.ttf",
        "/Library/Fonts/Arial.ttf",
    )
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def draw_scatter(rows: list[dict[str, Any]], out_path: Path) -> None:
    width, height = 1000, 650
    margin_left, margin_right = 95, 45
    margin_top, margin_bottom = 55, 95
    plot_w = width - margin_left - margin_right
    plot_h = height - margin_top - margin_bottom

    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    font = load_font(16)
    small = load_font(14)
    title_font = load_font(22)

    xs = [row["graph_density"] for row in rows]
    x_min = max(0.0, math.floor(min(xs) - 1.0))
    x_max = math.ceil(max(xs) + 1.0)
    y_min, y_max = 0.0, 1.0

    def sx(value: float) -> float:
        return margin_left + (value - x_min) / (x_max - x_min) * plot_w

    def sy(value: float) -> float:
        return margin_top + (y_max - value) / (y_max - y_min) * plot_h

    axis = (50, 57, 66)
    grid = (226, 232, 240)
    text = (30, 41, 59)
    colors = {
        "greenhouse_java": (214, 76, 75),
        "demo_app_ts": (48, 121, 214),
        "brocoders_ts": (34, 150, 104),
        "realworld_ts": (142, 91, 186),
    }

    draw.text((margin_left, 18), "Graph Density vs Predictor F1", fill=text, font=title_font)
    draw.line(
        (margin_left, margin_top + plot_h, margin_left + plot_w, margin_top + plot_h),
        fill=axis,
        width=2,
    )
    draw.line((margin_left, margin_top, margin_left, margin_top + plot_h), fill=axis, width=2)

    for i in range(int(x_min), int(x_max) + 1):
        x = sx(i)
        draw.line((x, margin_top, x, margin_top + plot_h), fill=grid, width=1)
        draw.text((x - 7, margin_top + plot_h + 10), str(i), fill=text, font=small)

    for tick in [0.0, 0.25, 0.5, 0.75, 1.0]:
        y = sy(tick)
        draw.line((margin_left, y, margin_left + plot_w, y), fill=grid, width=1)
        draw.text((margin_left - 58, y - 9), f"{tick:.2f}", fill=text, font=small)

    draw.text(
        (margin_left + plot_w / 2 - 55, height - 45),
        "Graph density",
        fill=text,
        font=font,
    )
    y_label = Image.new("RGBA", (130, 28), (255, 255, 255, 0))
    y_label_draw = ImageDraw.Draw(y_label)
    y_label_draw.text((0, 0), "Predictor F1", fill=text, font=font)
    y_label = y_label.rotate(90, expand=True)
    image.paste(y_label, (18, margin_top + plot_h // 2 - 65), y_label)

    label_offsets = {
        "greenhouse_java": (-125, -22),
        "demo_app_ts": (12, -28),
        "brocoders_ts": (12, 8),
        "realworld_ts": (12, -4),
    }
    for row in rows:
        x = sx(row["graph_density"])
        y = sy(row["predictor"]["f1"])
        color = colors.get(row["key"], (64, 64, 64))
        draw.ellipse((x - 7, y - 7, x + 7, y + 7), fill=color, outline=(20, 20, 20), width=1)
        dx, dy = label_offsets.get(row["key"], (10, -10))
        label = row["label"].replace(" TS", "")
        draw.text((x + dx, y + dy), label, fill=text, font=small)

    image.save(out_path)


def write_done(rows: list[dict[str, Any]]) -> None:
    java = next(row for row in rows if row["key"] == "greenhouse_java")
    type_scripts = [row for row in rows if row["language"] == "TypeScript"]
    high_ts = max(type_scripts, key=lambda row: row["predictor"]["f1"])
    done = (
        "Predictor F1 ranges from "
        f"{java['predictor']['f1']:.4f} (Java, density {java['graph_density']:.2f}) "
        f"to {high_ts['predictor']['f1']:.4f} "
        f"(TypeScript, density {high_ts['graph_density']:.2f}); "
        "the position paper's argument is that the field's bottleneck is "
        "upstream of the contract — it is the graph."
    )
    (OUT_DIR / "DONE.md").write_text(done + "\n", encoding="utf-8")


def main() -> int:
    missing_graphs = [
        codebase.graph_path for codebase in CODEBASES if not rel(codebase.graph_path).exists()
    ]
    if missing_graphs:
        write_failure(["Missing context graphs:", *[f"- {path}" for path in missing_graphs]])
        return 1

    analysis_hits = rg_analysis_hits()
    rows: list[dict[str, Any]] = []
    missing_analysis: list[str] = []
    for codebase in CODEBASES:
        row = {
            "key": codebase.key,
            "label": codebase.label,
            "context_graph": codebase.graph_path,
            **graph_stats(rel(codebase.graph_path)),
        }
        try:
            predictor = predictor_metrics(codebase)
        except (FileNotFoundError, KeyError, ValueError) as exc:
            missing_analysis.append(str(exc))
            continue
        row["predictor"] = predictor
        rows.append(row)

    if missing_analysis:
        write_failure(
            [
                "Missing predictor analysis:",
                *[f"- {item}" for item in missing_analysis],
                "",
                "Search hits from `rg --files | rg 'analysis_report|_analysis/report.json'`:",
                *[f"- {hit}" for hit in analysis_hits],
            ]
        )
        return 1

    rows.sort(key=lambda row: row["label"])
    report = make_report_json(rows, analysis_hits)
    (OUT_DIR / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (OUT_DIR / "report.md").write_text(make_markdown(rows), encoding="utf-8")
    draw_scatter(rows, OUT_DIR / "scatter.png")
    write_done(rows)

    failure = OUT_DIR / "FAILURE.md"
    if failure.exists():
        failure.unlink()
    return 0


if __name__ == "__main__":
    sys.exit(main())
