# Configuration Reference

Pseudo-Scribe is configured through environment variables loaded from a `.env` file in the project root (using `python-dotenv`) or from the host environment.

Copy `.env.example` to `.env` to get started:

```bash
# Windows (PowerShell)
Copy-Item .env.example .env

# macOS / Linux (bash)
cp .env.example .env
```

---

## Providers and Models

Pseudo-Scribe supports four LLM providers. Configure the active provider with `LLM_PROVIDER`.

### Gemini (Default)

Uses the official `google-genai` SDK.

```env
LLM_PROVIDER=gemini
GEMINI_API_KEY=your_gemini_api_key
GEMINI_MODEL=gemini-3.6-flash
```

* `GOOGLE_API_KEY` is accepted as an alternative to `GEMINI_API_KEY`.
* `google` and `googleai` are accepted as aliases for `LLM_PROVIDER`.
* If `GEMINI_MODEL` is unset, `LLM_MODEL` is used as a fallback (defaults to `gemini-3.6-flash`).

### Groq

Uses the OpenAI-compatible endpoint at `https://api.groq.com/openai/v1/chat/completions`. Prefers the `requests` library and falls back to `urllib`.

```env
LLM_PROVIDER=groq
GROQ_API_KEY=your_groq_api_key
GROQ_MODEL=llama-3.3-70b-versatile
```

### OpenRouter

Uses the OpenAI-compatible endpoint at `https://openrouter.ai/api/v1/chat/completions`.

```env
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=your_openrouter_api_key
OPENROUTER_MODEL=openrouter/free
```

* `openrouter/free` automatically routes to a currently available free model.
* Optional ranking and attribution headers:
  * `OPENROUTER_SITE_URL` (default: `https://github.com/richmondsogo/Pseudo-Scribe`)
  * `OPENROUTER_SITE_NAME` (default: `Pseudo-Scribe`)

### DeepSeek

Uses the OpenAI-compatible endpoint at `https://api.deepseek.com/chat/completions`.

```env
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=your_deepseek_api_key
DEEPSEEK_MODEL=deepseek-chat
```

* Set `DEEPSEEK_MODEL=deepseek-reasoner` to use DeepSeek's reasoning model.

---

## Heavy-Call Routing

Some operations consume more context than standard chapter generation:
* Final course QA (`cmd_qa`)
* Fallback typesetting for older markdown drafts (`get_chapter_body_fragment`)

You can route these calls to a provider with larger context windows or higher rate limits:

```env
HEAVY_CALL_PROVIDER=openrouter
```

If left empty, heavy calls use `LLM_PROVIDER`.

---

## Environment Variable Reference

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `LLM_PROVIDER` | string | `gemini` | Primary LLM provider (`gemini`, `groq`, `openrouter`, `deepseek`). |
| `HEAVY_CALL_PROVIDER` | string | *empty* | Provider override for final QA and typesetter fallback. Defaults to `LLM_PROVIDER`. |
| `LLM_MODEL` | string | `gemini-3.6-flash` | General fallback model identifier. |
| `GEMINI_MODEL` | string | `gemini-3.6-flash` | Model for the Gemini provider. Takes precedence over `LLM_MODEL`. |
| `GROQ_MODEL` | string | `llama-3.3-70b-versatile` | Model for the Groq provider. |
| `OPENROUTER_MODEL` | string | `openrouter/free` | Model for OpenRouter. |
| `DEEPSEEK_MODEL` | string | `deepseek-chat` | Model for DeepSeek. |
| `GEMINI_API_KEY` | string | *empty* | API key for Gemini. Also checks `GOOGLE_API_KEY`. |
| `GROQ_API_KEY` | string | *empty* | API key for Groq. |
| `OPENROUTER_API_KEY` | string | *empty* | API key for OpenRouter. |
| `DEEPSEEK_API_KEY` | string | *empty* | API key for DeepSeek. |
| `LLM_TEMPERATURE` | float | `0.2` | Sampling temperature for text generation. |
| `HTTP_TIMEOUT_SECONDS` | int/float | `180` | Request timeout for HTTP chat completions (Groq, OpenRouter, DeepSeek). |
| `OPENROUTER_SITE_URL` | string | `https://github.com/richmondsogo/Pseudo-Scribe` | Attribution URL sent in `HTTP-Referer` header to OpenRouter. |
| `OPENROUTER_SITE_NAME` | string | `Pseudo-Scribe` | Attribution name sent in `X-Title` header to OpenRouter. |
| `MAX_CONCURRENT_CHAPTERS` | int | `1` | Max threads for parallel chapter generation. Keep at 1 for rate-limited providers. |
| `MAX_CHAPTER_RETRIES` | int | `2` | Number of times to retry a chapter on empty output, refusal, or non-LaTeX body. |
| `MAX_API_RETRIES` | int | `5` | Retries for transient API failures (5xx, 429, timeouts, connection resets). |
| `API_RETRY_BASE_DELAY` | float | `2.0` | Base delay in seconds for exponential backoff with jitter. |
| `MAX_AUTO_RETRY_WAIT_SECONDS` | float | `90` | If provider requests a retry wait greater than this, fail fast instead of hanging. |
| `FINAL_QA_EXCERPT_CHARS` | int | `1000` | Character limit excerpted from each chapter for final QA to cap prompt size. |
| `ENABLE_CHAPTER_ENHANCE` | boolean | `0` | Run a second LLM pass per chapter for ASCII diagrams and polish. Alias: `ENABLE_CHAPTER_QA`. |
| `ENABLE_CONFLICT_CHECK` | boolean | `0` | Run an LLM check when terms are redefined. Original definitions are always kept. |
| `DRY_RUN` | boolean | `0` | Run pipeline with mock responses and dummy PDFs without making LLM or LaTeX calls. |

---

## Concurrency and Quota Strategy

Free tiers and rate-limited accounts require conservative settings:

```env
MAX_CONCURRENT_CHAPTERS=1
ENABLE_CHAPTER_ENHANCE=0
ENABLE_CONFLICT_CHECK=0
```

* **Concurrency**: Increasing `MAX_CONCURRENT_CHAPTERS` speeds up wall-clock time on paid tiers, but does not decrease total API calls and can trigger per-minute limits (TPM/RPM).
* **Chapter Enhancement**: Setting `ENABLE_CHAPTER_ENHANCE=1` adds an additional LLM call per chapter.
* **Transient Retries**: When providers return rate limits with a suggested wait time (`retryDelay`, `Retry-After`, or error text), Pseudo-Scribe pauses for that exact duration. If the wait exceeds `MAX_AUTO_RETRY_WAIT_SECONDS` (90 seconds), it halts immediately to avoid hanging. Progress is preserved in `generation_state.json`.
