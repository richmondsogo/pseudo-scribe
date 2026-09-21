# Pseudo-Scribe — University Lecture Note Generator

A command-line tool that generates a complete, professionally typeset PDF of university lecture notes from a course outline. Terminology stays consistent across the whole course, chapters are written directly as LaTeX, and the output is a single merged book with a title page, a real table of contents, and correct page numbers.

This is a **V1 reference implementation** — deliberately simple, resumable, and cost-aware. It uses an LLM (Gemini, Groq, OpenRouter, or DeepSeek) for content generation and `pdflatex` for typesetting.

---

## Features

* **Interactive wizard** — paste or point at a course outline, answer a few questions, and get a finished PDF end-to-end.
* **Resumable** — interrupted runs pick up from the saved generation state instead of starting over.
* **Cost-aware** — chapters are written directly as LaTeX, final QA uses bounded per-chapter excerpts, and optional LLM passes are disabled by default.
* **Terminology consistency** — a course-wide `terminology.json` registry acts as the single source of truth; later chapters must reuse existing definitions or add new ones.
* **Real book output** — title page, page-numbered table of contents, PDF bookmarks, and a merged single-file PDF built from per-chapter LaTeX.
* **Provider-agnostic** — works with Gemini, Groq, OpenRouter, or DeepSeek. Token-heavy calls can be routed to a different provider.
* **Failure recovery** — transient API failures are retried automatically, while completed work is preserved for later runs.
* **Generation safeguards** — malformed model responses, refusals, LaTeX errors, and terminology conflicts are detected rather than silently written into the final book.

---

## Quick start

### 1. Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate
pip install -r requirements.txt
```

Requirements:

* **Python 3.11+**
* A LaTeX distribution with `pdflatex` available on your `PATH`

  * [MiKTeX](https://miktex.org/) on Windows
  * [TeX Live](https://www.tug.org/texlive/) on macOS/Linux

### 2. Configure an LLM provider

Copy `.env.example` to `.env`:

```powershell
Copy-Item .env.example .env
```

Then add the API key for the provider you want to use.

| Provider   | `LLM_PROVIDER` | Required key                         |
| ---------- | -------------- | ------------------------------------ |
| Gemini     | `gemini`       | `GEMINI_API_KEY` or `GOOGLE_API_KEY` |
| Groq       | `groq`         | `GROQ_API_KEY`                       |
| OpenRouter | `openrouter`   | `OPENROUTER_API_KEY`                 |
| DeepSeek   | `deepseek`     | `DEEPSEEK_API_KEY`                   |

Gemini is the default provider.

See [Configuration](#configuration) for model selection, retries, concurrency, QA, and other options.

### 3. Run the generator

```powershell
python generate.py
```

The interactive wizard collects course metadata and a course outline. The outline can be pasted directly or loaded from a `.md`, `.txt`, `.docx`, or `.pdf` file.

The pipeline then runs:

```text
Outline
   ↓
Terminology extraction
   ↓
Chapter generation
   ↓
Chapter QA / optional enhancement
   ↓
Final course QA
   ↓
LaTeX compilation
   ↓
Merged PDF
```

If a previous run exists, the generator can resume from its saved state.

> **Source materials are optional.** The generator can work from the course outline and learning outcomes alone. If you have lecture slides, readings, or other material you want the model to use, provide a path when prompted.

---

## Command-line reference

| Command                                      | What it does                                |
| -------------------------------------------- | ------------------------------------------- |
| `python generate.py`                         | Interactive wizard and full pipeline        |
| `python generate.py init <COURSE_ID>`        | Create a new course folder skeleton         |
| `python generate.py profile <COURSE_ID>`     | Create or edit `course_profile.md`          |
| `python generate.py terminology <COURSE_ID>` | Run terminology extraction from the outline |
| `python generate.py chapter <COURSE_ID> <N>` | Generate a single chapter                   |
| `python generate.py all <COURSE_ID>`         | Generate all missing chapters               |
| `python generate.py qa <COURSE_ID>`          | Run final course-wide QA                    |
| `python generate.py compile <COURSE_ID>`     | Typeset and compile the final PDF           |
| `python generate.py build <COURSE_ID>`       | Run the complete pipeline                   |

### Example manual workflow

```powershell
python generate.py init INS202_HCI

# Edit:
# courses/INS202_HCI/course_profile.md
# courses/INS202_HCI/outline.json

python generate.py terminology INS202_HCI
python generate.py all INS202_HCI
python generate.py qa INS202_HCI
python generate.py compile INS202_HCI
```

The `build` command can be used when you want to run the complete pipeline in one step:

```powershell
python generate.py build INS202_HCI
```

---

## Project layout

Each course lives in its own folder under `courses/`:

```text
courses/<COURSE_ID>/
├── course_profile.md        # Course metadata + style rules
├── outline.json             # Parsed course outline / chapter list
├── terminology.json         # Canonical term registry
├── generation_state.json    # Progress tracking for resumable runs
├── prompts/                 # Prompt templates copied at initialization
├── sources/                 # Optional supplied source materials
├── pasted_outline.md        # Raw pasted outline, when provided
├── drafts/                  # Intermediate/legacy chapter drafts
├── tex/                     # Chapter .tex/.pdf files and compile logs
├── final/                   # Final assembled material and QA report
├── output.pdf               # Final merged book
└── run_warnings.json        # Warnings collected during the last run
```

The most important state files are:

* `outline.json` — what the course is supposed to contain.
* `terminology.json` — the canonical terminology used across the course.
* `generation_state.json` — which parts of the pipeline have completed.
* `final/final_qa.json` — the final QA report.
* `output.pdf` — the generated book.

---

## Configuration

All configuration is controlled through environment variables. Copy `.env.example` to `.env` and change only what you need.

### Provider selection

```env
LLM_PROVIDER=gemini
```

Supported values:

```text
gemini
groq
openrouter
deepseek
```

### Gemini

```env
GEMINI_API_KEY=
GEMINI_MODEL=gemini-3.6-flash
```

`GEMINI_API_KEY` is preferred, but `GOOGLE_API_KEY` is also accepted.

`LLM_MODEL` can also be used as a general model override, but `GEMINI_MODEL` takes precedence for Gemini.

### Groq

```env
GROQ_API_KEY=
GROQ_MODEL=llama-3.3-70b-versatile
```

### OpenRouter

```env
OPENROUTER_API_KEY=
OPENROUTER_MODEL=openrouter/free
```

`openrouter/free` automatically routes to an available free model. Set `OPENROUTER_MODEL` if you want to pin a specific model.

OpenRouter attribution can optionally be customized:

```env
OPENROUTER_SITE_URL=https://github.com/richmondsogo/Pseudo-Scribe
OPENROUTER_SITE_NAME=Pseudo-Scribe
```

### DeepSeek

```env
DEEPSEEK_API_KEY=
DEEPSEEK_MODEL=deepseek-chat
```

Use `deepseek-reasoner` when you want DeepSeek's reasoning model instead.

### Generation settings

```env
LLM_TEMPERATURE=0.2
```

Controls sampling temperature for generation.

Lower values generally produce more deterministic output.

### Heavy-call routing

```env
HEAVY_CALL_PROVIDER=
```

Some operations use significantly more tokens than normal chapter generation, particularly final QA and legacy markdown-to-LaTeX typesetting.

You can route these calls through another provider:

```env
HEAVY_CALL_PROVIDER=openrouter
```

If left empty, they use the normal `LLM_PROVIDER`.

### Chapter retries

```env
MAX_CHAPTER_RETRIES=2
```

Controls how many times the system retries a chapter after a failed or invalid generation.

This is separate from API-level retries.

### API retries

```env
MAX_API_RETRIES=5
API_RETRY_BASE_DELAY=2.0
MAX_AUTO_RETRY_WAIT_SECONDS=90
```

These control retries for transient provider failures such as:

* rate limits
* HTTP 429 errors
* server errors
* timeouts
* temporary connection failures

If a provider tells Pseudo-Scribe to wait longer than `MAX_AUTO_RETRY_WAIT_SECONDS`, the run fails rather than sitting idle for a long period.

Completed work is preserved, so the same command can be run again later.

### Final QA

```env
FINAL_QA_EXCERPT_CHARS=1000
```

Final QA does not send the entire generated book to the model. Instead, a bounded excerpt from each chapter is used to keep the QA call predictable in size.

The final QA checks for things such as:

* course coverage
* terminology consistency
* cross-chapter consistency

The QA report is saved to:

```text
courses/<COURSE_ID>/final/final_qa.json
```

> **Important:** QA is an automated check, not a guarantee of factual correctness. Generated academic material should always be reviewed by a human.

### Chapter enhancement

```env
ENABLE_CHAPTER_ENHANCE=0
```

When enabled:

```env
ENABLE_CHAPTER_ENHANCE=1
```

Pseudo-Scribe runs a second LLM pass on each chapter to improve the draft and add diagrams where appropriate.

This **adds another LLM call per chapter**, so it is disabled by default.

### Terminology conflict checking

```env
ENABLE_CONFLICT_CHECK=0
```

By default, when a chapter proposes a different definition for an existing term, Pseudo-Scribe keeps the original definition and records a warning.

When enabled:

```env
ENABLE_CONFLICT_CHECK=1
```

an additional LLM call checks whether the new definition represents a genuine conflict.

This improves conflict analysis but increases LLM usage.

### Chapter concurrency

```env
MAX_CONCURRENT_CHAPTERS=1
```

Controls how many chapters can be generated concurrently.

The default is deliberately `1`.

Increasing concurrency can reduce wall-clock time on providers with generous limits, but it does **not** reduce the total number of API calls. It can also cause several requests to hit a provider's per-minute limits at the same time.

For free or rate-limited providers, keeping this at `1` is recommended.

### HTTP timeout

```env
HTTP_TIMEOUT_SECONDS=180
```

Controls the request timeout for compatible chat-completions providers such as Groq, OpenRouter, and DeepSeek.

### Dry run

```env
DRY_RUN=1
```

Runs the pipeline using canned responses instead of making real LLM calls.

This is useful for checking the local pipeline, file handling, and LaTeX compilation without consuming API quota.

---

## Recommended `.env`

For a basic Gemini setup:

```env
LLM_PROVIDER=gemini

GEMINI_API_KEY=
GEMINI_MODEL=gemini-3.6-flash

LLM_TEMPERATURE=0.2
MAX_CHAPTER_RETRIES=2
MAX_CONCURRENT_CHAPTERS=1

ENABLE_CHAPTER_ENHANCE=0
ENABLE_CONFLICT_CHECK=0
```

For most users, the remaining settings can be left at their defaults.

---

## Token and quota considerations

Pseudo-Scribe is designed to be usable with relatively limited API quotas.

The most important settings are:

```env
MAX_CONCURRENT_CHAPTERS=1
ENABLE_CHAPTER_ENHANCE=0
ENABLE_CONFLICT_CHECK=0
```

Increasing concurrency does not reduce total API usage. It simply makes requests happen closer together.

Likewise, enabling chapter enhancement adds another LLM pass for every chapter.

If your primary provider has a low token-per-minute limit, consider using:

```env
HEAVY_CALL_PROVIDER=openrouter
```

or another provider with a suitable context/token budget for the larger operations.

Provider quotas and model availability change over time, so check the provider's current limits before relying on specific free-tier numbers.

---

## How it works

### 1. Terminology extraction

The course outline and optional source materials are analyzed to seed:

```text
terminology.json
```

This becomes the course-wide terminology registry.

### 2. Chapter generation

Each chapter is generated directly as a LaTeX body fragment.

The model does **not** generate the complete LaTeX document. Programmatic scaffolding handles the document class, packages, formatting, page structure, and other book-level requirements.

New terminology is returned in a `[TERMINOLOGY]` JSON block and merged into the course registry before subsequent chapters are generated.

This means later chapters can reuse established terminology instead of independently redefining the same concepts.

### 3. Terminology conflict handling

If a chapter proposes a new definition for an existing term, the original definition is never silently overwritten.

The system:

1. Keeps the original definition.
2. Records a warning in `run_warnings.json`.
3. Optionally uses another LLM call to determine whether the difference is a genuine conflict when `ENABLE_CONFLICT_CHECK=1`.

### 4. Final QA

A bounded excerpt from each chapter is sent through the final course-wide QA process.

The resulting report is saved as:

```text
final/final_qa.json
```

QA issues are recorded as warnings rather than automatically stopping the entire build.

### 5. Compilation

Each chapter is compiled with `pdflatex`.

The title page and table of contents are generated separately, and chapters are compiled again when necessary so that their actual starting page numbers can be reflected correctly in the table of contents.

Finally, the generated PDFs are merged with `pypdf` into one book.

The final PDF includes:

* title page
* table of contents
* page numbers
* PDF bookmarks
* all generated chapters

---

## Why chapters sometimes look wrong

LLM-generated LaTeX has several failure modes. Pseudo-Scribe includes safeguards for some of the most common ones.

### Unescaped `%`

In LaTeX, `%` starts a comment.

For example:

```text
90%
```

can cause the remainder of the line to be ignored.

Generated content is sanitized so that bare percentage signs are escaped where appropriate.

### Refused or one-line responses

If the model returns a refusal or an obviously invalid one-line response instead of a chapter, the response is treated as a failed generation rather than being saved as the chapter.

The system then retries according to `MAX_CHAPTER_RETRIES`.

### Malformed chapter delimiters

The model may occasionally produce malformed `[CHAPTER]` or `[TERMINOLOGY]` delimiters.

Pseudo-Scribe detects and handles known malformed responses and can self-heal certain older drafts during compilation.

### Learning outcomes mistaken for chapters

Course outlines frequently contain sections such as:

```text
Learning Outcomes
```

or learning-outcome statements that look like chapter titles.

The outline parser attempts to distinguish these from actual chapters and clean inappropriate titles.

### LaTeX compilation errors

`pdflatex` can sometimes exit successfully even when the resulting document contains real errors, particularly when running in nonstop mode.

Pseudo-Scribe therefore scans compilation logs for actual LaTeX errors instead of relying only on the process exit code.

---

## Terminology & conflict handling

`terminology.json` is the course-wide source of truth.

Chapter writers must declare new terms in their output using a `[TERMINOLOGY]` JSON block.

A redefined term never silently replaces the original:

* the first definition is preserved;
* a warning is recorded in `run_warnings.json`;
* `ENABLE_CONFLICT_CHECK=1` can trigger an additional LLM check to determine whether the definitions genuinely conflict.

This is intentionally conservative. Consistency is preferred over silently changing the meaning of a technical term halfway through a course.

---

## Troubleshooting

| Symptom                                        | Fix                                                                                                                                                                                         |
| ---------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `'pdflatex' isn't installed or isn't on PATH`  | Install MiKTeX or TeX Live and ensure `pdflatex` is available on your `PATH`.                                                                                                               |
| **LaTeX compilation error**                    | Check the relevant error log in `tex/`. The generated `.tex` source is retained for inspection.                                                                                             |
| **Chapter content is a refusal or one-liner**  | The pipeline retries automatically. If it persists, check the model/provider and try `python generate.py chapter <COURSE_ID> <N>`.                                                          |
| **Chapter titles look like learning outcomes** | Edit `outline.json` or rerun the wizard using the raw course outline.                                                                                                                       |
| **JSON parse failure from the LLM**            | Raw responses are preserved next to the relevant output for inspection.                                                                                                                     |
| **API failures / rate limits**                 | Check the API key and network connection. Transient errors are retried automatically. If the provider requires a long wait, completed work is preserved and the command can be rerun later. |
| **`pypdf` is not installed**                   | Run `pip install -r requirements.txt`.                                                                                                                                                      |
| **PDF output looks incomplete**                | Check `run_warnings.json`, the chapter `.tex` files, and the relevant LaTeX compile logs.                                                                                                   |
| **Generation stopped halfway through**         | Run the same command again. The saved generation state allows the pipeline to continue from completed work.                                                                                 |

---

## Extending the tool

### Add an LLM provider

Implement a provider-specific `_call_<name>` function and register it in `call_llm()` in `generate.py`.

### Change the writing style

Edit:

```text
prompts/chapter_writer.txt
```

The prompt template is copied into each course during initialization, so existing courses retain their own prompt configuration.

### Change the output formatting

The shared LaTeX scaffold is defined in `_shared_preamble()` in `generate.py`.

This controls the document-level formatting such as fonts, margins, and packages.

---

## Limitations

Pseudo-Scribe is a **V1 reference implementation**, not a replacement for academic review.

Important limitations include:

* LLM-generated content can contain factual errors or omissions.
* Final QA samples bounded chapter excerpts rather than exhaustively verifying every sentence.
* Terminology consistency does not guarantee conceptual correctness.
* Provider quotas and model behavior can change.
* LaTeX generation can still produce edge cases that require manual inspection.
* Source materials improve grounding but do not guarantee that the generated notes faithfully represent them.

Always review generated academic material before using it for teaching, assessment, publication, or other consequential purposes.

---

## Safety & License

This tool uses an LLM to generate academic material.

**Always review generated content before use in teaching or assessment.**

See [`LICENSE`](LICENSE) for license details.

Pseudo-Scribe is released under the **Apache-2.0 license**.

---

## Contributing

Contributions are welcome.

Open an issue or pull request with:

* a clear description of the change;
* the motivation behind it;
* relevant reproduction steps or examples where applicable.

Pseudo-Scribe is currently a V1 reference implementation intended as a starting point for research, experimentation, and further development.
