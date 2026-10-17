# Architectural & Design Decisions

This document records decisions, rationale, trade-offs, and bug fixes made during the codebase cleanup of `richmondsogo/pseudo-scribe`.

---

## Decision Log

### 1. Document Discovered Bugs Before Fixing

* **Context**: Step 0 baseline execution revealed two bugs in `generate.py` that break baseline dry-run execution on Windows without modifications.
* **Bug 1 (DRY_RUN Canned Chapter Output)**:
  * *Location*: `generate.py` lines 244–247.
  * *Issue*: When chapters were migrated to direct LaTeX body fragments, `_looks_like_usable_chapter_body` was added requiring `\section{...}`. However, the `DRY_RUN` canned response still returns markdown (`# Sample Chapter\n\n## Section 1`), causing every chapter in dry-run mode to be rejected as a "degenerate or refusal response" and fail.
  * *Decision*: Document current behavior first in tests. Update the `DRY_RUN` canned response to return a valid LaTeX body fragment (`\section{Section 1}`) that satisfies `_looks_like_usable_chapter_body`.
* **Bug 2 (Windows Console Unicode Encoding)**:
  * *Location*: `generate.py` box-drawing characters in `print_run_summary` and `interactive_setup`.
  * *Issue*: Printing literal Unicode characters (e.g., `\u2500` `─`, `╭`, `╰`) causes `UnicodeEncodeError` under Windows console encodings like `cp1252` unless `PYTHONIOENCODING=utf-8` is forced.
  * *Decision*: Ensure terminal output handles standard stdout encoding gracefully or fallback to ASCII separators when stdout encoding does not support box-drawing glyphs.

### 2. Characterization Testing Baseline

* **Context**: Need to verify that helper functions, outline parsing, delimiter extraction, sanitization, and state transitions preserve exact behavior.
* **Decision**: Implement comprehensive pytest suites covering:
  * Outline parsing: Heuristic regex parsing and LLM extractor mocking.
  * Chapter delimiter parsing: Clean tags, stray delimiters, malformed closing tags (`\end{CHAPTER}`), and leaked terminology blocks.
  * Text & LaTeX sanitization: Bare `%` escaping outside verbatim environments, and delimiter cleanup.
  * Usable body heuristic: Refusal keywords, minimum length check (250 chars), and LaTeX `\section` detection.
  * Terminology merging: Mutex synchronization, duplicate rejection, and first-definition preservation.
  * Retry and backoff: Header parsing (`Retry-After`, `retryDelay`), exponential backoff jitter, and fast failure past `MAX_AUTO_RETRY_WAIT_SECONDS`.
  * Compilation & Error parsing: Scanning `pdflatex` logs for `!` errors despite `nonstopmode`.
  * Configuration: Environment variable defaults and overrides.

### 3. Codebase Modularization & Single Entrypoint

* **Context**: `generate.py` contains ~2100 lines combining configuration, CLI commands, network calls, LaTeX generation, and file I/O.
* **Decision**: Structure the codebase into a clean, cohesive internal package (`src/pseudoscribe/` or `pseudoscribe/`) while keeping `generate.py` as the identical top-level CLI entrypoint. Verify via snapshot tests that `python generate.py` outputs identical files and behavior.
