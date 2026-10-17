"""Characterization tests for terminology registry and conflict management."""

import pytest
import json
from pathlib import Path
import generate


@pytest.fixture
def temp_course(tmp_path):
    c_path = tmp_path / "TEST_COURSE"
    c_path.mkdir()
    (c_path / "prompts").mkdir()
    (c_path / "drafts").mkdir()
    (c_path / "tex").mkdir()
    (c_path / "final").mkdir()

    term_path = c_path / "terminology.json"
    generate.save_json(term_path, {"terms": {}})

    state_path = c_path / "generation_state.json"
    generate.save_json(state_path, {"status": "in_progress", "chapters": {}})

    course = generate.Course.load("TEST_COURSE")
    course.path = c_path
    course.terminology_path = term_path
    course.generation_state_path = state_path
    return course


def test_merge_chapter_terms_preserves_first_definition(temp_course):
    # Initial chapter introduces term
    proposed_ch1 = {
        "cache": {
            "preferred_term": "cache",
            "definition": "Fast temporary storage.",
            "definition_status": "defined"
        }
    }
    added1 = generate._merge_chapter_terms(temp_course, 1, proposed_ch1)
    assert added1 == 1

    registry1 = generate.load_terminology(temp_course.terminology_path)
    assert registry1["terms"]["cache"]["definition"] == "Fast temporary storage."
    assert registry1["terms"]["cache"]["introduced_in"] == 1

    # Subsequent chapter redefines term
    proposed_ch2 = {
        "cache": {
            "preferred_term": "cache",
            "definition": "A hardware buffer between CPU and RAM.",
            "definition_status": "defined"
        },
        "register": {
            "preferred_term": "register",
            "definition": "Small fast storage inside CPU.",
            "definition_status": "defined"
        }
    }
    added2 = generate._merge_chapter_terms(temp_course, 2, proposed_ch2)
    assert added2 == 1  # only 'register' was newly added

    registry2 = generate.load_terminology(temp_course.terminology_path)
    # Original definition for 'cache' must be preserved!
    assert registry2["terms"]["cache"]["definition"] == "Fast temporary storage."
    assert registry2["terms"]["cache"]["introduced_in"] == 1
    assert registry2["terms"]["register"]["definition"] == "Small fast storage inside CPU."
    assert registry2["terms"]["register"]["introduced_in"] == 2


def test_load_and_save_terminology(tmp_path):
    p = tmp_path / "terminology.json"
    data = {"terms": {"test_term": {"preferred_term": "test_term"}}}
    generate.save_terminology(p, data)

    loaded = generate.load_terminology(p)
    assert "test_term" in loaded["terms"]

    # When file missing, returns empty dict with terms key
    missing_p = tmp_path / "nonexistent.json"
    empty = generate.load_terminology(missing_p)
    assert empty == {"terms": {}}
