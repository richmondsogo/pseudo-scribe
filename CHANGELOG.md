# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] - Code cleanup, tests, CI, documentation sync

### Added
- Modular package architecture under `pseudoscribe/` (`config`, `models`, `utils`, `retries`, `providers`, `prompts_loader`, `outline`, `terminology`, `latex`, `chapter`, `qa`, `compiler`, `pipeline`, `cli`).
- Comprehensive characterization test suite under `tests/` covering:
  - Syllabus outline parsing and learning outcome heuristic normalization
  - Stray delimiter extraction and delimiter leak healing
  - LaTeX percent escaping and document scaffolding
  - Refusal and one-liner response detection
  - Terminology registry conflict resolution
  - Exponential backoff and Retry-After header parsing with mocked clocks
  - Atomic JSON persistence and course state transitions
  - End-to-end golden snapshot regression testing under `DRY_RUN=1`.
- GitHub Actions CI workflow (`.github/workflows/ci.yml`) testing Python 3.11 and 3.12 with linting and offline dry-run test suite.
- Code quality tooling configured in `pyproject.toml` using Ruff (linter and formatter) with strict modern Python standards.
- CLI `--version` flag displaying package version without affecting command dispatch.
- Project guidelines in `CONTRIBUTING.md` and architecture decision records in `docs/DECISIONS.md`.

### Changed
- Re-architected `generate.py` as a backward-compatible CLI entry point that re-exports all constants, functions, and classes from `pseudoscribe`.
- Enhanced file write safety with atomic write-and-replace (`.tmp` + `os.replace`) to prevent state corruption upon interruption.
- Standardized character encoding with explicit UTF-8 and safe fallback console printing for Windows CP1252/terminal environments.
- Modernized type hints across the entire codebase.

### Fixed
- Fixed `DRY_RUN` chapter mock returning Markdown instead of valid LaTeX fragment, enabling reliable offline pipeline verification.
- Fixed UnicodeEncodeError on Windows terminals when rendering box-drawing boundary characters.

### Removed
- Removed obsolete `fix_cjk.py` scratch script.
