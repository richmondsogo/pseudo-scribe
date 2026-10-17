"""Characterization tests for pure helper functions in generate.py."""

import pytest
import re
import generate


def test_normalize_chapter_title_transformations():
    cases = [
        ("analyse the concepts of Human Computer Interaction", "Foundations of Human Computer Interaction"),
        ("analyze the concepts of operating systems", "Foundations of Operating Systems"),
        ("list major forms, types and root causes of software bugs", "Forms, Types and Root Causes of Software Bugs"),
        ("differentiate between compile time and run time errors", "Compile Time and Run Time Errors"),
        ("enumerate caching strategies", "Caching Strategies"),
        ("describe roles of operating system kernels", "Operating System Kernels"),
        ("Introduction to Algorithms;", "Introduction to Algorithms"),
        ("Data Structures and", "Data Structures"),
    ]
    for raw, expected in cases:
        assert generate.normalize_chapter_title(raw) == expected


def test_parse_outline_text_to_chapters_heuristic():
    sample_text = """
    Chapter 1: Foundations of Computing
    Some details here.

    ## Chapter 2 - Data Structures
    More details.

    3. Algorithms and Complexity
    Additional topics.
    """
    chapters = generate.parse_outline_text_to_chapters_heuristic(sample_text)
    assert len(chapters) == 3
    assert chapters[0]["number"] == 1
    assert "Foundations" in chapters[0]["title"]
    assert chapters[1]["number"] == 2
    assert "Data Structures" in chapters[1]["title"]
    assert chapters[2]["number"] == 3
    assert "Algorithms" in chapters[2]["title"]


def test_extract_json():
    # Clean json
    assert generate._extract_json('{"key": "value"}') == {"key": "value"}

    # In markdown fences
    assert generate._extract_json('```json\n{"key": "value"}\n```') == {"key": "value"}

    # Extra surrounding text
    assert generate._extract_json('Here is the json:\n{"key": 123}\nHope this helps!') == {"key": 123}

    # Invalid JSON
    with pytest.raises(ValueError):
        generate._extract_json("No JSON present here.")


def test_sanitize_latex_percent():
    raw = "90% of all requests were served.\n\\begin{verbatim}\n10% | 20%\n\\end{verbatim}\nAnother 5% here."
    sanitized = generate._sanitize_latex_percent(raw)
    assert r"90\% of all requests were served." in sanitized
    assert "10% | 20%" in sanitized  # verbatim content preserved intact
    assert r"Another 5\% here." in sanitized


def test_strip_stray_delimiters():
    raw = (
        "[TERMINOLOGY]\n{\"term\": 1}\n[/TERMINOLOGY]\n"
        "[CHAPTER]\n\\section{Intro}\nHello world.\n[/CHAPTER]"
    )
    cleaned = generate._strip_stray_delimiters(raw)
    assert "[TERMINOLOGY]" not in cleaned
    assert "[CHAPTER]" not in cleaned
    assert "[/CHAPTER]" not in cleaned
    assert "\\section{Intro}" in cleaned


def test_looks_like_body_fragment():
    assert generate._looks_like_body_fragment("\\section{Introduction}\nContent") is True
    assert generate._looks_like_body_fragment("\\documentclass{article}\n\\begin{document}") is False
    assert generate._looks_like_body_fragment("# Markdown Title\nSome text") is False


def test_looks_like_usable_chapter_body():
    # Refusal string
    assert generate._looks_like_usable_chapter_body("User Safety: safe") is False
    assert generate._looks_like_usable_chapter_body("I cannot help with that request.") is False

    # Too short (<250 chars)
    assert generate._looks_like_usable_chapter_body("\\section{Test}\nShort.") is False

    # Valid LaTeX body with sufficient length
    valid_latex = "\\section{Introduction}\n" + ("This is valid lecture note text explaining computer systems. " * 10)
    assert len(valid_latex) >= 250
    assert generate._looks_like_usable_chapter_body(valid_latex) is True


def test_parse_generation_output():
    text = (
        "[TERMINOLOGY]\n"
        "{\"terms\": {\"cache\": {\"preferred_term\": \"cache\", \"definition\": \"fast memory\"}}}\n"
        "[/TERMINOLOGY]\n\n"
        "[CHAPTER]\n"
        "\\section{Memory}\n"
        "Memory content.\n"
        "[/CHAPTER]"
    )
    terms, chapter = generate.parse_generation_output(text)
    assert "cache" in terms
    assert "\\section{Memory}" in chapter

    # Malformed closing tag \end{CHAPTER}
    malformed = (
        "[TERMINOLOGY]\n{}\n[/TERMINOLOGY]\n"
        "[CHAPTER]\n"
        "\\section{Test}\n"
        "Body content.\n"
        "\\end{CHAPTER}"
    )
    terms2, chapter2 = generate.parse_generation_output(malformed)
    assert "\\section{Test}" in chapter2


def test_latex_escape():
    raw = "CS & IT: 100% of $5 #1 {notes} ~home ^top \\path"
    escaped = generate._latex_escape(raw)
    assert r"\&" in escaped
    assert r"\%" in escaped
    assert r"\$" in escaped
    assert r"\#" in escaped
    assert r"\{" in escaped
    assert r"\}" in escaped
    assert r"\textasciitilde{}" in escaped
    assert r"\textasciicircum{}" in escaped
    assert r"\textbackslash{}" in escaped


def test_parse_course_profile_fields():
    profile = """
    # Course Profile

    Course Code: INS202
    Course Title: Human-Computer Interaction
    University: University of Lagos
    Department: Computer Science
    Academic Session: 2025/2026
    Level: 300 Level
    Prepared by: Dr. Smith
    """
    fields = generate.parse_course_profile_fields(profile)
    assert fields["course code"] == "INS202"
    assert fields["course title"] == "Human-Computer Interaction"
    assert fields["university"] == "University of Lagos"
    assert fields["prepared by"] == "Dr. Smith"
