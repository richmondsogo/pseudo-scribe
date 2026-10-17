"""Final quality assurance review and course markdown consolidation."""

from __future__ import annotations

import json
import os

from pseudoscribe.config import FINAL_QA_EXCERPT_CHARS, LLM_PROVIDER
from pseudoscribe.models import Course, require_course_files
from pseudoscribe.prompts_loader import load_prompt, render_prompt
from pseudoscribe.providers import call_llm
from pseudoscribe.terminology import load_terminology
from pseudoscribe.utils import _extract_json, info, load_json, save_json, warn


def cmd_qa(course_id: str) -> None:
    """Consolidate course markdown and run comprehensive final QA evaluation."""
    c = Course.load(course_id)
    require_course_files(c)
    outline = load_json(c.outline_path)
    chapters = outline.get("chapters", [])
    final_md = []
    qa_excerpt_md = []
    included = 0
    for ch in chapters:
        fname = c.drafts_path / f"ch{int(ch.get('number')):02d}.md"
        if not fname.exists():
            warn(f"Missing chapter draft: {fname}. Excluding chapter {ch.get('number')} from the final document.")
            continue
        text = fname.read_text(encoding="utf-8")
        final_md.append(f"# Chapter {ch.get('number')}: {ch.get('title')}\n\n")
        final_md.append(text)
        final_md.append("\n\n")
        excerpt = text[:FINAL_QA_EXCERPT_CHARS]
        if len(text) > FINAL_QA_EXCERPT_CHARS:
            excerpt += "\n...[chapter truncated for this QA pass; the full chapter is complete in final_course.md]..."
        qa_excerpt_md.append(f"# Chapter {ch.get('number')}: {ch.get('title')}\n\n{excerpt}\n\n")
        included += 1

    final_content = "\n".join(final_md)
    (c.final_path / "final_course.md").write_text(final_content, encoding="utf-8")
    if included == 0:
        warn("No chapter drafts were available to assemble. Skipping final QA.")
        return

    qa_content = "\n".join(qa_excerpt_md)
    template = load_prompt("final_qa.txt")
    terminology = load_terminology(c.terminology_path)
    profile = c.profile_path.read_text(encoding="utf-8")
    context = {
        "course_profile": profile,
        "outline_json": outline,
        "terminology_json": terminology,
        "final_course_markdown": qa_content,
    }
    prompt = render_prompt(template, context)
    info("Running final course QA. This may be a longer call.")
    state = load_json(c.generation_state_path)
    heavy_provider = os.getenv("HEAVY_CALL_PROVIDER") or LLM_PROVIDER
    try:
        resp = call_llm(prompt, temperature=0.0, provider=heavy_provider)
    except Exception as e:
        warn(f"Final QA call failed ({e}). Proceeding to compile without a final QA report.")
        state["final_qa"] = True
        state["final_qa_passed"] = None
        save_json(c.generation_state_path, state)
        return

    try:
        report = _extract_json(resp)
        if not isinstance(report, dict):
            raise ValueError("Final QA response was not a JSON object")
        (c.final_path / "final_qa.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        status = report.get("status", "FAIL")
        state["final_qa"] = True
        state["final_qa_passed"] = status == "PASS"
        save_json(c.generation_state_path, state)
        if status != "PASS":
            warn(f"Final QA flagged issues (proceeding anyway): {report.get('critical_issues', [])}")
        else:
            info("Final QA passed.")
    except Exception:
        (c.final_path / "final_qa_raw.txt").write_text(resp, encoding="utf-8")
        warn("Failed to parse final QA response as JSON. Saved raw output to final_qa_raw.txt. Proceeding to compile.")
        state["final_qa"] = True
        state["final_qa_passed"] = None
        save_json(c.generation_state_path, state)
