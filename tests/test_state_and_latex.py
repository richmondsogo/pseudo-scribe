"""Characterization tests for state tracking and LaTeX document building / log parsing."""

import json

import pytest

import generate


@pytest.fixture
def sample_course(tmp_path):
    c_path = tmp_path / "SAMPLE_COURSE"
    c_path.mkdir()
    (c_path / "prompts").mkdir()
    (c_path / "drafts").mkdir()
    (c_path / "tex").mkdir()
    (c_path / "final").mkdir()

    profile_text = """
    # Course Profile
    Course Code: CS101
    Course Title: Computer Systems
    University: Test University
    Department: Computer Science
    Academic Session: 2025/2026
    Prepared by: Jane Doe
    """
    (c_path / "course_profile.md").write_text(profile_text, encoding="utf-8")
    (c_path / "outline.json").write_text(json.dumps({"chapters": [{"number": 1, "title": "Intro"}]}), encoding="utf-8")
    (c_path / "generation_state.json").write_text(json.dumps({"chapters": {}}), encoding="utf-8")

    course = generate.Course.load("SAMPLE_COURSE")
    course.path = c_path
    course.profile_path = c_path / "course_profile.md"
    course.outline_path = c_path / "outline.json"
    course.generation_state_path = c_path / "generation_state.json"
    course.tex_path = c_path / "tex"
    course.drafts_path = c_path / "drafts"
    course.final_path = c_path / "final"
    course.output_pdf = c_path / "output.pdf"
    return course


def test_set_chapter_state(sample_course):
    generate._set_chapter_state(sample_course, 1, "complete")
    state = generate.load_json(sample_course.generation_state_path)
    assert state["chapters"]["1"] == "complete"

    generate._set_chapter_state(sample_course, 2, "failed")
    state2 = generate.load_json(sample_course.generation_state_path)
    assert state2["chapters"]["1"] == "complete"
    assert state2["chapters"]["2"] == "failed"


def test_shared_preamble_content():
    preamble = generate._shared_preamble()
    assert "\\documentclass[12pt]{report}" in preamble
    assert "\\usepackage{lmodern}" in preamble
    assert "\\usepackage{microtype}" in preamble
    assert "\\usepackage{parskip}" in preamble
    assert "\\usepackage{booktabs}" in preamble
    assert "\\usepackage{longtable}" in preamble
    assert "\\usepackage{tabularx}" in preamble
    assert "\\usepackage[hidelinks]{hyperref}" in preamble


def test_build_chapter_body_document(sample_course):
    doc = generate.build_chapter_body_document(
        sample_course, 1, "Introduction to Systems", "\\section{Overview}\nSample body text.", start_page=5
    )
    assert "\\setcounter{page}{5}" in doc
    assert "\\setcounter{chapter}{0}" in doc
    assert "\\chapter{Introduction to Systems}" in doc
    assert "\\section{Overview}" in doc
    assert "\\end{document}" in doc


def test_build_single_book_document(sample_course):
    fragments = [
        (1, "First Chapter", "\\section{First}\nContent 1"),
        (2, "Second Chapter", "\\section{Second}\nContent 2"),
    ]
    doc = generate.build_single_book_document(sample_course, fragments)
    assert "\\tableofcontents" in doc
    assert "Computer Systems" in doc
    assert "CS101" in doc
    assert "\\chapter{First Chapter}" in doc
    assert "\\chapter{Second Chapter}" in doc


def test_pdflatex_log_error_detection():
    # Simulate scanning LaTeX log output for error markers
    log_text = """
    This is pdfTeX, Version 3.141592653-2.6-1.40.24 (TeX Live 2022)
    entering extended mode
    (./main.tex
    ! Undefined control sequence.
    l.15 \\badcommand
    ! Emergency stop.
    ==> Fatal error occurred, no output PDF file produced!
    """
    error_lines = [
        ln.strip()
        for ln in log_text.splitlines()
        if ln.strip().startswith("!") and "Emergency stop" not in ln and "==> Fatal error occurred" not in ln
    ]
    assert len(error_lines) == 1
    assert error_lines[0] == "! Undefined control sequence."
