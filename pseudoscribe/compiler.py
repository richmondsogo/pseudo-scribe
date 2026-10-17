"""LaTeX compilation execution, two-pass pagination, and PDF assembly."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from pseudoscribe.chapter import get_chapter_body_fragment
from pseudoscribe.latex import (
    _looks_like_body_fragment,
    _sanitize_latex_percent,
    _strip_stray_delimiters,
    build_chapter_body_document,
    build_front_matter_document,
    build_single_book_document,
)
from pseudoscribe.models import Course, require_course_files
from pseudoscribe.utils import info, load_json, save_json, warn


def _run_pdflatex(
    work_dir: Path,
    tex_path: Path,
    expected_pdf: Path,
    error_label: str,
    passes: int = 2,
) -> Path | None:
    """Run pdflatex in work_dir, handling dry-run mocking and log error scanning."""
    if os.getenv("DRY_RUN", "").lower() in ("1", "true", "yes"):
        try:
            from pypdf import PdfWriter

            writer = PdfWriter()
            writer.add_blank_page(width=595, height=842)
            with open(expected_pdf, "wb") as f:
                writer.write(f)
            writer.close()
        except Exception:
            expected_pdf.write_bytes(b"%PDF-1.4\n% Dummy PDF produced in DRY_RUN mode\n")
        return expected_pdf

    old_cwd = os.getcwd()
    try:
        os.chdir(work_dir)
        cmd = ["pdflatex", "-interaction=nonstopmode", tex_path.name]
        last_output = b""
        res: subprocess.CompletedProcess | None = None
        for _ in range(passes):
            try:
                res = subprocess.run(cmd, capture_output=True)
            except FileNotFoundError:
                warn(
                    "'pdflatex' isn't installed or isn't on PATH. Install a LaTeX distribution "
                    "(e.g. MiKTeX on Windows, TeX Live on macOS/Linux) — your LaTeX source is "
                    f"still ready at {tex_path}."
                )
                return None
            last_output = res.stdout + b"\n" + res.stderr
            if res.returncode != 0:
                (tex_path.with_suffix("")).with_name(tex_path.stem + "_error.log").write_bytes(last_output)

        log_text = (last_output or b"").decode("utf-8", errors="replace")
        error_lines = [
            ln.strip()
            for ln in log_text.splitlines()
            if ln.strip().startswith("!") and "Emergency stop" not in ln and "==> Fatal error occurred" not in ln
        ]
        if error_lines or (res is not None and res.returncode != 0):
            error_log = tex_path.with_suffix(".error.log")
            error_log.write_bytes(last_output)
            status_desc = (
                error_lines[0] if error_lines else (f"exit code {res.returncode}" if res is not None else "unknown")
            )
            warn(
                f"LaTeX error compiling {error_label}: {status_desc}. "
                f"See {error_log} for the full log. The .tex source is kept for manual fixing."
            )
            return None

        if expected_pdf.exists():
            info(f"Compiled {error_label}: {expected_pdf}")
            return expected_pdf

        warn(
            f"{error_label.capitalize()} did not compile to PDF (see {tex_path.stem}_error.log "
            f"in {work_dir}). Its LaTeX source is still available for manual fixing."
        )
        return None
    finally:
        os.chdir(old_cwd)


def compile_chapter_pdf(c: Course, chapter_number: int, chapter_title: str) -> Path | None:
    """Compile a single chapter's content into its own standalone PDF."""
    pdf_path = c.tex_path / f"ch{int(chapter_number):02d}.pdf"
    tex_path = c.tex_path / f"ch{int(chapter_number):02d}.tex"
    if pdf_path.exists():
        info(f"Chapter {chapter_number} already compiled. Skipping (no LLM/compile work).")
        return pdf_path

    body = get_chapter_body_fragment(c, chapter_number)
    if body is None:
        return None
    document = build_chapter_body_document(c, chapter_number, chapter_title, body)
    c.tex_path.mkdir(parents=True, exist_ok=True)
    tex_path.write_text(document, encoding="utf-8")
    info(f"Wrote LaTeX for chapter {chapter_number}: {tex_path}")
    return _run_pdflatex(c.tex_path, tex_path, pdf_path, error_label=f"chapter {chapter_number}")


def finalize_chapter_pdf_page_numbers(
    c: Course, chapter_number: int, chapter_title: str, start_page: int
) -> Path | None:
    """Recompile chapter with starting page number matching merged book position."""
    pdf_path = c.tex_path / f"ch{int(chapter_number):02d}.pdf"
    tex_path = c.tex_path / f"ch{int(chapter_number):02d}.tex"
    fname = c.drafts_path / f"ch{int(chapter_number):02d}.md"
    if not fname.exists():
        return None
    body = fname.read_text(encoding="utf-8")
    body = _strip_stray_delimiters(body)
    if _looks_like_body_fragment(body):
        body = _sanitize_latex_percent(body)
    document = build_chapter_body_document(c, chapter_number, chapter_title, body, start_page=start_page)
    tex_path.write_text(document, encoding="utf-8")
    return _run_pdflatex(c.tex_path, tex_path, pdf_path, error_label=f"chapter {chapter_number}")


def compile_front_matter(
    c: Course,
    entries: list[tuple[int, str]],
    chapter_page_counts: dict[int, int],
) -> Path | None:
    """Compile title page and table of contents with accurate page numbers."""
    front_tex = c.tex_path / "front_matter.tex"
    front_pdf = c.tex_path / "front_matter.pdf"

    draft_entries: list[tuple[int, str, int | None]] = [(num, title, None) for num, title in entries]
    front_tex.write_text(build_front_matter_document(c, draft_entries), encoding="utf-8")
    pass1_pdf = _run_pdflatex(c.tex_path, front_tex, front_pdf, error_label="front matter (pass 1)")
    if pass1_pdf is None:
        return None
    try:
        from pypdf import PdfReader

        front_page_count = len(PdfReader(str(pass1_pdf)).pages)
    except Exception as e:
        warn(
            f"Couldn't measure front matter length ({e}); falling back to a 1-page estimate, "
            f"so contents page numbers may be off by a page or two."
        )
        front_page_count = 1

    real_entries = []
    running_page = front_page_count + 1
    for num, title in entries:
        real_entries.append((num, title, running_page))
        running_page += chapter_page_counts.get(num, 1)
    front_tex.write_text(build_front_matter_document(c, real_entries), encoding="utf-8")
    return _run_pdflatex(c.tex_path, front_tex, front_pdf, error_label="front matter")


def assemble_course_pdf(
    c: Course,
    front_matter_pdf: Path,
    chapter_pdfs: list[tuple[int, str, Path]],
) -> bool:
    """Concatenate front matter and chapter PDFs into a unified output book."""
    try:
        from pypdf import PdfWriter
    except Exception:
        warn(
            "pypdf isn't installed, so the book can't be assembled into one PDF. "
            f"Run: pip install pypdf. Individual pieces are still in {c.tex_path}."
        )
        return False
    writer = PdfWriter()
    try:
        writer.append(str(front_matter_pdf))
        page_cursor = len(writer.pages)
        for num, title, pdf_path in chapter_pdfs:
            writer.append(str(pdf_path))
            writer.add_outline_item(f"Chapter {num}: {title}", page_cursor)
            page_cursor = len(writer.pages)
        c.output_pdf.parent.mkdir(parents=True, exist_ok=True)
        with open(c.output_pdf, "wb") as f:
            writer.write(f)
    except Exception as e:
        warn(f"Assembling the final book failed ({e}). Individual pieces are still in {c.tex_path}.")
        return False
    finally:
        writer.close()
    return True


def cmd_compile(course_id: str) -> None:
    """Typeset and compile all completed chapters into final single-document book PDF."""
    c = Course.load(course_id)
    require_course_files(c)
    state = load_json(c.generation_state_path)
    if not state.get("final_qa"):
        warn("Final QA wasn't run before compiling. Proceeding anyway.")
    outline = load_json(c.outline_path)
    chapters = outline.get("chapters", [])

    chapter_fragments: list[tuple[int, str, str]] = []
    for ch in chapters:
        num = int(ch.get("number"))
        title = ch.get("title") or f"Chapter {num}"
        body = get_chapter_body_fragment(c, num)
        if body is None:
            warn(
                f"Chapter {num} couldn't be converted to a LaTeX body fragment; skipping it in "
                f"the compiled PDF (its draft is still at {c.drafts_path})."
            )
            continue
        chapter_fragments.append((num, title, body))

    if not chapter_fragments:
        warn(
            "No chapters could be compiled to PDF this run. Your chapter drafts are still "
            f"available as markdown/LaTeX in {c.drafts_path} and {c.tex_path}."
        )
        return
    if len(chapter_fragments) < len(chapters):
        warn(
            f"Only {len(chapter_fragments)}/{len(chapters)} chapters compiled; the book will only "
            f"include the ones that succeeded. The rest are still available individually where "
            f"they got to."
        )

    book_document = build_single_book_document(c, chapter_fragments)
    c.tex_path.mkdir(parents=True, exist_ok=True)
    main_tex = c.tex_path / "main.tex"
    main_pdf = c.tex_path / "main.pdf"
    main_tex.write_text(book_document, encoding="utf-8")
    info(f"Wrote single-document book LaTeX: {main_tex}")

    pdf = _run_pdflatex(c.tex_path, main_tex, main_pdf, error_label="book", passes=3)
    if pdf is None:
        warn(f"Book PDF didn't compile; LaTeX source is still available at {main_tex}.")
        return
    c.output_pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.replace(c.output_pdf)
    info(f"Book compiled: {c.output_pdf}")
    state["compiled"] = True
    save_json(c.generation_state_path, state)
