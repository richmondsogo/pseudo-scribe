"""Terminology management, conflict detection, and vocabulary registry extraction."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pseudoscribe.config import ENABLE_CONFLICT_CHECK
from pseudoscribe.models import TERMINOLOGY_LOCK, Course, require_course_files
from pseudoscribe.prompts_loader import load_prompt, render_prompt
from pseudoscribe.providers import call_llm
from pseudoscribe.utils import _extract_json, info, load_json, save_json, warn


def load_terminology(path: Path) -> dict[str, Any]:
    """Load terminology registry from JSON, returning empty structure if not found."""
    if not path.exists():
        return {"terms": {}}
    return load_json(path)


def save_terminology(path: Path, data: dict[str, Any]) -> None:
    """Atomically persist terminology registry to disk."""
    tmp = path.with_suffix(".tmp.json")
    save_json(tmp, data)
    tmp.replace(path)


def definitions_conflict(existing_def: str | None, new_def: str | None) -> tuple[bool, str]:
    """Compare two definitions using an LLM to decide whether they conflict."""
    if not existing_def or not new_def:
        return False, "one or both definitions empty -> no conflict"

    prompt = f"""
You are a terminology conflict checker. Decide whether the two definitions below describe the same technical concept or are in substantive conflict.

Existing definition:
{existing_def}

New proposed definition:
{new_def}

Answer with a JSON object with fields:
- conflict: true|false
- reasoning: short explanation (one or two sentences)

Be conservative: if uncertain, set conflict=true.
"""
    try:
        resp = call_llm(prompt, temperature=0.0)
    except Exception as e:
        return False, f"conflict check unavailable ({e}); keeping existing definition"

    try:
        parsed = _extract_json(resp)
        if isinstance(parsed, dict):
            return bool(parsed.get("conflict")), parsed.get("reasoning", "")
        lc = resp.strip().lower()
        if "conflict" in lc and "true" in lc:
            return True, resp
        if "conflict" in lc and "false" in lc:
            return False, resp
        return True, "Unclear from checker response; conservatively flagging conflict"
    except Exception:
        return True, "Failed to parse checker response; conservatively flagging conflict"


def _merge_chapter_terms(c: Course, chapter_number: int, proposed_terms: dict[str, Any]) -> int:
    """Thread-safe merge of a chapter's proposed terms into terminology.json.

    Auto-resolves conflicts by preserving the first recorded definition.
    Returns the number of newly added terms.
    """
    with TERMINOLOGY_LOCK:
        terminology_json = load_terminology(c.terminology_path)
        loaded_terms = terminology_json.get("terms", {})
        added = 0
        for term_key, term_val in proposed_terms.items():
            if isinstance(term_val, str):
                pref = term_val
                ddef = None
                status = "undefined"
            elif isinstance(term_val, dict):
                pref = term_val.get("preferred_term") or term_key
                ddef = term_val.get("definition")
                status = term_val.get("definition_status") or ("defined" if ddef else "undefined")
            else:
                continue

            if pref in loaded_terms:
                existing = loaded_terms[pref]
                if ENABLE_CONFLICT_CHECK:
                    cflag, reasoning = definitions_conflict(existing.get("definition"), ddef)
                    if cflag:
                        warn(
                            f"Chapter {chapter_number}: term '{pref}' was redefined differently "
                            f"({reasoning}). Keeping the original definition from chapter "
                            f"{existing.get('introduced_in')}."
                        )
                elif ddef and ddef != existing.get("definition"):
                    warn(
                        f"Chapter {chapter_number}: term '{pref}' was redefined. Keeping the "
                        f"original definition from chapter {existing.get('introduced_in')}."
                    )
            else:
                loaded_terms[pref] = {
                    "preferred_term": pref,
                    "definition": ddef,
                    "definition_status": status,
                    "introduced_in": int(chapter_number),
                }
                added += 1

        terminology_json["terms"] = loaded_terms
        save_terminology(c.terminology_path, terminology_json)
        return added


def cmd_terminology(course_id: str) -> None:
    """Run initial terminology extraction across outline and profile."""
    c = Course.load(course_id)
    require_course_files(c)
    outline = load_json(c.outline_path)
    profile = c.profile_path.read_text(encoding="utf-8")
    terminology_template = load_prompt("terminology_extractor.txt")
    context = {
        "course_profile": profile,
        "outline_json": outline,
        "source_materials": "",
    }
    prompt = render_prompt(terminology_template, context)
    info("Calling LLM for initial terminology extraction (cost-conscious prompt)...")
    try:
        resp = call_llm(prompt)
    except Exception as e:
        warn(
            f"Terminology extraction call failed ({e}). Continuing with empty terminology; "
            f"terms will still be captured as chapters declare them."
        )
        return

    terms = {}
    try:
        data = _extract_json(resp)
        if isinstance(data, dict) and "terms" in data:
            terms = data["terms"]
        elif isinstance(data, list):
            for entry in data:
                term_key = entry.get("term") or entry.get("preferred_term")
                if term_key:
                    terms[term_key] = entry
    except Exception:
        (c.path / "terminology_raw.txt").write_text(resp, encoding="utf-8")
        warn(
            "Failed to parse terminology extractor output as JSON. Saved raw response to "
            "terminology_raw.txt. Continuing with empty terminology."
        )
        return

    existing = load_terminology(c.terminology_path)
    existing_terms = existing.get("terms", {})
    added = 0
    for k, v in terms.items():
        if k in existing_terms:
            continue
        entry = {
            "preferred_term": v.get("preferred_term") if isinstance(v, dict) else k,
            "definition": v.get("definition") if isinstance(v, dict) else None,
            "definition_status": v.get("definition_status") if isinstance(v, dict) else "undefined",
            "introduced_in": v.get("introduced_in") if isinstance(v, dict) else None,
        }
        existing_terms[k] = entry
        added += 1

    existing["terms"] = existing_terms
    save_terminology(c.terminology_path, existing)
    info(f"Terminology extraction complete. {added} new terms added.")
