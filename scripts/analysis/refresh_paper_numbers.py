#!/usr/bin/env python3
"""Regenerate the Starlette Kimi K2.6 block in ``experiments/PAPER_NUMBERS.md``.

Looks for ``aggregate.json`` under ``experiments/real_repos/starlette/`` (e.g.
``runs_sonnet_v2_n5/``). If none exists, exits 0 without modifying the doc.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SECTION_HEADING = "## starlette (Python, Kimi K2.6)"
ROOT = Path(__file__).resolve().parents[2]
PAPER = ROOT / "experiments" / "PAPER_NUMBERS.md"
STARLETTE = ROOT / "experiments" / "real_repos" / "starlette"


def _find_aggregate_json() -> Path | None:
    if not STARLETTE.is_dir():
        return None
    preferred = STARLETTE / "runs_sonnet_v2_n5" / "aggregate.json"
    if preferred.is_file():
        return preferred
    matches = sorted(STARLETTE.rglob("aggregate.json"))
    return matches[0] if matches else None


def _json_to_bullets(obj: object, depth: int = 0, max_depth: int = 3) -> list[str]:
    if depth > max_depth:
        return ["  " * depth + "- …"]
    lines: list[str] = []
    if isinstance(obj, dict):
        for k, v in sorted(obj.items(), key=lambda kv: str(kv[0])):
            if isinstance(v, (dict, list)) and v:
                lines.append(f"{'  ' * depth}- `{k}`:")
                lines.extend(_json_to_bullets(v, depth + 1, max_depth))
            else:
                lines.append(f"{'  ' * depth}- `{k}`: {_scalar(v)}")
    elif isinstance(obj, list):
        for i, item in enumerate(obj[:30]):
            lines.append(f"{'  ' * depth}- [{i}] {_scalar(item)}")
            if isinstance(item, (dict, list)) and item and depth < max_depth:
                lines.extend(_json_to_bullets(item, depth + 1, max_depth))
        if len(obj) > 30:
            lines.append(f"{'  ' * depth}- … ({len(obj) - 30} more)")
    else:
        lines.append(f"{'  ' * depth}- {_scalar(obj)}")
    return lines


def _scalar(v: object) -> str:
    if isinstance(v, str):
        return repr(v)[:500]
    if v is None or isinstance(v, (bool, int, float)):
        return repr(v)
    return type(v).__name__


def _render_section(agg_path: Path, data: object) -> str:
    rel = str(agg_path.relative_to(ROOT))
    body = "\n".join(_json_to_bullets(data))
    return (
        f"{SECTION_HEADING}\n\n"
        f"- Source aggregate: `{rel}`.\n"
        f"- Regenerate with `python scripts/analysis/refresh_paper_numbers.py`.\n\n"
        f"{body}\n"
    )


def _replace_section(text: str, new_block: str) -> str:
    pattern = re.compile(
        rf"^{re.escape(SECTION_HEADING)}\s*\n.*?(?=^## |\Z)",
        re.MULTILINE | re.DOTALL,
    )
    if pattern.search(text):
        return pattern.sub(new_block.rstrip() + "\n", text, count=1)
    if text and not text.endswith("\n"):
        text += "\n"
    return text.rstrip() + "\n\n" + new_block


def main() -> int:
    agg = _find_aggregate_json()
    if agg is None:
        print("No starlette aggregate.json found; leaving PAPER_NUMBERS.md unchanged.", file=sys.stderr)
        return 0
    data = json.loads(agg.read_text(encoding="utf-8"))
    block = _render_section(agg, data)
    text = PAPER.read_text(encoding="utf-8")
    PAPER.write_text(_replace_section(text, block), encoding="utf-8")
    print(f"Updated {PAPER.relative_to(ROOT)} from {agg.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
