"""Chapter generation, enhancement pass, and legacy draft conversion."""

from __future__ import annotations

import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from pseudoscribe.config import (
    ENABLE_CHAPTER_ENHANCE,
    LLM_PROVIDER,
    MAX_CHAPTER_RETRIES,
    MAX_CONCURRENT_CHAPTERS,
)
from pseudoscribe.latex import (
    _looks_like_body_fragment,
    _looks_like_usable_chapter_body,
    _sanitize_latex_percent,
    _strip_stray_delimiters,
)
from pseudoscribe.models import STATE_LOCK, Course, require_course_files
from pseudoscribe.prompts_loader import load_prompt, render_prompt
from pseudoscribe.providers import call_llm
from pseudoscribe.terminology import (
    _merge_chapter_terms,
    load_terminology,
)
from pseudoscribe.utils import _extract_json, error, info, load_json, save_json, warn


def parse_generation_output(text: str | None) -> tuple[dict[str, Any], str]:
    """Parse an LLM chapter response into (proposed_terms, chapter_body).

    Expects [TERMINOLOGY] ... [/TERMINOLOGY] and [CHAPTER] ... [/CHAPTER].
    Normalizes LaTeX-style \\end{CHAPTER} closes and strips leaked terminology.
    """
    if not text:
        return {}, ""
    text = text.replace("\\end{CHAPTER}", "[/CHAPTER]")
    term_start = text.find("[TERMINOLOGY]")
    term_end = text.find("[/TERMINOLOGY]")
    chap_start = text.find("[CHAPTER]")
    chap_end = text.rfind("[/CHAPTER]")
    terms: dict[str, Any] = {}
    chapter = ""
    if term_start != -1 and term_end != -1:
        raw = text[term_start + len("[TERMINOLOGY]") : term_end].strip()
        try:
            parsed = _extract_json(raw)
            if isinstance(parsed, dict) and "terms" in parsed:
                terms = parsed["terms"]
            elif isinstance(parsed, dict):
                terms = parsed
        except Exception:
            terms = {}
    if chap_start != -1 and chap_end != -1:
        chapter = text[chap_start + len("[CHAPTER]") : chap_end].strip()
    else:
        chapter = text.strip()

    leaked_start = chapter.find("[TERMINOLOGY]")
    leaked_end = chapter.find("[/TERMINOLOGY]")
    if leaked_start != -1 and leaked_end != -1:
        chapter = (chapter[:leaked_start] + chapter[leaked_end + len("[/TERMINOLOGY]") :]).strip()
    return terms, chapter


def _set_chapter_state(c: Course, chapter_number: int, status: str) -> None:
    """Thread-safe update of generation_state.json chapter status."""
    with STATE_LOCK:
        state = load_json(c.generation_state_path)
        state.setdefault("chapters", {})[str(chapter_number)] = status
        save_json(c.generation_state_path, state)


def run_chapter_enhancer(c: Course, chapter_body: str, chapter_number: int | None = None) -> str:
    """Enhancement pass: improves LaTeX structure and adds ASCII diagrams."""
    template = load_prompt("chapter_enhancer.txt")
    terminology = load_terminology(c.terminology_path)
    context = {
        "terminology_json": terminology,
        "chapter_body": chapter_body,
    }
    prompt = render_prompt(template, context)
    resp = call_llm(prompt, temperature=0.2)
    resp = re.sub(r"\\documentclass.*?\\begin\{document\}", "", resp, flags=re.DOTALL)
    resp = re.sub(r"\\end\{document\}", "", resp)
    resp = re.sub(r"^\s*\\chapter\{[^}]*\}\s*", "", resp.strip())
    return resp


def get_chapter_body_fragment(c: Course, chapter_number: int) -> str | None:
    """Retrieve LaTeX body fragment for a chapter, converting older drafts if necessary."""
    fname = c.drafts_path / f"ch{int(chapter_number):02d}.md"
    if not fname.exists():
        warn(f"No draft for chapter {chapter_number}; skipping it in the compiled PDF.")
        return None
    draft_content = fname.read_text(encoding="utf-8")

    cleaned = _strip_stray_delimiters(draft_content)

    if not _looks_like_usable_chapter_body(cleaned):
        warn(
            f"Chapter {chapter_number} draft is not a usable LaTeX body fragment "
            f"(maybe an older refusal or markdown draft). Keeping it as-is instead of "
            f"feeding it to the PDF; {fname} can be regenerated with "
            f"'python generate.py chapter {c.id} {chapter_number}'."
        )
        return None

    if _looks_like_body_fragment(cleaned):
        cleaned = _sanitize_latex_percent(cleaned)
        return cleaned

    template = load_prompt("typesetter.txt")
    outline = load_json(c.outline_path)
    chap = next(
        (ch for ch in outline.get("chapters", []) if int(ch.get("number")) == int(chapter_number)),
        None,
    )
    context = {
        "chapter_number": chapter_number,
        "chapter_title": chap.get("title") if chap else f"Chapter {chapter_number}",
        "chapter_markdown": draft_content,
        "terminology_json": load_terminology(c.terminology_path),
    }
    prompt = render_prompt(template, context)
    try:
        resp = call_llm(
            prompt,
            temperature=0.0,
            provider=os.getenv("HEAVY_CALL_PROVIDER") or LLM_PROVIDER,
        )
    except Exception as e:
        warn(
            f"Typesetting chapter {chapter_number} failed ({e}). Skipping it in the compiled PDF "
            f"(the markdown draft is still available at {fname})."
        )
        return None

    resp = re.sub(r"\\documentclass.*?\\begin\{document\}", "", resp, flags=re.DOTALL)
    resp = re.sub(r"\\end\{document\}", "", resp)
    resp = re.sub(r"^\s*\\chapter\{[^}]*\}\s*", "", resp.strip())
    fname.write_text(resp, encoding="utf-8")
    return resp


def generate_chapter_content(
    c: Course,
    chapter_number: int,
    chap: dict[str, Any],
    total_chapters: int,
    max_retries: int = MAX_CHAPTER_RETRIES,
) -> dict[str, Any]:
    """Generate one chapter directly as LaTeX."""
    title = chap.get("title") or f"Chapter {chapter_number}"
    profile = c.profile_path.read_text(encoding="utf-8")
    outline = load_json(c.outline_path)
    chapter_writer_template = load_prompt("chapter_writer.txt")

    attempts = 0
    last_issues: Any = None
    chapter_body: str | None = None
    while attempts < max_retries:
        attempts += 1
        info(f"[chapter {chapter_number}] [Attempt {attempts}/{max_retries}] Generating ...")
        terminology_snapshot = load_terminology(c.terminology_path)
        context = {
            "course_profile": profile,
            "outline_json": outline,
            "terminology_json": terminology_snapshot,
            "chapter_number": chapter_number,
            "chapter_title": chap.get("title"),
            "chapter_topic": chap.get("topic"),
            "source_materials": "",
        }
        prompt = render_prompt(chapter_writer_template, context)
        try:
            resp = call_llm(prompt)
        except Exception as e:
            last_issues = f"LLM call failed: {e}"
            warn(f"[chapter {chapter_number}] generation attempt {attempts} failed ({e}).")
            continue
        proposed_terms, chapter_body = parse_generation_output(resp)
        if not chapter_body.strip():
            last_issues = "Response had no usable chapter content"
            warn(f"[chapter {chapter_number}] attempt {attempts} produced no chapter content; retrying.")
            chapter_body = None
            continue

        if not _looks_like_usable_chapter_body(chapter_body):
            last_issues = f"Response was not a usable LaTeX chapter body: {chapter_body[:200]!r}"
            warn(
                f"[chapter {chapter_number}] attempt {attempts} was rejected as a refusal or "
                f"degenerate response; retrying."
            )
            chapter_body = None
            continue

        new_term_count = _merge_chapter_terms(c, chapter_number, proposed_terms)

        if ENABLE_CHAPTER_ENHANCE:
            try:
                enhanced = run_chapter_enhancer(c, chapter_body, chapter_number)
                if enhanced.strip():
                    chapter_body = enhanced
                    info(f"[chapter {chapter_number}] Enhancement pass applied (diagrams/polish).")
            except Exception as e:
                warn(
                    f"[chapter {chapter_number}] Enhancement call failed ({e}). Keeping the "
                    f"original draft — nothing lost, just not polished this round."
                )

        ch_filename = f"ch{int(chapter_number):02d}.md"
        (c.drafts_path / ch_filename).write_text(chapter_body, encoding="utf-8")
        info(f"[chapter {chapter_number}] Draft written to {c.drafts_path / ch_filename}")
        info(f"[{chapter_number}/{total_chapters}] {title}\n  - Generated\n  - {new_term_count} new terms added")
        _set_chapter_state(c, chapter_number, "complete")
        return {"status": "complete", "chapter": chapter_number}

    warn(
        f"[chapter {chapter_number}] could not be generated after {attempts} attempts ({last_issues}). "
        f"No draft available — it will be skipped when assembling the final document."
    )
    _set_chapter_state(c, chapter_number, "failed")
    return {"status": "failed", "chapter": chapter_number, "error": str(last_issues)}


def cmd_chapter(course_id: str, chapter_number: int) -> None:
    """Generate a single course chapter by number."""
    c = Course.load(course_id)
    if not c.path.exists():
        error("Course not found. Run 'init' first.")
        sys.exit(1)
    try:
        require_course_files(c)
        outline = load_json(c.outline_path)
        chapters = outline.get("chapters", [])
        chap = next(
            (ch for ch in chapters if int(ch.get("number")) == int(chapter_number)),
            None,
        )
        if not chap:
            error(f"Chapter {chapter_number} not found in outline.json")
            sys.exit(1)
        result = generate_chapter_content(c, chapter_number, chap, len(chapters))
    except Exception as e:
        error(str(e))
        sys.exit(1)
    if result.get("status") == "failed":
        error(f"Chapter {chapter_number} failed: {result.get('error')}")
        sys.exit(1)


def cmd_all(course_id: str) -> None:
    """Generate all missing course chapters sequentially or concurrently."""
    c = Course.load(course_id)
    require_course_files(c)
    outline = load_json(c.outline_path)
    chapters = outline.get("chapters", [])
    total = len(chapters)
    state = load_json(c.generation_state_path)
    chapter_state = state.get("chapters", {})
    to_generate = [
        ch
        for ch in chapters
        if chapter_state.get(str(int(ch.get("number")))) not in ("complete", "complete_with_warnings")
    ]
    already_done = total - len(to_generate)
    if already_done:
        info(f"{already_done} chapter(s) already complete, skipping.")
    if not to_generate:
        info("No chapters need generation.")
        return
    info(f"Generating {len(to_generate)} chapter(s) for {course_id} (concurrency={MAX_CONCURRENT_CHAPTERS})")
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_CHAPTERS) as ex:
        future_map = {
            ex.submit(generate_chapter_content, c, int(ch.get("number")), ch, total): int(ch.get("number"))
            for ch in to_generate
        }
        for fut in as_completed(future_map):
            num = future_map[fut]
            try:
                fut.result()
            except Exception as e:
                warn(f"Chapter {num} raised an unexpected error ({e}). Skipping and continuing.")
    info("All chapters generation finished (or were already complete).")
