# Architecture & Pipeline Design

This document details the internal design, data flow, and safeguards of Pseudo-Scribe.

---

## Pipeline Overview

Pseudo-Scribe executes sequentially or semi-concurrently across structured pipeline stages:

```text
               +----------------------------------+
               |  Course Outline (File or Paste)  |
               +----------------------------------+
                                |
                                v
               +----------------------------------+
               |    Outline Parsing & Chapters    |  (LLM extractor with regex fallback)
               +----------------------------------+
                                |
                                v
               +----------------------------------+
               |  Initial Terminology Extraction  |  (Seeded into terminology.json)
               +----------------------------------+
                                |
                                v
               +----------------------------------+
               |     Chapter Generation Loop      |  (Direct LaTeX body fragments)
               |  - New terms extracted & merged  |  (First definition always wins)
               |  - Refusals & delimiters cleaned |
               |  - Percent signs (%) sanitized   |
               +----------------------------------+
                                |
                                v
               +----------------------------------+
               |   Optional Chapter Enhancement   |  (Adds ASCII diagrams & polish)
               +----------------------------------+
                                |
                                v
               +----------------------------------+
               |      Final Course-Wide QA        |  (Bounded character excerpts)
               +----------------------------------+
                                |
                                v
               +----------------------------------+
               |      LaTeX Book Compilation      |  (Single main.tex, 3 pdflatex passes)
               +----------------------------------+
                                |
                                v
               +----------------------------------+
               |            output.pdf            |
               +----------------------------------+
```

---

## Course Lifecycle and State Tracking

Each course is managed in its own isolated directory under `courses/<COURSE_ID>/`.

Progress is tracked in `generation_state.json`:

```json
{
  "status": "in_progress",
  "chapters": {
    "1": "complete",
    "2": "complete"
  },
  "final_qa": true,
  "final_qa_passed": true,
  "compiled": true
}
```

* **Resumability**: If a run fails due to quota limits or process interruption, re-running `python generate.py` or `python generate.py build <COURSE_ID>` checks `generation_state.json` and skips completed chapters.
* **Concurrency Protection**: Thread-safe operations use `threading.Lock` (`STATE_LOCK` and `TERMINOLOGY_LOCK`) to prevent race conditions during concurrent chapter generation (`MAX_CONCURRENT_CHAPTERS > 1`).

---

## Detailed Pipeline Stages

### 1. Outline Ingestion and Chapter Extraction

Outlines can be supplied as raw pasted text or loaded from `.json`, `.md`, `.txt`, `.docx` (via `python-docx`), or `.pdf` (via `PyPDF2`).

* **Primary Extractor (`parse_outline_text_to_chapters_llm`)**: Calls the LLM to separate actual teachable course content from learning outcomes (which often start with verbs like "discuss", "apply", "analyze"). It segments the curriculum into structured chapters with short noun-phrase titles and subtopic descriptions.
* **Title Normalization (`normalize_chapter_title`)**: Regex rules convert common syllabus outcome phrases into academic chapter titles (e.g., "analyze the concepts of X" becomes "Foundations of X").
* **Fallback Parser (`parse_outline_text_to_chapters_heuristic`)**: If the LLM extraction fails or is unavailable, a regex heuristic scans for headings and numbered markers.

### 2. Terminology Registry and Conflict Handling

Terminology consistency is enforced course-wide through `terminology.json`.

* **Initial Seeding**: `cmd_terminology` runs `prompts/terminology_extractor.txt` against the course profile and outline to extract canonical technical terms.
* **Incremental Updates**: Each chapter generation returns a `[TERMINOLOGY]` JSON payload. Proposed terms are merged under a mutex lock so subsequent chapters see previously established definitions.
* **Conflict Resolution**: If a chapter redefines an existing term, **the original definition is always retained**. No silent overwrites occur.
* **Semantic Verification (`ENABLE_CONFLICT_CHECK=1`)**: When enabled, the LLM evaluates whether a restatement represents a genuine conceptual conflict. Warnings are recorded in `run_warnings.json`.

### 3. Chapter Generation and Guardrails

Chapters are generated using `prompts/chapter_writer.txt`.

* **Direct LaTeX Fragments**: To minimize LLM calls, chapters are written directly as LaTeX body fragments (starting with `\section`), omitting document scaffolding (`\documentclass`, `\chapter`, title pages) which is handled programmatically.
* **Output Format**: Models return two delimited blocks:
  * `[TERMINOLOGY] ... [/TERMINOLOGY]`
  * `[CHAPTER] ... [/CHAPTER]`
* **Refusal Detection**: Responses shorter than 250 characters, containing refusal keywords ("User Safety: safe", "I cannot help"), or missing `\section` tags are rejected. The chapter is retried up to `MAX_CHAPTER_RETRIES`.
* **Delimiter & LaTeX Sanitization**:
  * Delimiters (`[CHAPTER]`, `[TERMINOLOGY]`) and stray `\end{CHAPTER}` tags are stripped from output.
  * Bare `%` characters outside verbatim blocks are automatically escaped to `\%` to prevent unintended LaTeX line truncation.
* **ASCII Diagrams**: Prompts instruct the model to render flowcharts, pipelines, and architecture diagrams in plain ASCII wrapped inside `\begin{verbatim}...\end{verbatim}`.

### 4. Optional Chapter Enhancement

When `ENABLE_CHAPTER_ENHANCE=1` (or `ENABLE_CHAPTER_QA=1`), a secondary pass runs `prompts/chapter_enhancer.txt` on the draft. This pass:
* Checks for missing diagrams and inserts ASCII diagrams where visual explanations aid comprehension.
* Verifies table structures (`booktabs`, `tabularx`).
* If the enhancement call fails, the pipeline safely falls back to the original draft.

### 5. Final Quality Assurance

`cmd_qa` executes `prompts/final_qa.txt` across the entire course.

* **Bounded Excerpts (`FINAL_QA_EXCERPT_CHARS`)**: Rather than sending hundreds of thousands of words into one prompt, Pseudo-Scribe extracts a bounded excerpt (default: 1000 characters) per chapter. This provides sufficient signal to assess progression, terminology, and tone without exceeding TPM limits.
* **QA Findings**: The review checks for curriculum coverage, cross-chapter contradictions, and terminology drift. The structured result is written to `final/final_qa.json`. Critical issues generate warnings in `run_warnings.json` without abruptly stopping compilation.

### 6. LaTeX Book Compilation

Document compilation is handled in `cmd_compile`:

* **Shared Preamble (`_shared_preamble`)**: Establishes uniform typography using:
  * `lmodern`: Latin Modern clean textbook serif font.
  * `geometry`: Standard margins (`top=2.3cm, bottom=2.5cm, left=2.5cm, right=2.5cm`).
  * `microtype` & `parskip`: Spaced, unindented paragraphs.
  * `booktabs`, `longtable`, `tabularx`: Center-aligned, borderless tables.
  * `amsmath`, `amssymb`: Mathematical notation.
  * `enumitem`: Compact list spacing (`itemsep=0.15em`).
  * `hyperref` with `hidelinks`: Fully clickable table of contents and PDF links without colored rectangles.
* **Unified Document Assembly**: `build_single_book_document` merges metadata, the title page, a native `\tableofcontents`, and all chapter fragments into `tex/main.tex`.
* **Three-Pass pdflatex**:
  * Pass 1: Generates auxiliary files (`.toc`, `.aux`).
  * Passes 2 & 3: Resolves cross-references, table of contents page numbers, and PDF hyperlink targets.
* **Log Error Parsing**: Because `pdflatex -interaction=nonstopmode` can exit with code 0 even when fatal formatting errors occur, Pseudo-Scribe parses the raw compilation log for lines starting with `!` and flags errors with log extracts saved to `tex/main.error.log`.
* **Deliverable**: The resulting `tex/main.pdf` is moved to `output.pdf`.

---

## Codebase Architecture & Modular Package Structure

The codebase is organized into an internal package `pseudoscribe/` with single-responsibility modules, accessed via the root CLI entry point `generate.py`:

* `pseudoscribe/cli.py`: Interactive setup wizard and CLI command parsing (`argparse`).
* `pseudoscribe/pipeline.py`: Course folder skeleton creation (`cmd_init`) and pipeline orchestration (`run_pipeline`).
* `pseudoscribe/config.py`: Centralized, typed runtime configuration (`Settings`) and environment variable accessors.
* `pseudoscribe/models.py`: Course directory and artifact path mappings (`Course`), plus thread-safe mutex locks (`STATE_LOCK`, `TERMINOLOGY_LOCK`).
* `pseudoscribe/utils.py`: Safe console printing (`_safe_print`), atomic JSON file I/O (`save_json`), and resilient JSON block extraction (`_extract_json`).
* `pseudoscribe/retries.py`: Exponential backoff with jitter and `Retry-After` header parsing (`call_with_retry`).
* `pseudoscribe/providers.py`: API integration for Gemini, Groq, OpenRouter, DeepSeek, and dry-run mocks (`call_llm`).
* `pseudoscribe/prompts_loader.py`: Template loading and placeholder interpolation.
* `pseudoscribe/outline.py`: Syllabus outline segmentation and title normalization heuristics.
* `pseudoscribe/terminology.py`: Canonical terminology registry and LLM conflict verification.
* `pseudoscribe/latex.py`: Preamble generation, book scaffolding, percent sanitization, and delimiter cleanup.
* `pseudoscribe/chapter.py`: Chapter generation loop, enhancement pass, and legacy draft conversion.
* `pseudoscribe/qa.py`: Final course QA analysis and report serialization.
* `pseudoscribe/compiler.py`: `pdflatex` execution and PDF page re-numbering.
* `generate.py`: Backward-compatible root entry point re-exporting all symbols from `pseudoscribe`.

For historical decisions and design trade-offs, see [docs/DECISIONS.md](DECISIONS.md).
