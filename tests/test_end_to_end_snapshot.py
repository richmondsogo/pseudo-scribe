"""End-to-end characterization and snapshot tests for the full pipeline under DRY_RUN."""

import json
from pathlib import Path

import generate


def test_dry_run_pipeline_matches_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "1")
    monkeypatch.setenv("ENABLE_CHAPTER_ENHANCE", "1")

    # Point COURSES_DIR to tmp_path so the repo workspace is not polluted
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

    course_id = "INS202_TestRun"
    generate.cmd_init(course_id)

    course_path = tmp_path / course_id
    (course_path / "outline.json").write_text(json.dumps(outline, indent=2), encoding="utf-8")
    generate.write_course_profile_from_metadata(course_path / "course_profile.md", outline)

    # Run the entire pipeline
    generate.run_pipeline(course_id)

    golden_dir = Path(__file__).parent / "fixtures" / "golden_baseline"

    # Compare JSON state files
    for json_file in ["outline.json", "terminology.json", "generation_state.json"]:
        generated = json.loads((course_path / json_file).read_text(encoding="utf-8"))
        golden = json.loads((golden_dir / json_file).read_text(encoding="utf-8"))
        assert generated == golden, f"Mismatch in {json_file}"

    # Compare QA report
    generated_qa = json.loads((course_path / "final" / "final_qa.json").read_text(encoding="utf-8"))
    golden_qa = json.loads((golden_dir / "final" / "final_qa.json").read_text(encoding="utf-8"))
    assert generated_qa == golden_qa

    # Compare LaTeX source
    generated_tex = (course_path / "tex" / "main.tex").read_text(encoding="utf-8").strip()
    golden_tex = (golden_dir / "tex" / "main.tex").read_text(encoding="utf-8").strip()
    assert generated_tex == golden_tex, "Mismatch in tex/main.tex"

    # Compare assembled markdown
    generated_md = (course_path / "final" / "final_course.md").read_text(encoding="utf-8").strip()
    golden_md = (golden_dir / "final" / "final_course.md").read_text(encoding="utf-8").strip()
    assert generated_md == golden_md, "Mismatch in final/final_course.md"
