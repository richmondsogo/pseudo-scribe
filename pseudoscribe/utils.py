"""General utilities: safe console printing, JSON extraction, and atomic file I/O."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pseudoscribe.models import Course

RUN_WARNINGS: list[str] = []


def _safe_print(msg: str, file: Any = None) -> None:
    """Print to console safely, handling potential encoding issues on non-UTF-8 terminals."""
    target = file if file is not None else sys.stdout
    try:
        print(msg, file=target)
    except UnicodeEncodeError:
        encoding = getattr(target, "encoding", "ascii") or "ascii"
        print(msg.encode(encoding, errors="replace").decode(encoding), file=target)


def info(msg: str) -> None:
    """Standard informational logger."""
    _safe_print(msg)


def error(msg: str) -> None:
    """Standard error logger to stderr."""
    _safe_print(f"ERROR: {msg}", file=sys.stderr)


def warn(msg: str) -> None:
    """Record a run-wide warning and output to stderr without terminating."""
    RUN_WARNINGS.append(msg)
    _safe_print(f"WARNING: {msg}", file=sys.stderr)


def save_run_warnings(c: Course) -> None:
    """Persist all collected run warnings to run_warnings.json in the course folder."""
    if RUN_WARNINGS:
        save_json(
            c.path / "run_warnings.json",
            RUN_WARNINGS,
        )


def load_json(path: Path) -> Any:
    """Load JSON from disk, tolerating Windows UTF-8 BOM signatures."""
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def save_json(path: Path, data: Any) -> None:
    """Atomically write JSON data to disk via a temporary file to prevent corruption."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    tmp_path.replace(path)


def _extract_json(text: str) -> Any:
    """Extract and parse a JSON object from raw LLM output.

    Strips ```json code fences if present. If that fails, scans for the outermost
    matching curly braces. Raises ValueError if no valid JSON structure is found.
    """
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.lower().startswith("json"):
            t = t[4:]
        t = t.strip()

    try:
        return json.loads(t)
    except Exception:
        pass

    start = t.find("{")
    if start != -1:
        depth = 0
        for i in range(start, len(t)):
            if t[i] == "{":
                depth += 1
            elif t[i] == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(t[start : i + 1])

    raise ValueError("No JSON object found in response")
