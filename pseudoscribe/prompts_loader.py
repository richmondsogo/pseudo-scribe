"""Prompt loading and context rendering utilities."""

from __future__ import annotations

import json
from typing import Any

from pseudoscribe.config import get_prompts_dir


def load_prompt(name: str) -> str:
    """Load a prompt template from the global prompts directory."""
    path = get_prompts_dir() / name
    if not path.exists():
        raise FileNotFoundError(f"Prompt template not found: {path}")
    return path.read_text(encoding="utf-8")


def render_prompt(template: str, context: dict[str, Any]) -> str:
    """Substitute {{placeholder}} variables with string or JSON-serialized context values."""
    result = template
    for k, v in context.items():
        placeholder = f"{{{{{k}}}}}"
        if isinstance(v, (dict, list)):
            repl = json.dumps(v, indent=2, ensure_ascii=False)
        else:
            repl = str(v)
        result = result.replace(placeholder, repl)
    return result
