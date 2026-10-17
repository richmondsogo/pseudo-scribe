# Contributing to Pseudo-Scribe

Thank you for contributing to Pseudo-Scribe! This guide outlines how to set up your development environment, run tests, adhere to code style guidelines, and extend the system (such as adding a new LLM provider).

---

## 1. Development Setup

1. **Clone the repository**:
   ```bash
   git clone https://github.com/richmondsogo/Pseudo-Scribe.git
   cd Pseudo-Scribe
   ```

2. **Create and activate a virtual environment** (Python 3.11+ required):
   ```bash
   python -m venv .venv
   # Windows:
   .venv\Scripts\activate
   # macOS/Linux:
   source .venv/bin/activate
   ```

3. **Install dependencies**:
   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt -r requirements-dev.txt
   ```

4. **Set up local environment configuration**:
   ```bash
   cp .env.example .env
   ```
   Add your API keys to `.env` as needed. For offline development and testing, you can set `DRY_RUN=1`.

---

## 2. Running Tests & Linters

Pseudo-Scribe uses `pytest` for testing and `ruff` for linting and formatting.

### Running Pytest
Run the full test suite with:
```bash
pytest -v
```
All tests run with mocked/offline inputs under `DRY_RUN=1`, requiring no external API keys or pdflatex installation.

### Linting & Formatting with Ruff
Check code style:
```bash
ruff check .
```
Automatically fix linting issues:
```bash
ruff check --fix .
```
Verify formatting:
```bash
ruff format --check .
```
Format files:
```bash
ruff format .
```

---

## 3. Code Style & Architecture Guidelines

- **Python Version**: Maintain Python 3.11+ compatibility.
- **Path Handling**: Use `pathlib.Path` for all filesystem operations; avoid hardcoded path separators (`/` or `\\`).
- **File I/O**: Always specify `encoding="utf-8"` when reading or writing text files. Use atomic writes (write `.tmp` then `replace`) for state files.
- **Typing**: Provide type annotations for all function parameters and return types.
- **Entry Point Compatibility**: All CLI functionality remains accessible via `python generate.py` as well as the modular `pseudoscribe` package.

---

## 4. How to Add a New LLM Provider

Pseudo-Scribe's provider integration is located in `pseudoscribe/providers.py`. To add a new provider:

1. **Add Provider Logic in `pseudoscribe/providers.py`**:
   - For OpenAI-compatible endpoints, use the existing `_call_compatible_chat_completions` helper:
     ```python
     def _call_myprovider(prompt: str, model: Optional[str], temperature: float) -> str:
         api_key = os.getenv("MYPROVIDER_API_KEY")
         if not api_key:
             raise RuntimeError("MYPROVIDER_API_KEY must be set to use myprovider")
         model_name = model or os.getenv("MYPROVIDER_MODEL") or "default-model"
         return _call_compatible_chat_completions(
             "MyProvider",
             "https://api.myprovider.com/v1/chat/completions",
             api_key,
             model_name,
             prompt,
             temperature,
         )
     ```
   - For proprietary SDKs, implement retry handling with `call_with_retry(...)`.

2. **Update `call_llm` Dispatch**:
   Add a branch in `call_llm` in `pseudoscribe/providers.py`:
   ```python
   elif provider == "myprovider":
       return _call_myprovider(prompt, model, temperature)
   ```

3. **Update Configuration & Documentation**:
   - Add default model and environment keys in `pseudoscribe/config.py`.
   - Document new environment variables in `.env.example`, `README.md`, and `docs/configuration.md`.
   - Record architectural choices in `docs/DECISIONS.md`.

---

## 5. Submitting Changes

1. Create a feature branch: `git checkout -b feature/your-feature-name`
2. Ensure all tests pass (`pytest`) and code is clean (`ruff check .`, `ruff format --check .`).
3. Commit using [Conventional Commits](https://www.conventionalcommits.org/) (e.g. `feat: add mistral provider`, `fix: handle edge case in outline parsing`).
4. Submit a Pull Request describing your changes, test results, and any relevant decisions.
