"""Outline ingestion, heuristic and LLM segmentation, and profile generation."""

from __future__ import annotations

import re
import textwrap
from pathlib import Path
from typing import Any

from pseudoscribe.providers import call_llm
from pseudoscribe.utils import _extract_json, load_json, warn


def format_course_dir_name(course_code: str, course_title: str) -> str:
    """Format alphanumeric directory name <CourseCode>_<TitleSlug>."""
    safe_code = "".join(ch for ch in course_code if ch.isalnum())
    safe_title = "".join(ch if (ch.isalnum() or ch.isspace()) else "" for ch in course_title)
    title_slug = "_".join(safe_title.strip().split())
    return f"{safe_code}_{title_slug}" if title_slug else safe_code


def normalize_chapter_title(title: str) -> str:
    """Convert common syllabus learning-outcome phrasing into concise chapter titles."""
    value = re.sub(r"\s+", " ", str(title or "")).strip()
    value = re.sub(r"[;.:]+(?=\s|$)", "", value).strip()
    value = re.sub(r"\s+(?:and|or)$", "", value, flags=re.IGNORECASE).strip()

    patterns = (
        (r"^(?:analyse|analyze) the concepts of (.+)$", r"Foundations of \1"),
        (
            r"^list major forms, types and root causes of (.+)$",
            r"Forms, Types and Root Causes of \1",
        ),
        (r"^differentiate between (.+)$", r"\1"),
        (r"^enumerate (.+?) strategies$", r"\1 Strategies"),
        (r"^describe roles of (.+)$", r"\1"),
    )

    transformed = False
    for pattern, replacement in patterns:
        match = re.match(pattern, value, flags=re.IGNORECASE)
        if match:
            value = re.sub(pattern, replacement, value, flags=re.IGNORECASE)
            value = re.sub(r"\s+in$", "", value, flags=re.IGNORECASE).strip()
            value = value.title()
            value = re.sub(
                r"\b(Of|And|The|In|On|To)\b",
                lambda match: match.group().lower(),
                value,
            )
            transformed = True
            break

    if transformed:
        return value
    return value[:1].upper() + value[1:] if value else "Chapter"


def parse_outline_text_to_chapters_heuristic(text: str) -> list[dict[str, Any]]:
    """Regex-based fallback segmentation of outline text into chapter records."""
    chapters: list[dict[str, Any]] = []
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    chapter_re = re.compile(r"^(?:Chapter\s+)?(\d{1,3})[:\.)\s-]*(.*)$", re.IGNORECASE)
    md_heading_re = re.compile(r"^#{1,3}\s+(.*)$")
    numbered_re = re.compile(r"^(\d{1,3})[\.:]\s+(.*)$")

    for ln in lines:
        m = chapter_re.match(ln)
        if m:
            num = int(m.group(1))
            title = m.group(2).strip() or f"Chapter {num}"
            chapters.append({"number": num, "title": normalize_chapter_title(title), "topic": ""})
            continue
        m = md_heading_re.match(ln)
        if m:
            title = m.group(1).strip()
            m2 = re.match(r"^(\d{1,3})[\s\-:\.)]+(.*)$", title)
            if m2:
                num = int(m2.group(1))
                t = m2.group(2).strip() or f"Chapter {num}"
                chapters.append({"number": num, "title": normalize_chapter_title(t), "topic": ""})
            else:
                chapters.append(
                    {
                        "number": len(chapters) + 1,
                        "title": normalize_chapter_title(title),
                        "topic": "",
                    }
                )
            continue
        m = numbered_re.match(ln)
        if m:
            num = int(m.group(1))
            title = m.group(2).strip()
            chapters.append({"number": num, "title": normalize_chapter_title(title), "topic": ""})

    if not chapters:
        paragraphs = re.split(r"\n\s*\n", text)
        for i, p in enumerate(paragraphs[:50], start=1):
            first_line = p.strip().splitlines()[0][:120]
            if len(first_line) > 0:
                chapters.append(
                    {
                        "number": i,
                        "title": normalize_chapter_title(first_line),
                        "topic": "",
                    }
                )

    seen = set()
    unique: list[dict[str, Any]] = []
    for ch in sorted(chapters, key=lambda x: x["number"]):
        if ch["number"] in seen:
            continue
        seen.add(ch["number"])
        unique.append(ch)
    return unique


def parse_outline_text_to_chapters_llm(text: str) -> list[dict[str, Any]]:
    """Primary LLM-based curriculum outline parser, segmenting teachable topics."""
    prompt = f"""You are an outline chapter extractor for a university course lecture-note generator.

You will be given raw, possibly badly formatted, pasted course outline or syllabus text (it may
have no line breaks, run-on sentences, or mixed sections). Your job is to identify the actual
TEACHABLE CHAPTERS the lecture notes should be organized into.

Course outlines typically mix together things like:
- Learning Outcomes (a numbered list of things students should be able to do) — these describe
  goals, NOT chapters. Do not turn each learning outcome into its own chapter. Never use a
  learning outcome as a chapter title.
- Course Contents / Course Description (the actual topics to be taught) — this is what chapters
  should be built from.

Rules for chapter titles:
- A title must be a SHORT NOUN PHRASE describing a topic, e.g. "Memory Management",
  "Linked Lists", "Process Scheduling", "File Systems". It must NOT start with a verb like
  "discuss", "apply", "implement", "choose", "analyse", "evaluate", "understand", "explain" —
  those are learning outcomes, not chapter titles.
- If the outline only contains learning outcomes and no separate course content, derive the
  chapters FROM THE TOPIC WORDS inside the outcomes. Example: an outcome reading "discuss the
  appropriate use of built-in data structures" becomes a chapter titled "Built-in Data
  Structures", with topic "arrays, dynamic arrays, tuples, dictionaries, sets, hash maps".
- Strip trailing punctuation (semicolons, commas, 'and', 'or') from titles.
- Capitalize titles as proper nouns.
- Never copy a sentence fragment verbatim as a title.

Break the taught content into a logical sequence of chapters. Group closely related short topics
into one chapter; split large topic lists into multiple chapters where they're distinct enough to
teach separately. Order chapters in a sensible teaching sequence (foundational topics first).

Respond with ONLY a JSON object, no markdown fences, no commentary, in exactly this shape:
{{
  "chapters": [
    {{"number": 1, "title": "Short chapter title", "topic": "Comma-separated subtopics this chapter covers"}},
    {{"number": 2, "title": "...", "topic": "..."}}
  ]
}}

Raw outline text:
---
{text}
---
"""
    try:
        resp = call_llm(prompt, temperature=0.0)
        data = _extract_json(resp)
        chapters_raw = data.get("chapters") if isinstance(data, dict) else None
        if not isinstance(chapters_raw, list) or not chapters_raw:
            raise ValueError("Response had no usable 'chapters' array")

        chapters: list[dict[str, Any]] = []
        for i, ch in enumerate(chapters_raw, start=1):
            if not isinstance(ch, dict):
                continue
            raw_num = ch.get("number")
            try:
                num = int(raw_num) if raw_num is not None else i
            except (TypeError, ValueError):
                num = i
            title = normalize_chapter_title(str(ch.get("title") or f"Chapter {num}").strip() or f"Chapter {num}")
            topic = str(ch.get("topic") or "").strip()
            chapters.append({"number": num, "title": title, "topic": topic})

        seen = set()
        unique: list[dict[str, Any]] = []
        for ch in sorted(chapters, key=lambda c: c["number"]):
            if ch["number"] in seen:
                continue
            seen.add(ch["number"])
            unique.append(ch)
        if not unique:
            raise ValueError("Chapter list was empty after normalization")
        return unique
    except Exception as e:
        warn(
            f"LLM-based outline parsing failed ({e}); falling back to basic text parsing. "
            f"Check outline.json afterward — the chapter list may need manual adjustment."
        )
        return parse_outline_text_to_chapters_heuristic(text)


def parse_outline_file(path: Path) -> dict[str, Any]:
    """Parse an outline file (.json, .md, .txt, .docx, .pdf) into an outline dict."""
    if not path.exists():
        raise FileNotFoundError(f"Outline file not found: {path}")

    ext = path.suffix.lower()
    text = ""
    if ext == ".json":
        data = load_json(path)
        for chapter in data.get("chapters", []):
            chapter["title"] = normalize_chapter_title(chapter.get("title", ""))
        return data
    elif ext in {".md", ".txt"}:
        text = path.read_text(encoding="utf-8")
    elif ext == ".docx":
        try:
            import docx
        except Exception as e:
            raise RuntimeError("python-docx is required to parse .docx outlines. Install via requirements.txt") from e
        doc = docx.Document(str(path))
        text = "\n".join(p.text for p in doc.paragraphs)
    elif ext == ".pdf":
        try:
            from PyPDF2 import PdfReader
        except Exception as e:
            raise RuntimeError("PyPDF2 is required to parse .pdf outlines. Install via requirements.txt") from e
        reader = PdfReader(str(path))
        pages = []
        for p in reader.pages:
            try:
                pages.append(p.extract_text() or "")
            except Exception:
                pages.append("")
        text = "\n".join(pages)
    else:
        text = path.read_text(encoding="utf-8", errors="ignore")

    chapters = parse_outline_text_to_chapters_llm(text)
    return {
        "course_code": "",
        "course_title": "",
        "department": "",
        "university": "",
        "academic_session": "",
        "level": "",
        "chapters": chapters,
    }


def write_course_profile_from_metadata(path: Path, metadata: dict[str, Any]) -> None:
    """Generate standardized course_profile.md containing metadata and pedagogical style rules."""
    template = textwrap.dedent(
        f"""
        # Course Profile

        Course Code: {metadata.get("course_code", "")}
        Course Title: {metadata.get("course_title", "")}
        University: {metadata.get("university", "")}
        Department: {metadata.get("department", "")}
        Academic Session: {metadata.get("academic_session", "")}
        Level: {metadata.get("level", "")}
        Prepared by: {metadata.get("prepared_by", "")}

        ## Style Rules

        * Use clear academic English.
        * Explain concepts before assuming familiarity.
        * Prefer explanation over memorization.
        * Avoid filler.
        * Do not use em dashes.
        * Use consistent terminology.
        * Do not use conversational language.
        * Do not unnecessarily repeat explanations.
        """
    )
    path.write_text(template, encoding="utf-8")


def parse_course_profile_fields(profile_text: str) -> dict[str, str]:
    """Extract 'Key: Value' metadata fields from course_profile.md."""
    fields: dict[str, str] = {}
    for line in profile_text.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if key and value:
            fields[key] = value
    return fields
