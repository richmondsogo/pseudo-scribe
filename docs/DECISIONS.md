# Architectural & Design Decisions

This document records decisions, rationale, trade-offs, bug fixes, and recommendations made during the codebase cleanup and refactoring of `richmondsogo/pseudo-scribe`.

---

## Decision Log

### 1. Document Discovered Bugs Before Fixing

* **Context**: Step 0 baseline execution revealed two bugs in `generate.py` that broke baseline dry-run execution on Windows without modifications.
* **Bug 1 (DRY_RUN Canned Chapter Output)**:
  * *Location*: `generate.py` lines 244–247 (now in `pseudoscribe/providers.py`).
  * *Issue*: When chapters were previously migrated to direct LaTeX body fragments, `_looks_like_usable_chapter_body` was added requiring a LaTeX `\section{...}` command. However, the `DRY_RUN` canned chapter response still returned Markdown (`# Sample Chapter\n\n## Section 1`), causing every chapter in dry-run mode to be rejected as a "degenerate or refusal response" and fail.
  * *Decision*: Document current behavior first in tests. Update the `DRY_RUN` canned response to return a valid LaTeX body fragment (`\section{Overview}...`) that satisfies `_looks_like_usable_chapter_body`. This makes end-to-end dry-run testing possible without changing production LLM prompt handling.
* **Bug 2 (Windows Console Unicode Encoding)**:
  * *Location*: `generate.py` box-drawing characters in `print_run_summary` and `interactive_setup`.
  * *Issue*: Printing literal Unicode characters (e.g., `\u2500` `─`, `╭`, `╰`) causes `UnicodeEncodeError` under Windows console encodings like `cp1252` unless `PYTHONIOENCODING=utf-8` is forced.
  * *Decision*: Introduce `_safe_print` helper that catches `UnicodeEncodeError` and falls back to terminal-safe ASCII replacement characters.

### 2. Characterization Testing Baseline

* **Context**: Need to verify that helper functions, outline parsing, delimiter extraction, sanitization, and state transitions preserve exact behavior across refactoring stages.
* **Decision**: Implement comprehensive pytest suites covering:
  * Outline parsing: Heuristic regex parsing and LLM extractor mocking.
  * Chapter delimiter parsing: Clean tags, stray delimiters, malformed closing tags (`\end{CHAPTER}`), and leaked terminology blocks.
  * Text & LaTeX sanitization: Bare `%` escaping outside verbatim environments, and delimiter cleanup.
  * Usable body heuristic: Refusal keywords, minimum length check (250 chars), and LaTeX `\section` detection.
  * Terminology merging: Mutex synchronization, duplicate rejection, and first-definition preservation.
  * Retry and backoff: Header parsing (`Retry-After`, `retryDelay`), exponential backoff jitter, and fast failure past `MAX_AUTO_RETRY_WAIT_SECONDS`.
  * Compilation & Error parsing: Scanning `pdflatex` logs for `!` errors despite `nonstopmode`.
  * Configuration: Environment variable defaults and overrides.
  * End-to-end golden snapshot regression: Comparing all generated artifacts (`outline.json`, `terminology.json`, `generation_state.json`, chapter drafts, `final_qa.json`, `.tex` files) against baseline snapshots.

### 3. Modularization into `pseudoscribe/` Package with Root Entry Point

* **Context**: `generate.py` previously contained ~2,100 lines combining configuration, CLI commands, network calls, LaTeX generation, and file I/O into a single file.
* **Decision**: Decompose the codebase into a clean, cohesive internal package `pseudoscribe/`:
  * `pseudoscribe/config.py`: Typed `Settings` dataclass and environment variable parsing with backward-compatible defaults.
  * `pseudoscribe/models.py`: `Course` artifact mappings and concurrency locks (`STATE_LOCK`, `TERMINOLOGY_LOCK`).
  * `pseudoscribe/utils.py`: `_safe_print`, logging, atomic `save_json`, and `_extract_json`.
  * `pseudoscribe/retries.py`: `call_with_retry`, transient error detection, and `Retry-After` header extraction.
  * `pseudoscribe/providers.py`: Dispatch for Gemini, Groq, OpenRouter, DeepSeek, and dry-run responses.
  * `pseudoscribe/prompts_loader.py`: `load_prompt` and template placeholder replacement.
  * `pseudoscribe/outline.py`: Syllabus outline parsing (LLM and regex heuristics) and `course_profile.md` generation.
  * `pseudoscribe/terminology.py`: Terminology registry loading, persistence, and LLM conflict checking.
  * `pseudoscribe/latex.py`: Preamble, chapter/book document scaffolding, and sanitization.
  * `pseudoscribe/chapter.py`: Chapter generation, enhancement pass, and legacy draft conversion.
  * `pseudoscribe/qa.py`: Final QA consolidation and reporting.
  * `pseudoscribe/compiler.py`: LaTeX compilation execution and multi-chapter PDF assembly.
  * `pseudoscribe/pipeline.py`: Course initialization and end-to-end pipeline runner.
  * `pseudoscribe/cli.py`: Interactive setup wizard, argparse CLI parsing, and `--version` support.
* **Entry Point Parity**: `generate.py` remains at the repository root as a thin wrapper that re-exports all constants, functions, and classes from `pseudoscribe`, ensuring 100% backward compatibility for all CLI invocations and existing tests.

### 4. Removal of Dead Code (`fix_cjk.py`)

* **Context**: `fix_cjk.py` was committed at root, containing hardcoded Windows absolute paths (`c:\Users\Richmond\...`) that attempted to patch `\usepackage{CJK}` into `generate.py`.
* **Decision**: Remove `fix_cjk.py` as it is dead scratch code unreferenced anywhere in the project.

### 5. Atomic File Persistence

* **Context**: If a generation run is aborted (Ctrl+C, power outage, network termination) while writing `generation_state.json` or `terminology.json`, files could be truncated or corrupted, preventing resumption.
* **Decision**: Update `save_json` to write to a temporary file (`.tmp`), flush and sync to disk (`os.fsync`), and atomically replace the destination path (`os.replace` / `Path.replace`).

### 6. Dev Tools & CI Configuration

* **Context**: Requirements specify Ruff for linting/formatting and GitHub Actions CI on Python 3.11 and 3.12 without external secrets or pdflatex dependencies.
* **Decision**:
  * Added `pyproject.toml` with Ruff configuration enforcing modern Python standards (`pyupgrade`, `isort`, `flake8-bugbear`).
  * Added `.github/workflows/ci.yml` running on push and PR with `DRY_RUN: "1"`.
  * Added `requirements-dev.txt` (`pytest>=8.0.0`, `ruff>=0.4.0`) kept separate from runtime `requirements.txt`.
