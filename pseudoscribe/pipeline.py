"""Course initialization, skeleton creation, and end-to-end pipeline orchestration."""

from __future__ import annotations

import shutil
import sys
import textwrap

from pseudoscribe import models
from pseudoscribe.chapter import cmd_all
from pseudoscribe.compiler import cmd_compile
from pseudoscribe.config import get_prompts_dir
from pseudoscribe.models import Course, course_path
from pseudoscribe.qa import cmd_qa
from pseudoscribe.terminology import cmd_terminology, load_terminology
from pseudoscribe.utils import (
    RUN_WARNINGS,
    _safe_print,
    error,
    info,
    load_json,
    save_json,
    save_run_warnings,
)


def cmd_init(course_id: str) -> None:
    """Initialize a new course folder skeleton with template state and outline."""
    p = course_path(course_id)
    if p.exists():
        error(f"Course {course_id} already exists at {p}")
        sys.exit(1)

    (p / "prompts").mkdir(parents=True)
    (p / "drafts").mkdir()
    (p / "tex").mkdir()
    (p / "final").mkdir()
    info(f"Created course skeleton at {p}")

    outline = {"course_code": course_id, "course_title": "", "chapters": []}
    save_json(p / "outline.json", outline)
    save_json(p / "terminology.json", {"terms": {}})
    state = {
        "status": "not_started",
        "chapters": {},
        "final_qa": False,
        "compiled": False,
    }
    save_json(p / "generation_state.json", state)

    repo_prompts = get_prompts_dir()
    if repo_prompts.exists():
        for file in repo_prompts.iterdir():
            if file.is_file():
                shutil.copy(file, p / "prompts" / file.name)
    info("Initialization complete. Edit course_profile.md and outline.json then run terminology extraction.")


def cmd_profile(course_id: str) -> None:
    """Create or inspect course_profile.md for pedagogical rules and metadata."""
    c = Course.load(course_id)
    if not c.path.exists():
        error("Course not found. Run 'init' first.")
        sys.exit(1)
    if not c.profile_path.exists():
        template = textwrap.dedent(
            f"""
        # Course Profile

        Course Code: {course_id}
        Course Title:
        University:
        Department:
        Academic Session:
        Level:

        ## Style Rules

        * Use clear academic English.
        * Explain concepts before assuming familiarity.
        * Prefer explanation over memorization.
        * Avoid filler.
        * Use consistent terminology.
        * Do not use conversational language.
        """
        )
        c.profile_path.write_text(template, encoding="utf-8")
        info(f"Wrote template profile to {c.profile_path}")
    else:
        info(f"Profile exists at {c.profile_path}. Open and edit as required.")


def print_run_summary(c: Course) -> None:
    """Print run statistics, generated artifacts, and recorded warnings to console."""
    save_run_warnings(c)
    state = load_json(c.generation_state_path)
    chapters_state = state.get("chapters", {})
    outline_chapters = load_json(c.outline_path).get("chapters", [])
    total = len(outline_chapters)
    complete = sum(1 for v in chapters_state.values() if v in ("complete", "complete_with_warnings"))
    with_warnings = sum(1 for v in chapters_state.values() if v == "complete_with_warnings")

    _safe_print("\n" + "─" * 50)
    _safe_print("Run summary")
    _safe_print("─" * 50)
    suffix = f" ({with_warnings} with QA warnings)" if with_warnings else ""
    _safe_print(f"Chapters generated: {complete}/{total}{suffix}")
    _safe_print(f"LLM calls made this run: {models.LLM_CALL_COUNT}")
    if state.get("compiled") and c.output_pdf.exists():
        _safe_print(f"PDF ready:   {c.output_pdf}")
    elif (c.path / "main.tex").exists():
        _safe_print("PDF compile didn't complete, but LaTeX source is ready:")
        _safe_print(f"   {c.path / 'main.tex'}")
        _safe_print(f"   {c.tex_path} (per-chapter .tex files)")
    else:
        _safe_print("No PDF or .tex output this run. Markdown drafts are available:")
        _safe_print(f"   {c.drafts_path}")

    if RUN_WARNINGS:
        _safe_print(f"\n{len(RUN_WARNINGS)} warning(s) were recorded:")
        for w in RUN_WARNINGS[:10]:
            _safe_print(f"   - {w}")
        if len(RUN_WARNINGS) > 10:
            _safe_print(f"   ... and {len(RUN_WARNINGS) - 10} more.")
        _safe_print(f"Full list saved to {c.path / 'run_warnings.json'}")
    _safe_print("─" * 50)


def run_pipeline(course_dir_name: str) -> str:
    """Execute end-to-end generation: terminology -> chapters -> QA -> compile."""
    c = Course.load(course_dir_name)
    terminology = load_terminology(c.terminology_path)
    if not terminology.get("terms"):
        cmd_terminology(course_dir_name)
    cmd_all(course_dir_name)
    cmd_qa(course_dir_name)
    cmd_compile(course_dir_name)
    print_run_summary(c)
    return course_dir_name


def cmd_build(course_id: str) -> None:
    """Convenience alias for run_pipeline."""
    run_pipeline(course_id)
