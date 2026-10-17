"""LaTeX document scaffolding, typography preambles, and sanitization utilities."""

from __future__ import annotations

import re

from pseudoscribe.models import Course
from pseudoscribe.outline import parse_course_profile_fields


def _latex_escape(text: str) -> str:
    """Escapes LaTeX-special characters in plain metadata values.

    Escapes course title, department, etc. before interpolation into
    the deterministic document scaffold.
    """
    if not text:
        return ""
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    out = []
    for ch in text:
        out.append(replacements.get(ch, ch))
    return "".join(out)


def _shared_preamble() -> str:
    """Packages and typography shared by front matter and chapter bodies.

    Typography choices:
    - Latin Modern (lmodern) for a clean textbook serif.
    - parskip for spaced, unindented paragraphs.
    - Tight list spacing via enumitem.
    - hidelinks for clickable links without colored borders.
    """
    lines = [
        "\\documentclass[12pt]{report}\n",
        "\\usepackage[top=2.3cm,bottom=2.5cm,left=2.5cm,right=2.5cm]{geometry}\n",
        "\\usepackage[T1]{fontenc}\n",
        "\\usepackage{lmodern}\n",
        "\\usepackage{microtype}\n",
        "\\usepackage{graphicx}\n",
        "\\usepackage{booktabs}\n",
        "\\usepackage{enumitem}\n",
        "\\setlist{itemsep=0.15em,topsep=0.35em,parsep=0pt}\n",
        "\\usepackage{array}\n",
        "\\usepackage{float}\n",
        "\\usepackage{longtable}\n",
        "\\usepackage{tabularx}\n",
        "\\usepackage{amsmath}\n",
        "\\usepackage{amssymb}\n",
        "\\usepackage{parskip}\n",
        "\\usepackage[hidelinks]{hyperref}\n\n",
    ]
    return "".join(lines)


def build_chapter_body_document(
    c: Course,
    chapter_number: int,
    chapter_title: str,
    body_fragment: str,
    start_page: int | None = None,
) -> str:
    """Standalone, compilable LaTeX document for one chapter's content only.

    No title page or ToC. Chapter numbering is explicitly set via
    \\setcounter{chapter}. start_page sets printed page numbers when known.
    """
    chapter_title_escaped = _latex_escape(chapter_title)
    n = int(chapter_number)
    doc = [_shared_preamble()]
    doc.append("\\begin{document}\n")
    doc.append("\\pagestyle{plain}\n")
    if start_page is not None:
        doc.append(f"\\setcounter{{page}}{{{start_page}}}\n")
    doc.append(f"\\setcounter{{chapter}}{{{n - 1}}}\n")
    doc.append(f"\\chapter{{{chapter_title_escaped}}}\n\n")
    doc.append(body_fragment.strip())
    doc.append("\n\n\\end{document}\n")
    return "".join(doc)


def build_front_matter_document(c: Course, toc_entries: list[tuple[int, str, int | None]]) -> str:
    """Title page plus page-numbered table of contents.

    toc_entries is (chapter_number, title, start_page). start_page is None
    on the first pass, then populated once front-matter length is measured.
    """
    profile_fields = parse_course_profile_fields(c.profile_path.read_text(encoding="utf-8"))
    course_code = _latex_escape(profile_fields.get("course code", ""))
    course_title = _latex_escape(profile_fields.get("course title", "Lecture Notes"))
    department = _latex_escape(profile_fields.get("department", ""))
    university = _latex_escape(profile_fields.get("university", ""))
    academic_session = _latex_escape(profile_fields.get("academic session", ""))
    prepared_by = _latex_escape(profile_fields.get("prepared by", ""))

    doc = [_shared_preamble()]
    doc.append("\\begin{document}\n\n")
    doc.append("\\begin{titlepage}\n")
    doc.append("\\thispagestyle{empty}\n")
    doc.append("\\centering\n")
    doc.append("\\vspace*{2cm}\n")
    doc.append(f"{{\\LARGE {course_title}}}\\\\[1cm]\n")
    doc.append(f"{{\\Large {course_code}}}\\\\[1cm]\n")
    doc.append("{\\Large Lecture Notes}\\\\[1.5cm]\n")
    doc.append(f"{department}\\\\\n")
    doc.append(f"{university}\\\\\n")
    doc.append(f"{academic_session}\\\\[2cm]\n")
    doc.append("Prepared by\\\\\n")
    doc.append(f"{prepared_by}\n")
    doc.append("\\end{titlepage}\n\n")
    doc.append("\\pagestyle{plain}\n")
    doc.append("\\chapter*{Contents}\n")
    doc.append("\\addcontentsline{toc}{chapter}{Contents}\n")
    doc.append("\\vspace{0.5em}\n")
    doc.append("\\noindent\n")
    for num, title, start_page in toc_entries:
        title_escaped = _latex_escape(title)
        page_str = str(start_page) if start_page is not None else "\\phantom{0}"
        doc.append(f"Chapter {num}: {title_escaped} \\dotfill\\ {page_str}\\\\[0.6em]\n")
    doc.append("\n\\end{document}\n")
    return "".join(doc)


def build_single_book_document(c: Course, chapter_fragments: list[tuple[int, str, str]]) -> str:
    """Build the whole book as a single LaTeX document.

    Includes title page, real \\tableofcontents, and all chapter bodies.
    chapter_fragments is (chapter_number, title, body_fragment).
    """
    profile_fields = parse_course_profile_fields(c.profile_path.read_text(encoding="utf-8"))
    course_code = _latex_escape(profile_fields.get("course code", ""))
    course_title = _latex_escape(profile_fields.get("course title", "Lecture Notes"))
    department = _latex_escape(profile_fields.get("department", ""))
    university = _latex_escape(profile_fields.get("university", ""))
    academic_session = _latex_escape(profile_fields.get("academic session", ""))
    prepared_by = _latex_escape(profile_fields.get("prepared by", ""))

    doc = [_shared_preamble()]
    doc.append("\\begin{document}\n")
    doc.append("\\begin{titlepage}\n")
    doc.append("\\thispagestyle{empty}\n")
    doc.append("\\centering\n")
    doc.append("\\vspace*{2cm}\n")
    doc.append(f"{{\\LARGE {course_title}}}\\\\[1cm]\n")
    doc.append(f"{{\\Large {course_code}}}\\\\[1cm]\n")
    doc.append("{\\Large Lecture Notes}\\\\[1.5cm]\n")
    doc.append(f"{department}\\\\\n")
    doc.append(f"{university}\\\\\n")
    doc.append(f"{academic_session}\\\\[2cm]\n")
    doc.append("Prepared by\\\\\n")
    doc.append(f"{prepared_by}\n")
    doc.append("\\end{titlepage}\n\n")
    doc.append("\\tableofcontents\n")
    doc.append("\\newpage\n\n")
    for num, title, body in chapter_fragments:
        n = int(num)
        doc.append(f"\\setcounter{{chapter}}{{{n - 1}}}\n")
        doc.append(f"\\chapter{{{_latex_escape(title)}}}\n\n")
        doc.append(body.strip())
        doc.append("\n\n")
    doc.append("\\end{document}\n")
    return "".join(doc)


def _sanitize_latex_percent(text: str) -> str:
    """Escapes bare '%' characters in a LaTeX body fragment to '\\%'.

    Verbatim blocks (\\begin{verbatim}...\\end{verbatim}) are left untouched.
    """
    lines = text.split("\n")
    out = []
    in_verbatim = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("\\begin{verbatim}"):
            in_verbatim = True
        if not in_verbatim:
            line = line.replace("%", r"\%")
        if stripped.startswith("\\end{verbatim}"):
            in_verbatim = False
        out.append(line)
    return "\n".join(out)


def _strip_stray_delimiters(text: str) -> str:
    """Strip leftover [TERMINOLOGY]...[/TERMINOLOGY] and [CHAPTER]...[/CHAPTER] blocks."""
    t = text
    while "[TERMINOLOGY]" in t and "[/TERMINOLOGY]" in t:
        start = t.find("[TERMINOLOGY]")
        end = t.find("[/TERMINOLOGY]") + len("[/TERMINOLOGY]")
        t = (t[:start] + t[end:]).strip()

    if "[CHAPTER]" in t and "[/CHAPTER]" in t:
        start = t.find("[CHAPTER]")
        end = t.rfind("[/CHAPTER]")
        end = end + len("[/CHAPTER]") if end != -1 else len(t)
        inner = t[start + len("[CHAPTER]") : end]
        if "[TERMINOLOGY]" not in inner:
            t = inner.strip()

    t = (
        t.replace("[CHAPTER]", "")
        .replace("[/CHAPTER]", "")
        .replace("[TERMINOLOGY]", "")
        .replace("[/TERMINOLOGY]", "")
        .strip()
    )
    return t


def _looks_like_body_fragment(text: str) -> bool:
    """Detect if draft is a LaTeX body fragment (\\section, no \\chapter/\\documentclass)."""
    head = text.strip()[:300]
    if re.search(r"\\documentclass|\\begin\{document\}", head):
        return False
    return bool(re.match(r"\\section\s*\{", head))


def _looks_like_usable_chapter_body(text: str) -> bool:
    """Validate that LLM response is a real chapter draft rather than a refusal or one-liner.

    Requires at least one LaTeX \\section command, no delimiter markers,
    and a minimum length of 250 characters.
    """
    t = (text or "").strip()
    if not t:
        return False
    if "[TERMINOLOGY]" in t or "[CHAPTER]" in t or "[/CHAPTER]" in t:
        return False
    low = t.lower()
    refusal_markers = (
        "user safety:",
        "i can't help",
        "i cannot help",
        "i am unable",
        "i'm unable",
        "as an ai",
        "i apologize",
        "sorry, i",
        "i don't have enough",
        "cannot assist",
        "can't assist",
    )
    if any(marker in low for marker in refusal_markers):
        return False
    if len(t) < 250:
        return False
    if not re.search(r"\\(section|subsection|subsubsection|section\*)\s*\{", t):
        return False
    return True
