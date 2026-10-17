"""Test to verify against the external temporary snapshot if present."""

import json
from pathlib import Path

import pytest

import generate

EXTERNAL_SNAPSHOT = Path(r"C:\Users\Richmond\AppData\Local\Temp\pseudo_scribe_baseline_snapshot")


@pytest.mark.skipif(not EXTERNAL_SNAPSHOT.exists(), reason="External snapshot not found")
def test_matches_external_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "1")
    monkeypatch.setenv("ENABLE_CHAPTER_ENHANCE", "1")
    monkeypatch.setattr(generate, "COURSES_DIR", tmp_path)

    fixture_outline = Path(__file__).parent / "fixtures" / "sample_outline.md"
    outline = generate.parse_outline_file(fixture_outline)
    outline.update(
        {
            "course_code": "INS202",
            "course_title": "Human-Computer Interaction",
            "department": "CS",
            "university": "Unilag",
            "academic_session": "2025/2026",
            "level": "300",
            "prepared_by": "Tester",
        }
    )

    course_id = "INS202_ExtTest"
    generate.cmd_init(course_id)

    course_path = tmp_path / course_id
    (course_path / "outline.json").write_text(json.dumps(outline, indent=2), encoding="utf-8")
    generate.write_course_profile_from_metadata(course_path / "course_profile.md", outline)

    generate.run_pipeline(course_id)

    for json_file in ["outline.json", "terminology.json", "generation_state.json"]:
        generated = json.loads((course_path / json_file).read_text(encoding="utf-8"))
        snapshot = json.loads((EXTERNAL_SNAPSHOT / json_file).read_text(encoding="utf-8"))
        assert generated == snapshot, f"Mismatch with external snapshot in {json_file}"

    generated_tex = (course_path / "tex" / "main.tex").read_text(encoding="utf-8").strip()
    snapshot_tex = (EXTERNAL_SNAPSHOT / "tex" / "main.tex").read_text(encoding="utf-8").strip()
    assert generated_tex == snapshot_tex, "Mismatch with external snapshot in tex/main.tex"
