# Pseudo-Scribe

Pseudo-Scribe is a command-line tool that generates complete, professionally typeset university lecture notes as a single PDF from a course outline. Built for lecturers, students, and curriculum authors.

---

## What It Produces

Pseudo-Scribe generates a single, publication-ready PDF book (`output.pdf`) containing:
* A typeset title page with course metadata and institutional attribution.
* A native LaTeX table of contents with borderless, clickable page links.
* Academically structured chapters typeset in Latin Modern with ASCII diagrams and end-of-chapter tutorial questions.
* Standard PDF navigation bookmarks.

```text
Course Outline ──> Terminology Extraction ──> Chapter Generation (LaTeX) ──> QA ──> pdflatex (3 passes) ──> output.pdf
```

---

## Key Features

* **Consistent Terminology**: A central `terminology.json` registry is seeded from the outline and updated as chapters are written. Earlier definitions are never overwritten.
* **Direct LaTeX Output**: The model writes LaTeX body fragments directly, avoiding conversion artifacts and separate typesetting passes.
* **Resumable Pipeline**: State is tracked in `generation_state.json`. Interrupted runs pick up from completed work without repeating API calls.
* **Multi-Provider Support**: Works with Gemini (default), Groq, OpenRouter, and DeepSeek. Heavy QA and typesetting calls can be routed to a secondary provider.
* **Built-in Guardrails**: Detects and rejects safety refusals, sanitizes unescaped `%` comments, cleans delimiter leaks, and validates `pdflatex` compilation logs for hidden errors.

---

## Requirements and Installation

### Prerequisites

* **Python 3.11+**
* **LaTeX Distribution** with `pdflatex` available on your `PATH`:
  * **Windows**: [MiKTeX](https://miktex.org/)
  * **macOS / Linux**: [TeX Live](https://www.tug.org/texlive/) (`texlive-latex-base`, `texlive-latex-extra`, `texlive-fonts-recommended`)

### Setup Commands

**Windows (PowerShell)**:
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**macOS / Linux (bash)**:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## Quick Start

1. **Configure your provider**:
   ```bash
   # Windows (PowerShell)
   Copy-Item .env.example .env

   # macOS / Linux (bash)
   cp .env.example .env
   ```
   Add your API key (e.g. `GEMINI_API_KEY=your_key` for the default Gemini provider).

2. **Run the interactive wizard**:
   ```bash
   python generate.py
   ```
   Provide course metadata and paste your outline (or supply a `.json`, `.md`, `.txt`, `.docx`, or `.pdf` file). The pipeline generates the notes and compiles `output.pdf`.

---

## CLI Reference

| Command | Arguments | Description |
| --- | --- | --- |
| `python generate.py` | *None* | Interactive wizard for new courses or resuming incomplete runs. |
| `python generate.py --version` | *None* | Display program version (`Pseudo-Scribe 1.0.0`). |
| `python generate.py init` | `<COURSE_ID>` | Create a course folder skeleton under `courses/<COURSE_ID>/`. |
| `python generate.py profile` | `<COURSE_ID>` | Create or edit `course_profile.md` metadata template. |
| `python generate.py terminology` | `<COURSE_ID>` | Extract initial terminology registry from the outline. |
| `python generate.py chapter` | `<COURSE_ID> <N>` | Generate or regenerate a single chapter by number. |
| `python generate.py all` | `<COURSE_ID>` | Generate all uncompleted chapters (respects concurrency settings). |
| `python generate.py qa` | `<COURSE_ID>` | Run final course-wide QA across bounded chapter excerpts. |
| `python generate.py compile` | `<COURSE_ID>` | Compile unified book (`tex/main.tex`) to `output.pdf`. |
| `python generate.py build` | `<COURSE_ID>` | Run terminology, missing chapters, QA, and compilation end-to-end. |

---

## Configuration

Pseudo-Scribe reads settings from `.env`. See [docs/configuration.md](docs/configuration.md) for full descriptions and model tuning options.

| Variable | Default | Purpose |
| --- | --- | --- |
| `LLM_PROVIDER` | `gemini` | Primary provider: `gemini`, `groq`, `openrouter`, or `deepseek`. |
| `HEAVY_CALL_PROVIDER` | *empty* | Provider override for token-heavy QA and fallback typesetting calls. |
| `GEMINI_API_KEY` | *empty* | Gemini API key (`GOOGLE_API_KEY` also accepted). |
| `GROQ_API_KEY` | *empty* | Groq API key. Model set via `GROQ_MODEL` (default: `llama-3.3-70b-versatile`). |
| `OPENROUTER_API_KEY` | *empty* | OpenRouter API key. Model set via `OPENROUTER_MODEL` (default: `openrouter/free`). |
| `DEEPSEEK_API_KEY` | *empty* | DeepSeek API key. Model set via `DEEPSEEK_MODEL` (default: `deepseek-chat`). |
| `LLM_TEMPERATURE` | `0.2` | Sampling temperature for text generation. |
| `MAX_CONCURRENT_CHAPTERS` | `1` | Max threads for parallel chapter generation. Keep at 1 for rate-limited tiers. |
| `MAX_CHAPTER_RETRIES` | `2` | Retries per chapter when model output is empty, a refusal, or invalid LaTeX. |
| `MAX_API_RETRIES` | `5` | Retries for transient API failures (5xx, 429, timeouts, connection resets). |
| `API_RETRY_BASE_DELAY` | `2.0` | Base exponential backoff delay in seconds between retries. |
| `MAX_AUTO_RETRY_WAIT_SECONDS` | `90` | Halts immediately if a provider demands a retry wait longer than this limit. |
| `HTTP_TIMEOUT_SECONDS` | `180` | Request timeout in seconds for compatible chat completions. |
| `FINAL_QA_EXCERPT_CHARS` | `1000` | Character limit per chapter sent to final course QA to control prompt size. |
| `ENABLE_CHAPTER_ENHANCE` | `0` | Optional second pass to insert ASCII diagrams and polish text (alias: `ENABLE_CHAPTER_QA`). |
| `ENABLE_CONFLICT_CHECK` | `0` | Optional LLM check on redefined terms (original definitions are always kept). |
| `DRY_RUN` | `0` | Run the pipeline with canned responses and dummy PDFs without API calls. |

---

## Directory Layout

```text
pseudo-scribe/
├── generate.py              # CLI entry point (backward-compatible re-exporter)
├── pseudoscribe/            # Core package
│   ├── cli.py               # Argument parsing and interactive wizard
│   ├── pipeline.py          # Course skeleton creation and lifecycle runner
│   ├── config.py            # Typed settings and environment parsing
│   ├── models.py            # Course artifact mappings and concurrency locks
│   ├── utils.py             # Atomic file I/O, logging, safe printing
│   ├── retries.py           # Exponential backoff and Retry-After handling
│   ├── providers.py         # LLM provider dispatch and dry-run mocks
│   ├── prompts_loader.py    # Template loading and context rendering
│   ├── outline.py           # Outline parsing (LLM and heuristic)
│   ├── terminology.py       # Terminology registry and conflict checker
│   ├── chapter.py           # Chapter writer, enhancement, draft conversion
│   ├── qa.py                # Final course QA analysis
│   ├── latex.py             # Preamble, document scaffolding, sanitizers
│   └── compiler.py          # pdflatex runner and PDF assembly
├── prompts/                 # Prompt templates (writer, extractor, QA, enhancer)
├── tests/                   # Pytest suite and golden regression snapshots
├── docs/                    # Detailed guides
│   ├── configuration.md     # In-depth environment configuration guide
│   ├── architecture.md      # Pipeline architecture and lifecycle reference
│   └── DECISIONS.md         # Architecture and design decision records
├── requirements.txt         # Runtime dependencies
├── requirements-dev.txt     # Development and testing dependencies (pytest, ruff)
├── pyproject.toml           # Ruff linter, formatter, and pytest configuration
├── .github/workflows/ci.yml # GitHub Actions CI workflow
├── CHANGELOG.md             # Keep a Changelog version history
├── CONTRIBUTING.md          # Contribution guidelines
├── .env.example             # Example environment configuration
└── courses/<COURSE_ID>/     # Isolated course workspace
    ├── course_profile.md    # Course metadata, university info, and style rules
    ├── outline.json         # Structured chapter list and subtopics
    ├── terminology.json     # Canonical course terminology registry
    ├── generation_state.json# State tracking for resumable execution
    ├── prompts/             # Course-specific prompt snapshot copied on init
    ├── drafts/              # Generated per-chapter LaTeX body fragments (ch01.md, ...)
    ├── tex/                 # Unified book LaTeX (main.tex) and build logs
    ├── final/               # Assembled full markdown and final_qa.json report
    ├── output.pdf           # Final compiled PDF lecture notes
    └── run_warnings.json    # Warnings collected across the run
```

---

## Development

Pseudo-Scribe includes a complete offline test suite and automated linting.

### Development Setup
```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-dev.txt
```

### Running Tests
Execute the test suite with `pytest`:
```bash
pytest -v
```
All unit and end-to-end characterization tests run under `DRY_RUN=1` and require neither external API keys nor `pdflatex`.

### Linting and Formatting
Check code quality with [Ruff](https://docs.astral.sh/ruff/):
```bash
ruff check .
ruff format --check .
```
To automatically apply fixes and format:
```bash
ruff check --fix .
ruff format .
```

### Continuous Integration (CI)
GitHub Actions (`.github/workflows/ci.yml`) runs Ruff linting, formatting checks, and pytest on Python 3.11 and 3.12 for every push and pull request.

---

## How It Works

1. **Terminology Registry**: `cmd_terminology` seeds `terminology.json` from the outline. As chapters are generated, any new terms declared in `[TERMINOLOGY]` blocks are merged under a mutex lock. If an existing term is redefined, the original definition is preserved.
2. **Direct LaTeX Chapter Generation**: Prompts instruct the LLM to output clean LaTeX body fragments starting with `\section`. Output is validated to ensure it is valid LaTeX, exceeds 250 characters, and contains no model refusal strings. Stray delimiter tags and unescaped `%` characters are sanitized.
3. **Quality Assurance**: `cmd_qa` inspects bounded excerpts across all chapters for syllabus coverage, cross-chapter contradictions, and terminology drift, saving findings to `final/final_qa.json`.
4. **Unified Compilation**: `cmd_compile` builds a single document (`tex/main.tex`) combining the title page, LaTeX's native `\tableofcontents`, and chapter fragments. `pdflatex` executes 3 passes with Latin Modern typography, and raw logs are checked for error markers.

For deeper technical details, see [docs/architecture.md](docs/architecture.md).

---

## Troubleshooting

| Symptom | Cause | Solution |
| --- | --- | --- |
| `'pdflatex' isn't installed or isn't on PATH` | Missing or unlinked LaTeX system. | Install MiKTeX (Windows) or TeX Live (macOS/Linux) and verify `pdflatex --version`. |
| `LaTeX error compiling book` | Malformed LaTeX in chapter fragment. | Inspect `courses/<COURSE_ID>/tex/main.error.log`. Edit `drafts/chXX.md` and rerun `python generate.py compile <COURSE_ID>`. |
| `Chapter rejected as refusal or degenerate` | LLM returned a refusal, one-liner, or raw markdown. | Pipeline retries automatically. If retries exhaust, check API key/quota and run `python generate.py chapter <COURSE_ID> <N>`. |
| `Provider says to retry in ~X min (exceeds MAX_AUTO_RETRY_WAIT_SECONDS)` | Provider daily or quota reset interval exceeded. | Work is saved. Re-run `python generate.py build <COURSE_ID>` after the indicated time. |
| `WinError 10054 / connection reset` | Socket drops during chat completions. | Install `requests>=2.31.0` in your venv, which handles connection resets more reliably than `urllib`. |
| `pypdf / PyPDF2 import error` | Missing optional dependency for PDF handling. | Run `pip install -r requirements.txt`. |
| `Titles look like learning outcomes` | Outline phrasing mixed outcomes with topics. | The primary LLM extractor normalizes titles automatically; edit `courses/<COURSE_ID>/outline.json` manually if needed. |

---

## Extending

* **Add an LLM Provider**: Implement a provider helper in `pseudoscribe/providers.py` and register it in `call_llm()`. See [CONTRIBUTING.md](CONTRIBUTING.md) for step-by-step guidance.
* **Customize Writing Style**: Edit `prompts/chapter_writer.txt` globally, or modify `courses/<COURSE_ID>/prompts/chapter_writer.txt` for a single course.
* **Adjust Typesetting**: Modify `_shared_preamble()` in `pseudoscribe/latex.py` to change typography, geometry margins, or LaTeX packages.

---

## Limitations

* **Subject-Matter Verification**: Content is AI-generated and must be reviewed by qualified instructors prior to classroom use.
* **Bounded QA Excerpts**: Course QA reviews bounded excerpts (`FINAL_QA_EXCERPT_CHARS`) to fit context limits; it does not read every word of every chapter.
* **LaTeX Edge Cases**: Complex tables or mathematical formulations may occasionally require manual inspection of `.tex` source files.
* **Terminology Registry**: Enforces consistent naming across chapters, but does not guarantee semantic validity of definitions.

---

## Contributing and License

Contributions are welcome! Please review [CONTRIBUTING.md](CONTRIBUTING.md) for development workflows and check [CHANGELOG.md](CHANGELOG.md) for recent release notes.

Pseudo-Scribe is released under the [Apache-2.0 License](LICENSE).

