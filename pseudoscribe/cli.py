"""CLI entry point and interactive wizard for Pseudo-Scribe."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from pseudoscribe import __version__
from pseudoscribe.chapter import cmd_all, cmd_chapter
from pseudoscribe.compiler import cmd_compile
from pseudoscribe.config import get_courses_dir, get_prompts_dir
from pseudoscribe.outline import (
    format_course_dir_name,
    parse_outline_file,
    parse_outline_text_to_chapters_llm,
    write_course_profile_from_metadata,
)
from pseudoscribe.pipeline import (
    cmd_build,
    cmd_init,
    cmd_profile,
    run_pipeline,
)
from pseudoscribe.qa import cmd_qa
from pseudoscribe.terminology import cmd_terminology
from pseudoscribe.utils import _safe_print, error, load_json, save_json


def interactive_setup() -> str:
    """Run interactive CLI wizard prompting for course metadata and curriculum."""
    _safe_print("╭" + "─" * 42 + "╮")
    _safe_print("│       University Lecture Note Generator  │")
    _safe_print("╰" + "─" * 42 + "╯\n")
    _safe_print("Let's create your lecture note. Press Ctrl+C to exit at any time.\n")

    courses_dir = get_courses_dir()
    courses_dir.mkdir(exist_ok=True)
    incomplete = []
    for p in courses_dir.iterdir():
        if p.is_dir():
            state_file = p / "generation_state.json"
            if state_file.exists():
                try:
                    st = load_json(state_file)
                    chapters = st.get("chapters", {})
                    total_complete = sum(1 for v in chapters.values() if v in ("complete", "complete_with_warnings"))
                    if chapters and total_complete < max(1, len(chapters)):
                        incomplete.append((p.name, total_complete, len(chapters)))
                except Exception:
                    pass

    if incomplete:
        print("An incomplete course was found:\n")
        for i, (name, done, total) in enumerate(incomplete, start=1):
            print(f"[{i}] {name} — {done} of {total} chapters completed")
        choice = input("Resume an existing course? [y/N]: ").strip().lower()
        if choice in ("y", "yes"):
            sel = input("Enter the number of the course to resume: ").strip()
            try:
                idx = int(sel) - 1
                resume_id = incomplete[idx][0]
                print(f"\nResuming {resume_id} — this runs straight through to the end.\n")
                return run_pipeline(resume_id)
            except Exception:
                print("Invalid selection; starting a new course.")

    course_code = input("Course code: ").strip()
    course_title = input("Course title: ").strip()
    level = input("Your year/level: ").strip()
    prepared_by = input("Your name: ").strip()
    department = input("Department: ").strip()
    academic_session = input("Academic session: ").strip()
    university = input("University: ").strip()

    outline_path: Path | None = None
    outline_text: str | None = None
    outline_name = ""
    paste_choice = input("Paste the outline into the terminal instead of providing a file? [y/N]: ").strip().lower()
    if paste_choice in ("y", "yes"):
        print("Paste your outline below. End with a single line containing only END (or Ctrl+D/Ctrl+Z):")
        lines: list[str] = []
        try:
            while True:
                ln = input()
                if ln.strip() == "END":
                    break
                lines.append(ln)
        except EOFError:
            pass
        outline_text = "\n".join(lines).strip()
        if not outline_text:
            print("No outline text detected. Please provide an outline file instead.")
        else:
            outline_name = "pasted_outline.md"

    if not outline_text:
        while True:
            p = input("Course outline file: ").strip()
            ppath = Path(p)
            if ppath.exists():
                outline_path = ppath
                outline_name = ppath.name
                break
            print(f"File not found: {p}. Please try again.")

    print("\nSource materials are optional. The generator works from the course outline and learning outcomes alone.")
    print(
        "If you have slides or readings you want the generator to use, provide a path. Otherwise press Enter to skip."
    )
    source_materials = None
    while True:
        s = input("Path to file or directory with source materials (leave blank to skip): ").strip()
        if not s:
            break
        spath = Path(s)
        if spath.exists():
            source_materials = spath
            break
        print(f"Path not found: {s}. Enter a valid path or press Enter to skip.")

    print("\nCourse\n" + "─" * 40)
    print(f"Code:       {course_code}")
    print(f"Title:      {course_title}")
    print(f"Level:      {level}")
    print(f"Prepared by: {prepared_by}")
    print(f"Department: {department}")
    print(f"University: {university}")
    print(f"Session:    {academic_session}")

    if outline_path:
        outline_display = outline_path.name
    elif outline_name:
        outline_display = outline_name
    else:
        outline_display = "none provided"

    print(f"Outline:    {outline_display}")
    print("─" * 40)
    ready = input("Ready to generate your lecture note? [Y/n]: ").strip().lower()
    if ready in ("n", "no"):
        print("Aborted by user.")
        sys.exit(0)

    course_dir_name = format_course_dir_name(course_code, course_title)
    course_dir = courses_dir / course_dir_name
    if course_dir.exists():
        print(f"Course directory {course_dir} already exists. Resuming or using existing data.")
    else:
        cmd_init(course_dir_name)

    if outline_path:
        parsed_outline = parse_outline_file(outline_path)
    else:
        parsed_outline = {
            "course_code": "",
            "course_title": "",
            "department": "",
            "university": "",
            "academic_session": "",
            "level": "",
            "chapters": parse_outline_text_to_chapters_llm(outline_text or ""),
        }

    parsed_outline["course_code"] = course_code
    parsed_outline["course_title"] = course_title
    parsed_outline["department"] = department
    parsed_outline["university"] = university
    parsed_outline["academic_session"] = academic_session
    parsed_outline["level"] = level
    parsed_outline["prepared_by"] = prepared_by

    (course_dir / "outline.json").write_text(
        json.dumps(parsed_outline, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    if not outline_path and outline_text:
        (course_dir / (outline_name or "pasted_outline.md")).write_text(outline_text, encoding="utf-8")

    write_course_profile_from_metadata(course_dir / "course_profile.md", parsed_outline)
    save_json(course_dir / "terminology.json", {"terms": {}})
    save_json(
        course_dir / "generation_state.json",
        {
            "status": "in_progress",
            "chapters": {},
            "final_qa": False,
            "compiled": False,
        },
    )

    repo_prompts = get_prompts_dir()
    if repo_prompts.exists():
        for file in repo_prompts.iterdir():
            if file.is_file():
                shutil.copy(file, course_dir / "prompts" / file.name)

    if source_materials:
        dest = course_dir / "sources"
        if source_materials.is_dir():
            shutil.copytree(source_materials, dest, dirs_exist_ok=True)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(source_materials, dest / source_materials.name)

    print("\nCourse initialized\n")
    print("Generating your lecture note now — this runs straight through, start to finish.\n")
    return run_pipeline(course_dir_name)


def main(argv: list[str] | None = None) -> None:
    """CLI argument parsing and command dispatch."""
    parser = argparse.ArgumentParser(description="Lecture note generator CLI")
    parser.add_argument("--version", action="version", version=f"Pseudo-Scribe {__version__}")
    subparsers = parser.add_subparsers(dest="cmd")

    sp_init = subparsers.add_parser("init")
    sp_init.add_argument("course_id")

    sp_profile = subparsers.add_parser("profile")
    sp_profile.add_argument("course_id")

    sp_term = subparsers.add_parser("terminology")
    sp_term.add_argument("course_id")

    sp_ch = subparsers.add_parser("chapter")
    sp_ch.add_argument("course_id")
    sp_ch.add_argument("chapter_number", type=int)

    sp_all = subparsers.add_parser("all")
    sp_all.add_argument("course_id")

    sp_qa = subparsers.add_parser("qa")
    sp_qa.add_argument("course_id")

    sp_compile = subparsers.add_parser("compile")
    sp_compile.add_argument("course_id")

    sp_build = subparsers.add_parser("build")
    sp_build.add_argument("course_id")

    args = parser.parse_args(argv)

    if not args.cmd:
        try:
            interactive_setup()
            return
        except KeyboardInterrupt:
            print("\nAborted.")
            sys.exit(1)

    if args.cmd == "init":
        cmd_init(args.course_id)
    elif args.cmd == "profile":
        cmd_profile(args.course_id)
    elif args.cmd == "terminology":
        cmd_terminology(args.course_id)
    elif args.cmd == "chapter":
        cmd_chapter(args.course_id, args.chapter_number)
    elif args.cmd == "all":
        cmd_all(args.course_id)
    elif args.cmd == "qa":
        cmd_qa(args.course_id)
    elif args.cmd == "compile":
        cmd_compile(args.course_id)
    elif args.cmd == "build":
        cmd_build(args.course_id)
    else:
        error(f"Unknown command: {args.cmd}")
        sys.exit(1)
