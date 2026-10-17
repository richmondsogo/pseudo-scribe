"""
generate.py - V1 CLI for course lecture note generation

This CLI is an AI-driven pipeline that produces consistent, resumable, multi-chapter
lecture notes and compiles them to a single PDF using LaTeX.

Design goals and behavior are described in the project README.

Usage: python generate.py <command> <course_id> [chapter_number]

Commands:
  init       Create a new course folder skeleton
  profile    Create or edit course_profile.md (writes a template if missing)
  terminology Run initial terminology extraction
  chapter    Generate a single chapter (requires chapter number)
  all        Generate all missing chapters sequentially
  qa         Run final course QA
  compile    Typeset and compile final PDF
  build      Convenience: run terminology, missing chapters, final QA, typeset and compile

This file intentionally keeps a simple structure compatible with Python 3.11+.
"""

from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
import shutil
import textwrap
import time
import random
import threading
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, List

# Load .env if present so environment variables in .env are available at runtime
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    # If python-dotenv is not installed, continue — environment may be set externally
    pass

# Environment-driven configuration
# Default provider set to Gemini (Google) for this deployment
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini").lower()
# Default model name is a Gemini text model. Override with GEMINI_MODEL or LLM_MODEL.
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-3.6-flash")
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.2"))
MAX_CHAPTER_RETRIES = int(os.getenv("MAX_CHAPTER_RETRIES", "2"))
# Retries for transient API failures (500/503/429/timeouts) on a single call_llm() call,
# separate from MAX_CHAPTER_RETRIES which re-runs a whole chapter after a QA failure.
MAX_API_RETRIES = int(os.getenv("MAX_API_RETRIES", "5"))
API_RETRY_BASE_DELAY = float(os.getenv("API_RETRY_BASE_DELAY", "2.0"))
# If a provider tells us to wait longer than this to retry (e.g. a daily-quota reset that's
# 28 minutes away), don't block the run waiting it out automatically — fail that call fast
# with a clear "try again after this time" message instead. Short waits (a real per-minute
# burst limit) are still handled automatically.
MAX_AUTO_RETRY_WAIT_SECONDS = float(os.getenv("MAX_AUTO_RETRY_WAIT_SECONDS", "90"))
# Cap on how much of each chapter is sent to the final course-wide QA pass. Keeps that single
# call's size predictable regardless of how many/how long the chapters are, instead of scaling
# with the whole course (which is what blew past a 6000 TPM free-tier limit in practice).
FINAL_QA_EXCERPT_CHARS = int(os.getenv("FINAL_QA_EXCERPT_CHARS", "1000"))
# Optional: route the two token-heavy calls (final QA, per-chapter typesetting) to a different
# provider than everything else. Useful because these two calls are infrequent but large
# (won't fit a small free-tier TPM budget), while chapter writing/QA calls are frequent but
# small — a low-TPM/high-RPD provider suits the latter, a high-context provider suits the
# former, and you may want different providers for each. Falls back to LLM_PROVIDER if unset.
HEAVY_CALL_PROVIDER = os.getenv("HEAVY_CALL_PROVIDER")
# Both default OFF to minimize LLM calls per chapter. Chapters are now written directly as
# LaTeX (see prompts/chapter_writer.txt), which already removes the separate typesetting call;
# ENABLE_CHAPTER_ENHANCE and ENABLE_CONFLICT_CHECK remove two more optional LLM calls per
# chapter. Conflicts are still detected and logged when checking is off — they're just resolved
# by always keeping the first definition, without spending a call asking the model whether it's
# a "real" conflict. Turn either back on if you have quota to spare.
# ENABLE_CHAPTER_ENHANCE runs a second pass per chapter (prompts/chapter_enhancer.txt) that
# actively improves the draft and adds diagrams where they'd help, rather than just validating
# it — genuinely improves output quality, at the cost of one extra call per chapter. Off by
# default for the same reason: it doubles the chapter-generation call count. ENABLE_CHAPTER_QA
# is accepted as an alias so an existing .env with the old name still works.
ENABLE_CHAPTER_ENHANCE = (
    os.getenv("ENABLE_CHAPTER_ENHANCE", os.getenv("ENABLE_CHAPTER_QA", "0")).lower() in ("1", "true", "yes")
)
ENABLE_CONFLICT_CHECK = os.getenv("ENABLE_CONFLICT_CHECK", "0").lower() in ("1", "true", "yes")
# Chapters can generate concurrently via a thread pool. Defaults to 1 (sequential) deliberately:
# concurrency doesn't reduce the *total* number of API calls a course needs (that's what actually
# trips daily-request quotas), and it makes several chapters' calls land in the same short window,
# which is exactly what trips per-minute (TPM/RPM) quotas. Raise this only if you have quota to
# spare (a paid tier, or a generous provider) and want faster wall-clock time.
try:
    MAX_CONCURRENT_CHAPTERS = max(1, int(os.getenv("MAX_CONCURRENT_CHAPTERS", "1")))
except ValueError:
    MAX_CONCURRENT_CHAPTERS = 1
# Protects generation_state.json read-modify-write cycles when chapters run concurrently.
STATE_LOCK = threading.Lock()
# Thread-safe count of LLM calls made this run, surfaced in the run summary.
LLM_CALL_COUNT = 0
_LLM_CALL_COUNT_LOCK = threading.Lock()

# Repository root assumed to be current working directory when running
REPO_ROOT = Path(__file__).resolve().parent
COURSES_DIR = REPO_ROOT / "courses"
PROMPTS_DIR = REPO_ROOT / "prompts"

# Safe console output for cross-platform terminals
def _safe_print(msg: str, file: Any = None) -> None:
    target = file if file is not None else sys.stdout
    try:
        print(msg, file=target)
    except UnicodeEncodeError:
        encoding = getattr(target, "encoding", "ascii") or "ascii"
        print(msg.encode(encoding, errors="replace").decode(encoding), file=target)

# Simple logging
def info(msg: str) -> None:
    _safe_print(msg)

def error(msg: str) -> None:
    _safe_print(f"ERROR: {msg}", file=sys.stderr)

# --- Run-wide warnings ---
# Collected instead of hard-stopping the pipeline. Printed as a summary at the end
# of a hands-off run (interactive_setup / cmd_build) and saved to run_warnings.json
# in the course folder so nothing is silently lost.
RUN_WARNINGS: List[str] = []

def warn(msg: str) -> None:
    RUN_WARNINGS.append(msg)
    _safe_print(f"WARNING: {msg}", file=sys.stderr)

def save_run_warnings(c: "Course") -> None:
    if RUN_WARNINGS:
        (c.path / "run_warnings.json").write_text(
            json.dumps(RUN_WARNINGS, indent=2, ensure_ascii=False), encoding="utf-8"
        )

# --- Retry helper for transient API errors ---

def _is_transient_error(exc: Exception) -> bool:
    """Heuristic: retry on server-side / rate-limit / connectivity errors, not on
    things like bad API keys or malformed requests, which will never succeed on retry."""
    msg = str(exc).lower()
    transient_markers = (
        "500", "internal", "502", "503", "504", "unavailable",
        "429", "rate limit", "resource_exhausted", "quota",
        "timeout", "timed out", "deadline", "connection", "reset",
    )
    return any(marker in msg for marker in transient_markers)

def _parse_retry_after_seconds(error_text: str) -> Optional[float]:
    """Best-effort extraction of a server-suggested wait time from an error message, so
    retries wait exactly as long as the provider says instead of guessing. Handles Gemini's
    structured "'retryDelay': '10s'" fragment, Groq's "please try again in 28m20.35s" (or
    "in 5s") phrasing, and a bare "Retry-After header: 30s" (added by _call_compatible_chat_completions
    when a provider sets that HTTP header). Returns None if nothing recognizable is found."""
    text = error_text or ""
    m = re.search(r"retryDelay['\"]?\s*:\s*['\"](\d+(?:\.\d+)?)s['\"]", text)
    if m:
        return float(m.group(1))
    m = re.search(r"Retry-After header:\s*(\d+(?:\.\d+)?)s", text, re.IGNORECASE)
    if m:
        return float(m.group(1))
    m = re.search(r"try again in\s+(?:(\d+)h)?(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s)?", text, re.IGNORECASE)
    if m and any(m.groups()):
        total = float(m.group(1) or 0) * 3600 + float(m.group(2) or 0) * 60 + float(m.group(3) or 0)
        if total > 0:
            return total
    return None

def call_with_retry(fn, max_retries: int = MAX_API_RETRIES, base_delay: float = API_RETRY_BASE_DELAY):
    """Call fn() with backoff on transient errors. When the provider tells us exactly how long
    to wait (daily/per-minute quota messages usually do), that's used instead of guessing —
    and if that wait is longer than MAX_AUTO_RETRY_WAIT_SECONDS, this fails fast with a clear
    "try again after this time" message rather than blocking the run for many minutes with no
    visible progress. Re-raises immediately on non-transient errors, and re-raises the last
    error once retries are exhausted."""
    attempt = 0
    while True:
        try:
            return fn()
        except Exception as e:
            attempt += 1
            if attempt > max_retries or not _is_transient_error(e):
                raise
            suggested = _parse_retry_after_seconds(str(e))
            if suggested is not None:
                if suggested > MAX_AUTO_RETRY_WAIT_SECONDS:
                    ready_at = time.strftime("%H:%M:%S", time.localtime(time.time() + suggested))
                    raise RuntimeError(
                        f"{e} | The provider says to retry in ~{suggested/60:.1f} min (around "
                        f"{ready_at}), longer than this run will wait automatically "
                        f"(MAX_AUTO_RETRY_WAIT_SECONDS={MAX_AUTO_RETRY_WAIT_SECONDS:.0f}s). "
                        f"Completed work is saved — just re-run the same command after that "
                        f"time and it'll pick up where it left off."
                    ) from e
                delay = suggested + random.uniform(0, 1)
            else:
                delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 1)
            info(f"  Transient API error ({e}). Retrying in {delay:.1f}s... (attempt {attempt}/{max_retries})")
            time.sleep(delay)

# --- LLM wrapper ---

def call_llm(prompt: str, model: Optional[str] = None, temperature: Optional[float] = None, max_tokens: Optional[int] = None, provider: Optional[str] = None) -> str:
    """Call the configured LLM provider (LLM_PROVIDER env var).
    Pass provider= to override LLM_PROVIDER for just this call (see HEAVY_CALL_PROVIDER).

    This function supports a DRY_RUN mode via the environment variable DRY_RUN=1|true which returns canned responses
    for a quick smoke test without making external API calls.
    """
    provider = (provider or LLM_PROVIDER).lower()
    global LLM_CALL_COUNT
    with _LLM_CALL_COUNT_LOCK:
        LLM_CALL_COUNT += 1
    # Deliberately NOT applying a generic model fallback here — each provider has its own
    # default model name, and leaking one provider's default into another would silently
    # send the wrong model name (e.g. a Gemini model string to the Groq API).
    temperature = temperature if temperature is not None else LLM_TEMPERATURE

    dry = os.getenv("DRY_RUN", "").lower() in ("1", "true", "yes")
    if dry:
        low = (prompt or "").lower()
        # Terminology conflict checker canned response
        if "terminology conflict checker" in low or "decide whether the two definitions" in low:
            return json.dumps({"conflict": False, "reasoning": "Dry-run: assume no conflict."})
        # Outline chapter extractor canned response
        if "outline chapter extractor" in low:
            return json.dumps({
                "chapters": [
                    {"number": 1, "title": "Dry-run Chapter One", "topic": "sample topic A, sample topic B"},
                    {"number": 2, "title": "Dry-run Chapter Two", "topic": "sample topic C"},
                    {"number": 3, "title": "Dry-run Chapter Three", "topic": "sample topic D, sample topic E"},
                ]
            })
        # Terminology extractor canned response
        if "terminology extractor" in low or "extract a compact terminology" in low:
            return json.dumps({
                "terms": {
                    "example_term": {
                        "preferred_term": "example_term",
                        "definition": "An example term used in dry run.",
                        "definition_status": "defined",
                        "introduced_in": 1
                    }
                }
            })
        # Chapter writer canned response (terminology + chapter)
        if "you are writing one chapter" in low or "[terminology]" in prompt or "[chapter]" in prompt:
            term_json = {"new_term": {"preferred_term": "new_term", "definition": "A dry-run example term.", "definition_status": "defined", "introduced_in": None}}
            chapter_latex = (
                "\\section{Overview}\n"
                "This is a comprehensive sample chapter generated in dry-run mode to verify the end-to-end pipeline.\n\n"
                "\\subsection{Core Concepts}\n"
                "Sample explanation of core concepts with detailed academic explanations and rigor.\n\n"
                "\\subsection{Applications}\n"
                "Sample applied process and worked examples demonstrating practical implementations.\n\n"
                "\\section{Conclusion}\n"
                "Short conclusion leading naturally into the next course topic.\n\n"
                "\\section*{Tutorial Questions}\n"
                "\\begin{enumerate}\n"
                "\\item Explain the primary distinction between core concepts.\n"
                "\\item Describe a common failure mode in practical implementations.\n"
                "\\item How do the principles discussed apply to modern systems?\n"
                "\\item Provide a worked example of the process.\n"
                "\\item Discuss the architectural trade-offs involved.\n"
                "\\item Synthesize the key outcomes of this chapter.\n"
                "\\end{enumerate}\n"
            )
            return "[TERMINOLOGY]\n" + json.dumps(term_json) + "\n[/TERMINOLOGY]\n\n[CHAPTER]\n" + chapter_latex + "\n[/CHAPTER]"
        # Chapter enhancer canned pass
        if "you are the editor of a university lecture note series" in low or "chapter to improve:" in low:
            m = re.search(r"Chapter to improve:\s*(.*)", prompt, re.DOTALL)
            if m and m.group(1).strip():
                return m.group(1).strip()
            return "\\section{Overview}\nSample enhanced content in dry-run.\n"
        # Chapter QA canned pass
        if "chapter qa assistant" in low or "chapter_markdown" in low:
            return json.dumps({"status": "PASS", "issues": [], "warnings": []})
        # Final QA canned pass
        if "final-course qa" in low or "final course markdown" in low or "final_course_markdown" in low:
            return json.dumps({
                "status": "PASS",
                "critical_issues": [],
                "warnings": [],
                "coverage": {"complete": True, "missing": []},
                "terminology_consistent": True,
                "cross_chapter_consistent": True
            })
        # Typesetter canned LaTeX fragment
        if "typeset" in low or "latex" in low or "chapter_markdown" in low:
            return "\\chapter{Sample Chapter}\n\\section{Introduction}\nSample content.\n"
        return "DRY_RUN"

    if provider in ("gemini", "google", "googleai"):
        return _call_gemini(prompt, model, temperature)
    elif provider == "groq":
        return _call_groq(prompt, model, temperature)
    elif provider == "openrouter":
        return _call_openrouter(prompt, model, temperature)
    elif provider == "deepseek":
        return _call_deepseek(prompt, model, temperature)
    else:
        raise RuntimeError(
            f"Unsupported LLM_PROVIDER '{provider}'. Supported values: 'gemini', 'groq', "
            f"'openrouter', 'deepseek'. Set it in your .env file."
        )


def _call_gemini(prompt: str, model: Optional[str], temperature: float) -> str:
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY or GOOGLE_API_KEY must be set to use the gemini provider")

    gmodel = model or os.getenv("GEMINI_MODEL") or os.getenv("LLM_MODEL") or "gemini-3.6-flash"

    try:
        from google import genai
    except Exception as e:
        raise RuntimeError("google-genai package not installed. Run: pip install google-genai") from e

    try:
        client = genai.Client(api_key=api_key)
        resp = call_with_retry(lambda: client.models.generate_content(model=gmodel, contents=prompt))
    except Exception as e:
        raise RuntimeError(f"Gemini (google.genai) call failed: {e}")

    out = getattr(resp, "text", None)
    if not out:
        out = str(resp)
    return out


def _call_compatible_chat_completions(provider_label: str, url: str, api_key: str, model: str,
                                      prompt: str, temperature: float, extra_headers: Optional[Dict[str, str]] = None) -> str:
    """Shared implementation for compatible chat-completions endpoints (Groq,
    OpenRouter, and similar). Prefers the 'requests' library when available — it handles
    connection resets and flaky networks (a common source of WinError 10054 on Windows) far
    more gracefully than raw urllib — and falls back to stdlib urllib if requests isn't
    installed, so no dependency is strictly required."""
    body_dict = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
        # Cloudflare (which fronts several of these APIs) blocks urllib's default
        # "Python-urllib/x.y" User-Agent as a bot signature (HTTP 403, error 1010).
        # Any normal-looking UA avoids that; this doesn't need to be literally accurate.
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) PseudoScribe/1.0",
    }
    if extra_headers:
        headers.update(extra_headers)
    # Free-tier auto-routing can land on a slow backend model; default generous, overridable.
    timeout = float(os.getenv("HTTP_TIMEOUT_SECONDS", "180"))

    try:
        import requests as _requests
    except Exception:
        _requests = None

    def do_request() -> str:
        if _requests is not None:
            try:
                resp = _requests.post(url, json=body_dict, headers=headers, timeout=timeout)
                resp.raise_for_status()
                data = resp.json()
            except _requests.exceptions.HTTPError as e:
                err_body = e.response.text if e.response is not None else ""
                retry_after = e.response.headers.get("Retry-After") if e.response is not None else None
                status = e.response.status_code if e.response is not None else "?"
                msg = f"{status} {e}. {err_body}"
                if retry_after:
                    msg += f" | Retry-After header: {retry_after}s"
                raise RuntimeError(msg)
            except _requests.exceptions.RequestException as e:
                raise RuntimeError(f"{provider_label} request failed: {e}")
        else:
            import urllib.request
            import urllib.error
            req = urllib.request.Request(url, data=json.dumps(body_dict).encode("utf-8"),
                                          headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=timeout) as http_resp:
                    data = json.loads(http_resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8", errors="ignore")
                retry_after = e.headers.get("Retry-After") if e.headers else None
                msg = f"{e.code} {e.reason}. {err_body}"
                if retry_after:
                    msg += f" | Retry-After header: {retry_after}s"
                raise RuntimeError(msg)
            except (urllib.error.URLError, OSError, TimeoutError) as e:
                # OSError/TimeoutError catches raw socket-level errors (connection reset, read
                # timeout) that don't always get wrapped as URLError by urllib.
                reason = getattr(e, "reason", e)
                raise RuntimeError(f"{provider_label} request failed: {reason}")
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(f"Unexpected {provider_label} response shape: {data}") from e

    try:
        return call_with_retry(do_request)
    except Exception as e:
        raise RuntimeError(f"{provider_label} call failed: {e}")


def _call_groq(prompt: str, model: Optional[str], temperature: float) -> str:
    """Groq (https://groq.com) hosts open models (Llama 3.3, GPT-OSS, Qwen, etc.). Sign up at
    https://console.groq.com for a key. Rate/quota numbers change over time and are tracked
    per-model — check https://console.groq.com/settings/limits for your account's current
    figures before relying on a specific model's daily allowance."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY must be set to use the groq provider")
    gmodel = model or os.getenv("GROQ_MODEL") or "llama-3.3-70b-versatile"
    return _call_compatible_chat_completions(
        "Groq", "https://api.groq.com/openai/v1/chat/completions", api_key, gmodel, prompt, temperature
    )


def _call_openrouter(prompt: str, model: Optional[str], temperature: float) -> str:
    """OpenRouter (https://openrouter.ai) proxies many providers' models — including several
    tagged ':free' — behind one OpenAI-compatible API and key. Sign up at
    https://openrouter.ai/keys (no card required). Free-tier models rotate over time; the
    default here ('openrouter/free') auto-routes to a currently-available free model rather
    than hard-coding one that might get delisted. Set OPENROUTER_MODEL to pin a specific model
    — check https://openrouter.ai/models?max_price=0 for the live list. Free tier is roughly
    20 requests/minute and 50/day (1000/day after a one-time $10 credit top-up) at the time of
    writing — verify current numbers at https://openrouter.ai/docs before relying on them."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY must be set to use the openrouter provider")
    gmodel = model or os.getenv("OPENROUTER_MODEL") or "openrouter/free"
    # Optional but recommended by OpenRouter for attribution/rankings — harmless if generic.
    extra_headers = {
        "HTTP-Referer": os.getenv("OPENROUTER_SITE_URL", "https://github.com/richmondsogo/Pseudo-Scribe"),
        "X-Title": os.getenv("OPENROUTER_SITE_NAME", "Pseudo-Scribe"),
    }
    return _call_compatible_chat_completions(
        "OpenRouter", "https://openrouter.ai/api/v1/chat/completions", api_key, gmodel,
        prompt, temperature, extra_headers=extra_headers
    )


def _call_deepseek(prompt: str, model: Optional[str], temperature: float) -> str:
    """DeepSeek's chat-completions API for DeepSeek Chat and Reasoner models."""
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY must be set to use the deepseek provider")
    dmodel = model or os.getenv("DEEPSEEK_MODEL") or "deepseek-chat"
    return _call_compatible_chat_completions(
        "DeepSeek", "https://api.deepseek.com/chat/completions", api_key, dmodel, prompt, temperature
    )

# --- File utilities ---

def load_json(path: Path) -> Any:
    # Use utf-8-sig to tolerate files with BOM (e.g., written from Windows editors)
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)

def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# --- Course layout helpers ---

def course_path(course_id: str) -> Path:
    return COURSES_DIR / course_id

@dataclass
class Course:
    id: str
    path: Path
    profile_path: Path
    outline_path: Path
    terminology_path: Path
    generation_state_path: Path
    prompts_path: Path
    drafts_path: Path
    tex_path: Path
    final_path: Path
    output_pdf: Path

    @classmethod
    def load(cls, course_id: str) -> "Course":
        p = course_path(course_id)
        return cls(
            id=course_id,
            path=p,
            profile_path=p / "course_profile.md",
            outline_path=p / "outline.json",
            terminology_path=p / "terminology.json",
            generation_state_path=p / "generation_state.json",
            prompts_path=p / "prompts",
            drafts_path=p / "drafts",
            tex_path=p / "tex",
            final_path=p / "final",
            output_pdf=p / "output.pdf",
        )

# --- Template rendering ---

def render_prompt(template: str, context: Dict[str, Any]) -> str:
    # Very small placeholder replacement
    result = template
    for k, v in context.items():
        placeholder = f"{{{{{k}}}}}"
        if isinstance(v, (dict, list)):
            repl = json.dumps(v, indent=2, ensure_ascii=False)
        else:
            repl = str(v)
        result = result.replace(placeholder, repl)
    return result

# --- Prompt loading ---

def load_prompt(name: str) -> str:
    path = PROMPTS_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Prompt template not found: {path}")
    return path.read_text(encoding="utf-8")

# --- Terminology utilities ---

def load_terminology(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"terms": {}}
    return load_json(path)

def save_terminology(path: Path, data: Dict[str, Any]) -> None:
    # Do not overwrite lightly; write atomically
    tmp = path.with_suffix(".tmp.json")
    save_json(tmp, data)
    tmp.replace(path)

# Compare definitions using LLM: returns True if conflict
def definitions_conflict(existing_def: Optional[str], new_def: Optional[str]) -> Tuple[bool, str]:
    # If either is None or empty, consider non-conflict unless both are present and clearly contradictory
    if not existing_def or not new_def:
        return False, "one or both definitions empty -> no conflict"
    prompt = f"""
You are a terminology conflict checker. Decide whether the two definitions below describe the same technical concept or are in substantive conflict.

Existing definition:
{existing_def}

New proposed definition:
{new_def}

Answer with a JSON object with fields:
- conflict: true|false
- reasoning: short explanation (one or two sentences)

Be conservative: if uncertain, set conflict=true.
"""
    try:
        resp = call_llm(prompt, temperature=0.0)
    except Exception as e:
        # Can't verify either way. Since the caller always keeps the first definition
        # regardless of this result, default to "no conflict" to avoid noisy false alarms.
        return False, f"conflict check unavailable ({e}); keeping existing definition"
    # Try to extract JSON (tolerates ```json fences or stray commentary around the object)
    try:
        parsed = _extract_json(resp)
        if isinstance(parsed, dict):
            return bool(parsed.get("conflict")), parsed.get("reasoning", "")
        # fallback simple parsing on the raw text if it wasn't a JSON object at all
        lc = resp.strip().lower()
        if "conflict" in lc and "true" in lc:
            return True, resp
        if "conflict" in lc and "false" in lc:
            return False, resp
        # otherwise be conservative
        return True, "Unclear from checker response; conservatively flagging conflict"
    except Exception:
        return True, "Failed to parse checker response; conservatively flagging conflict"

# --- Core operations ---

def cmd_init(course_id: str) -> None:
    p = course_path(course_id)
    if p.exists():
        error(f"Course {course_id} already exists at {p}")
        sys.exit(1)
    # create folder structure
    (p / "prompts").mkdir(parents=True)
    (p / "drafts").mkdir()
    (p / "tex").mkdir()
    (p / "final").mkdir()
    info(f"Created course skeleton at {p}")
    # write minimal outline.json template
    outline = {
        "course_code": course_id,
        "course_title": "",
        "chapters": []
    }
    save_json(p / "outline.json", outline)
    # empty terminology
    save_json(p / "terminology.json", {"terms": {}})
    # generation_state
    state = {"status": "not_started", "chapters": {}, "final_qa": False, "compiled": False}
    save_json(p / "generation_state.json", state)
    # copy prompt templates from repo prompts if present
    repo_prompts = PROMPTS_DIR
    if repo_prompts.exists():
        for file in repo_prompts.iterdir():
            if file.is_file():
                shutil.copy(file, p / "prompts" / file.name)
    info("Initialization complete. Edit course_profile.md and outline.json then run terminology extraction.")

def cmd_profile(course_id: str) -> None:
    c = Course.load(course_id)
    if not c.path.exists():
        error("Course not found. Run 'init' first.")
        sys.exit(1)
    if not c.profile_path.exists():
        template = textwrap.dedent(f"""
        # Course Profile

        Course Code: {course_id}
        Course Title: 
        University: 
        Department: 
        Academic Session: 
        Level: 

        ## Style Rules

        * Use clear academic English.
        * Explain concepts before assuming familiarity.
        * Prefer explanation over memorization.
        * Avoid filler.
        * Use consistent terminology.
        * Do not use conversational language.
        """
        )
        c.profile_path.write_text(template, encoding="utf-8")
        info(f"Wrote template profile to {c.profile_path}")
    else:
        info(f"Profile exists at {c.profile_path}. Open and edit as required.")

def require_course_files(c: Course) -> None:
    if not c.path.exists():
        raise FileNotFoundError(f"Course not found: {c.path}")
    if not c.outline_path.exists():
        raise FileNotFoundError(f"Missing outline.json at {c.outline_path}")
    if not c.profile_path.exists():
        raise FileNotFoundError(f"Missing course_profile.md at {c.profile_path}")
    if not c.terminology_path.exists():
        # create empty
        save_json(c.terminology_path, {"terms": {}})
    if not c.generation_state_path.exists():
        save_json(c.generation_state_path, {"status": "in_progress", "chapters": {}, "final_qa": False, "compiled": False})

def cmd_terminology(course_id: str) -> None:
    c = Course.load(course_id)
    require_course_files(c)
    outline = load_json(c.outline_path)
    profile = c.profile_path.read_text(encoding="utf-8")
    terminology_template = load_prompt("terminology_extractor.txt")
    context = {
        "course_profile": profile,
        "outline_json": outline,
        "source_materials": ""
    }
    prompt = render_prompt(terminology_template, context)
    info("Calling LLM for initial terminology extraction (cost-conscious prompt)...")
    try:
        resp = call_llm(prompt)
    except Exception as e:
        # Non-fatal: chapters still declare their own terms as they're written,
        # so a failed upfront extraction just means terminology.json starts empty.
        warn(f"Terminology extraction call failed ({e}). Continuing with empty terminology; "
             f"terms will still be captured as chapters declare them.")
        return
    # Expect JSON list or object: try to extract JSON
    terms = {}
    try:
        # model should return JSON structure {"terms": {...}}
        data = _extract_json(resp)
        if isinstance(data, dict) and "terms" in data:
            terms = data["terms"]
        else:
            # allow list of entries
            if isinstance(data, list):
                for entry in data:
                    term_key = entry.get("term") or entry.get("preferred_term")
                    if term_key:
                        terms[term_key] = entry
    except Exception:
        # Non-fatal: save raw response for inspection and move on with no pre-seeded terms.
        (c.path / "terminology_raw.txt").write_text(resp, encoding="utf-8")
        warn("Failed to parse terminology extractor output as JSON. Saved raw response to "
             "terminology_raw.txt. Continuing with empty terminology.")
        return
    # Merge with existing but do not overwrite
    existing = load_terminology(c.terminology_path)
    existing_terms = existing.get("terms", {})
    added = 0
    for k, v in terms.items():
        if k in existing_terms:
            continue
        # normalize v
        entry = {
            "preferred_term": v.get("preferred_term") if isinstance(v, dict) else k,
            "definition": v.get("definition") if isinstance(v, dict) else None,
            "definition_status": v.get("definition_status") if isinstance(v, dict) else "undefined",
            "introduced_in": v.get("introduced_in") if isinstance(v, dict) else None,
        }
        existing_terms[k] = entry
        added += 1
    existing["terms"] = existing_terms
    save_terminology(c.terminology_path, existing)
    info(f"Terminology extraction complete. {added} new terms added.")

# Parse LLM chapter generation output into terminology and chapter
def parse_generation_output(text: Optional[str]) -> Tuple[Dict[str, Any], str]:
    """Parse an LLM chapter response into (proposed_terms, chapter_body).

    Expects [TERMINOLOGY] ... [/TERMINOLOGY] and [CHAPTER] ... [/CHAPTER]. Defensive
    against real-world model output: a None response (returned as an empty generation),
    a model closing the chapter with \\end{CHAPTER} instead of [/CHAPTER], and a raw
    [TERMINOLOGY] JSON block that leaked into the chapter body."""
    if not text:
        return {}, ""
    # Normalize a stray LaTeX-style close to the expected marker, then parse.
    text = text.replace("\\end{CHAPTER}", "[/CHAPTER]")
    term_start = text.find("[TERMINOLOGY]")
    term_end = text.find("[/TERMINOLOGY]")
    chap_start = text.find("[CHAPTER]")
    chap_end = text.rfind("[/CHAPTER]")
    terms = {}
    chapter = ""
    if term_start != -1 and term_end != -1:
        raw = text[term_start + len("[TERMINOLOGY]"):term_end].strip()
        # try JSON
        try:
            parsed = _extract_json(raw)
            # allow {"terms": {...}}
            if isinstance(parsed, dict) and "terms" in parsed:
                terms = parsed["terms"]
            elif isinstance(parsed, dict):
                terms = parsed
        except Exception:
            # Non-fatal: this chapter just won't contribute new terms this round.
            terms = {}
    if chap_start != -1 and chap_end != -1:
        chapter = text[chap_start + len("[CHAPTER]"):chap_end].strip()
    else:
        # If markers not present, assume whole response is chapter
        chapter = text.strip()
    # Self-heal: strip a [TERMINOLOGY] block that leaked into the chapter body.
    leaked_start = chapter.find("[TERMINOLOGY]")
    leaked_end = chapter.find("[/TERMINOLOGY]")
    if leaked_start != -1 and leaked_end != -1:
        chapter = (chapter[:leaked_start] + chapter[leaked_end + len("[/TERMINOLOGY]"):]).strip()
    return terms, chapter

def _looks_like_usable_chapter_body(text: str) -> bool:
    """Returns True only if an LLM response looks like a real chapter draft rather than a
    refusal ("User Safety: safe", "I can't help with that"), a one-liner, or raw markdown.
    Requires at least one LaTeX \\section-level command, no leftover delimiter markers, and a
    minimum plausible length (a real chapter has several paragraphs)."""
    t = (text or "").strip()
    if not t:
        return False
    # Any leftover outer delimiters mean the response wasn't parsed cleanly.
    if "[TERMINOLOGY]" in t or "[CHAPTER]" in t or "[/CHAPTER]" in t:
        return False
    # Refusal / safety-guard / placeholder one-liners.
    low = t.lower()
    refusal_markers = (
        "user safety:", "i can't help", "i cannot help", "i am unable",
        "i'm unable", "as an ai", "i apologize", "sorry, i",
        "i don't have enough", "cannot assist", "can't assist",
    )
    if any(marker in low for marker in refusal_markers):
        return False
    if len(t) < 250:
        return False
    # Must actually be LaTeX: a \section (or subsection/subsubsection/tutorial) command.
    if not re.search(r'\\(section|subsection|subsubsection|section\*)\s*\{', t):
        return False
    return True

def _set_chapter_state(c: Course, chapter_number: int, status: str) -> None:
    """Thread-safe read-modify-write of generation_state.json's per-chapter status."""
    with STATE_LOCK:
        state = load_json(c.generation_state_path)
        state.setdefault("chapters", {})[str(chapter_number)] = status
        save_json(c.generation_state_path, state)

TERMINOLOGY_LOCK = threading.Lock()

def _merge_chapter_terms(c: Course, chapter_number: int, proposed_terms: Dict[str, Any]) -> int:
    """Thread-safe: merge one chapter's proposed terms into terminology.json immediately, so
    later chapters (even running concurrently) see the growing registry. Auto-resolves conflicts
    by always keeping the first definition — the LLM-based check (ENABLE_CONFLICT_CHECK) only
    changes whether that's logged as a genuine conflict or silently accepted as a restatement,
    never which definition wins. Returns the count of newly added terms."""
    with TERMINOLOGY_LOCK:
        terminology_json = load_terminology(c.terminology_path)
        loaded_terms = terminology_json.get("terms", {})
        added = 0
        for term_key, term_val in proposed_terms.items():
            if isinstance(term_val, str):
                pref = term_val
                ddef = None
                status = "undefined"
            elif isinstance(term_val, dict):
                pref = term_val.get("preferred_term") or term_key
                ddef = term_val.get("definition")
                status = term_val.get("definition_status") or ("defined" if ddef else "undefined")
            else:
                continue
            if pref in loaded_terms:
                existing = loaded_terms[pref]
                if ENABLE_CONFLICT_CHECK:
                    cflag, reasoning = definitions_conflict(existing.get("definition"), ddef)
                    if cflag:
                        warn(f"Chapter {chapter_number}: term '{pref}' was redefined differently "
                             f"({reasoning}). Keeping the original definition from chapter "
                             f"{existing.get('introduced_in')}.")
                elif ddef and ddef != existing.get("definition"):
                    warn(f"Chapter {chapter_number}: term '{pref}' was redefined. Keeping the "
                         f"original definition from chapter {existing.get('introduced_in')}.")
                # Existing definition is always kept — no overwrite either way.
            else:
                loaded_terms[pref] = {
                    "preferred_term": pref,
                    "definition": ddef,
                    "definition_status": status,
                    "introduced_in": int(chapter_number)
                }
                added += 1
        terminology_json["terms"] = loaded_terms
        save_terminology(c.terminology_path, terminology_json)
        return added

def generate_chapter_content(c: Course, chapter_number: int, chap: Dict[str, Any], total_chapters: int,
                              max_retries: int = MAX_CHAPTER_RETRIES) -> Dict[str, Any]:
    """Generates one chapter directly as LaTeX (no separate typesetting call). Thread-safe:
    reads a fresh terminology snapshot each attempt and merges its own proposed terms back in
    under a lock immediately after generating, so this is safe to run concurrently across
    chapters via cmd_all's thread pool (default concurrency is 1 — see MAX_CONCURRENT_CHAPTERS).
    Never raises for recoverable failures — returns a result dict so a batch runner can collect
    outcomes without one chapter's failure affecting the others."""
    title = chap.get("title") or f"Chapter {chapter_number}"
    profile = c.profile_path.read_text(encoding="utf-8")
    outline = load_json(c.outline_path)
    chapter_writer_template = load_prompt("chapter_writer.txt")

    attempts = 0
    last_issues: Any = None
    chapter_body: Optional[str] = None
    while attempts < max_retries:
        attempts += 1
        info(f"[chapter {chapter_number}] [Attempt {attempts}/{max_retries}] Generating ...")
        terminology_snapshot = load_terminology(c.terminology_path)
        context = {
            "course_profile": profile,
            "outline_json": outline,
            "terminology_json": terminology_snapshot,
            "chapter_number": chapter_number,
            "chapter_title": chap.get("title"),
            "chapter_topic": chap.get("topic"),
            "source_materials": ""
        }
        prompt = render_prompt(chapter_writer_template, context)
        try:
            resp = call_llm(prompt)
        except Exception as e:
            last_issues = f"LLM call failed: {e}"
            warn(f"[chapter {chapter_number}] generation attempt {attempts} failed ({e}).")
            continue
        proposed_terms, chapter_body = parse_generation_output(resp)
        if not chapter_body.strip():
            last_issues = "Response had no usable chapter content"
            warn(f"[chapter {chapter_number}] attempt {attempts} produced no chapter content; retrying.")
            chapter_body = None
            continue
        # Refusal / degenerate-response guard: a one-line or non-LaTeX response
        # ("User Safety: safe", "I can't help with that", raw markdown, ...) must never
        # be saved as a chapter. Require real \section (or other LaTeX) structure and
        # a minimum plausible length before accepting it as a draft.
        if not _looks_like_usable_chapter_body(chapter_body):
            last_issues = f"Response was not a usable LaTeX chapter body: {chapter_body[:200]!r}"
            warn(f"[chapter {chapter_number}] attempt {attempts} was rejected as a refusal or "
                 f"degenerate response; retrying.")
            chapter_body = None
            continue

        new_term_count = _merge_chapter_terms(c, chapter_number, proposed_terms)

        if ENABLE_CHAPTER_ENHANCE:
            try:
                enhanced = run_chapter_enhancer(c, chapter_body, chapter_number)
                if enhanced.strip():
                    chapter_body = enhanced
                    info(f"[chapter {chapter_number}] Enhancement pass applied (diagrams/polish).")
            except Exception as e:
                warn(f"[chapter {chapter_number}] Enhancement call failed ({e}). Keeping the "
                     f"original draft — nothing lost, just not polished this round.")

        # Save the chapter (LaTeX). Kept at drafts/chXX.md so the rest of the pipeline (state
        # tracking, final assembly) doesn't need to change; only the content format changed.
        ch_filename = f"ch{int(chapter_number):02d}.md"
        (c.drafts_path / ch_filename).write_text(chapter_body, encoding="utf-8")
        info(f"[chapter {chapter_number}] Draft written to {c.drafts_path / ch_filename}")
        info(f"[{chapter_number}/{total_chapters}] {title}\n  - Generated\n  - {new_term_count} new terms added")
        _set_chapter_state(c, chapter_number, "complete")
        return {"status": "complete", "chapter": chapter_number}

    # Retries exhausted (the writer call itself kept failing — enhancement never got involved).
    warn(f"[chapter {chapter_number}] could not be generated after {attempts} attempts ({last_issues}). "
         f"No draft available — it will be skipped when assembling the final document.")
    _set_chapter_state(c, chapter_number, "failed")
    return {"status": "failed", "chapter": chapter_number, "error": str(last_issues)}

def run_chapter_enhancer(c: Course, chapter_body: str, chapter_number: Optional[int] = None) -> str:
    """Optional second pass (ENABLE_CHAPTER_ENHANCE): actively improves a chapter's LaTeX body
    and adds ASCII diagrams where they'd help, rather than just validating it. Returns the
    improved body; raises on failure so the caller can fall back to the original untouched."""
    template = load_prompt("chapter_enhancer.txt")
    terminology = load_terminology(c.terminology_path)
    context = {
        "terminology_json": terminology,
        "chapter_body": chapter_body,
    }
    prompt = render_prompt(template, context)
    resp = call_llm(prompt, temperature=0.2)
    # Defensive: strip an accidental document wrapper or \chapter{} line if the model added one.
    resp = re.sub(r'\\documentclass.*?\\begin\{document\}', '', resp, flags=re.DOTALL)
    resp = re.sub(r'\\end\{document\}', '', resp)
    resp = re.sub(r'^\s*\\chapter\{[^}]*\}\s*', '', resp.strip())
    return resp

def cmd_chapter(course_id: str, chapter_number: int) -> None:
    c = Course.load(course_id)
    if not c.path.exists():
        error("Course not found. Run 'init' first.")
        sys.exit(1)
    try:
        require_course_files(c)
        outline = load_json(c.outline_path)
        chapters = outline.get("chapters", [])
        chap = next((ch for ch in chapters if int(ch.get("number")) == int(chapter_number)), None)
        if not chap:
            error(f"Chapter {chapter_number} not found in outline.json")
            sys.exit(1)
        result = generate_chapter_content(c, chapter_number, chap, len(chapters))
    except Exception as e:
        error(str(e))
        sys.exit(1)
    if result.get("status") == "failed":
        error(f"Chapter {chapter_number} failed: {result.get('error')}")
        sys.exit(1)

def cmd_all(course_id: str) -> None:
    c = Course.load(course_id)
    require_course_files(c)
    outline = load_json(c.outline_path)
    chapters = outline.get("chapters", [])
    total = len(chapters)
    state = load_json(c.generation_state_path)
    chapter_state = state.get("chapters", {})
    to_generate = [ch for ch in chapters
                   if chapter_state.get(str(int(ch.get("number")))) not in ("complete", "complete_with_warnings")]
    already_done = total - len(to_generate)
    if already_done:
        info(f"{already_done} chapter(s) already complete, skipping.")
    if not to_generate:
        info("No chapters need generation.")
        return
    info(f"Generating {len(to_generate)} chapter(s) for {course_id} "
         f"(concurrency={MAX_CONCURRENT_CHAPTERS})")
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_CHAPTERS) as ex:
        future_map = {
            ex.submit(generate_chapter_content, c, int(ch.get("number")), ch, total): int(ch.get("number"))
            for ch in to_generate
        }
        for fut in as_completed(future_map):
            num = future_map[fut]
            try:
                fut.result()
            except Exception as e:
                # generate_chapter_content shouldn't raise for recoverable failures, but if
                # something unexpected slips through, don't let it kill the other chapters.
                warn(f"Chapter {num} raised an unexpected error ({e}). Skipping and continuing.")
    info("All chapters generation finished (or were already complete).")

# Final QA


def cmd_qa(course_id: str) -> None:
    c = Course.load(course_id)
    require_course_files(c)
    # assemble final course markdown (this is the real, complete deliverable — untouched below)
    outline = load_json(c.outline_path)
    chapters = outline.get("chapters", [])
    final_md = []
    qa_excerpt_md = []
    included = 0
    for ch in chapters:
        fname = c.drafts_path / f"ch{int(ch.get('number')):02d}.md"
        if not fname.exists():
            warn(f"Missing chapter draft: {fname}. Excluding chapter {ch.get('number')} from the final document.")
            continue
        text = fname.read_text(encoding="utf-8")
        final_md.append(f"# Chapter {ch.get('number')}: {ch.get('title')}\n\n")
        final_md.append(text)
        final_md.append("\n\n")
        # For the QA call only: a bounded excerpt, not the full chapter. Full text scales with
        # course length and will eventually exceed any free-tier per-minute token budget — the
        # excerpt still lets the model judge terminology use, tone, and coverage per chapter.
        excerpt = text[:FINAL_QA_EXCERPT_CHARS]
        if len(text) > FINAL_QA_EXCERPT_CHARS:
            excerpt += "\n...[chapter truncated for this QA pass; the full chapter is complete in final_course.md]..."
        qa_excerpt_md.append(f"# Chapter {ch.get('number')}: {ch.get('title')}\n\n{excerpt}\n\n")
        included += 1
    final_content = "\n".join(final_md)
    (c.final_path / "final_course.md").write_text(final_content, encoding="utf-8")
    if included == 0:
        warn("No chapter drafts were available to assemble. Skipping final QA.")
        return
    qa_content = "\n".join(qa_excerpt_md)
    # Run final QA prompt
    template = load_prompt("final_qa.txt")
    terminology = load_terminology(c.terminology_path)
    profile = c.profile_path.read_text(encoding="utf-8")
    context = {
        "course_profile": profile,
        "outline_json": outline,
        "terminology_json": terminology,
        "final_course_markdown": qa_content
    }
    prompt = render_prompt(template, context)
    info("Running final course QA. This may be a longer call.")
    state = load_json(c.generation_state_path)
    heavy_provider = os.getenv("HEAVY_CALL_PROVIDER") or LLM_PROVIDER
    try:
        resp = call_llm(prompt, temperature=0.0, provider=heavy_provider)
    except Exception as e:
        warn(f"Final QA call failed ({e}). Proceeding to compile without a final QA report.")
        state["final_qa"] = True
        state["final_qa_passed"] = None
        save_json(c.generation_state_path, state)
        return
    try:
        report = _extract_json(resp)
        if not isinstance(report, dict):
            raise ValueError("Final QA response was not a JSON object")
        (c.final_path / "final_qa.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        status = report.get("status", "FAIL")
        state["final_qa"] = True
        state["final_qa_passed"] = (status == "PASS")
        save_json(c.generation_state_path, state)
        if status != "PASS":
            warn(f"Final QA flagged issues (proceeding anyway): {report.get('critical_issues', [])}")
        else:
            info("Final QA passed.")
    except Exception:
        (c.final_path / "final_qa_raw.txt").write_text(resp, encoding="utf-8")
        warn("Failed to parse final QA response as JSON. Saved raw output to final_qa_raw.txt. Proceeding to compile.")
        state["final_qa"] = True
        state["final_qa_passed"] = None
        save_json(c.generation_state_path, state)

# Typesetting: ask LLM to convert markdown chapter into LaTeX chapter file according to rules.

def _latex_escape(text: str) -> str:
    """Escapes LaTeX-special characters in plain metadata values (course title, department,
    etc.) before they're interpolated into the deterministic document scaffold — these come
    from free-text user input and could otherwise break compilation or, worse, silently
    misrender."""
    if not text:
        return ""
    replacements = {
        "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$",
        "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    out = []
    for ch in text:
        out.append(replacements.get(ch, ch))
    return "".join(out)

def parse_course_profile_fields(profile_text: str) -> Dict[str, str]:
    """Parses the simple 'Key: Value' lines at the top of course_profile.md into a dict."""
    fields: Dict[str, str] = {}
    for line in profile_text.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if key and value:
            fields[key] = value
    return fields

def _shared_preamble() -> str:
    """Packages and typography shared by the front matter and every chapter body, so the whole
    merged book reads as one consistent document rather than mismatched fragments.

    Typography choices, and why: Latin Modern (lmodern) gives a clean textbook serif — not the
    heavy Times look — while parskip provides spaced, unindented paragraphs that read as modern
    lecture notes rather than a cramped article. Tight list spacing via enumitem keeps lists from
    eating a disproportionate amount of page space (generated content leans heavily on bullet
    lists)."""
    lines = []
    lines.append("\\documentclass[12pt]{report}\n")
    lines.append("\\usepackage[top=2.3cm,bottom=2.5cm,left=2.5cm,right=2.5cm]{geometry}\n")
    lines.append("\\usepackage[T1]{fontenc}\n")
    lines.append("\\usepackage{lmodern}\n")  # Latin Modern: clean textbook serif, not heavy Times
    lines.append("\\usepackage{microtype}\n")
    lines.append("\\usepackage{graphicx}\n")
    lines.append("\\usepackage{booktabs}\n")
    lines.append("\\usepackage{enumitem}\n")
    lines.append("\\setlist{itemsep=0.15em,topsep=0.35em,parsep=0pt}\n")  # tighter, book-style lists
    lines.append("\\usepackage{array}\n")
    lines.append("\\usepackage{float}\n")
    lines.append("\\usepackage{longtable}\n")
    lines.append("\\usepackage{tabularx}\n")
    lines.append("\\usepackage{amsmath}\n")
    lines.append("\\usepackage{amssymb}\n")
    lines.append("\\usepackage{parskip}\n")  # spaced, unseparated unindented paragraphs: clean textbook style
    # hidelinks: fully clickable ToC/links, no coloured borders or boxes around them.
    lines.append("\\usepackage[hidelinks]{hyperref}\n\n")
    return "".join(lines)

def build_chapter_body_document(c: Course, chapter_number: int, chapter_title: str, body_fragment: str,
                                 start_page: Optional[int] = None) -> str:
    """A complete, standalone, compilable LaTeX document for one chapter's CONTENT ONLY — no
    title page, no table of contents. These get merged after a separate front-matter document,
    so the merged result reads as one continuous book rather than N stapled-together documents.
    Chapter numbering is still set explicitly via \\setcounter{chapter} so it's correct (e.g.
    11.1, 11.2) even if earlier chapters are missing from this run. start_page, once known (see
    the two-pass compile in cmd_compile), sets this chapter's own printed page numbers to match
    its true position in the final merged book rather than restarting at 1 for every chapter.
    The LLM is never responsible for this scaffolding — only body_fragment (from the chapter
    writer or typesetter fallback) comes from the model."""
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

def build_front_matter_document(c: Course, toc_entries: List[Tuple[int, str, Optional[int]]]) -> str:
    """The book's title page plus one real table of contents (chapter titles with page numbers)
    covering every chapter that will be merged in. toc_entries is (chapter_number, title,
    start_page) — start_page is None on the first pass (used only to measure how many pages the
    front matter itself takes), then filled in with real numbers on the second pass once that's
    known (see compile_front_matter)."""
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
    # No headers, plain page numbers starting right after the (unnumbered) title page.
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

def build_single_book_document(c: Course, chapter_fragments: List[Tuple[int, str, str]]) -> str:
    """Builds the WHOLE book as one LaTeX document — title page, a REAL \\tableofcontents
    (which hyperref + hidelinks makes fully clickable with no visible borders), then every
    chapter body back to back. chapter_fragments is (chapter_number, title, body_fragment).
    Chapter counters are set explicitly so numbering is correct even when some chapters are
    skipped; the \\tableofcontents and hyperlinks only cover chapters actually included."""
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
    """Escapes bare '%' characters in a LaTeX body fragment to '\\%' — the single most
    common cause of 'chapter came out broken': a %%! literal like '90%' starts a LaTeX
    comment that silently truncates the rest of that line. Everything inside \\begin{verbatim}
    ... \\end{verbatim} blocks is left untouched (ASCII diagrams use % in cell separators)."""
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
    """Self-heal: strip leftover [TERMINOLOGY]...[/TERMINOLOGY] and [CHAPTER]...[/CHAPTER]
    blocks from an existing draft (written by older versions that saved raw delimiters)."""
    t = text
    # Strip [TERMINOLOGY]...[/TERMINOLOGY] blocks (possibly multiple).
    while "[TERMINOLOGY]" in t and "[/TERMINOLOGY]" in t:
        start = t.find("[TERMINOLOGY]")
        end = t.find("[/TERMINOLOGY]") + len("[/TERMINOLOGY]")
        t = (t[:start] + t[end:]).strip()
    # Strip a [CHAPTER]...[/CHAPTER] wrapper if present.
    if "[CHAPTER]" in t and "[/CHAPTER]" in t:
        start = t.find("[CHAPTER]")
        end = t.rfind("[/CHAPTER]")
        end = end + len("[/CHAPTER]") if end != -1 else len(t)
        inner = t[start + len("[CHAPTER]"):end]
        if "[TERMINOLOGY]" not in inner:
            t = inner.strip()
    # Defensive: remove any lone leftover markers (e.g. a trailing [/CHAPTER] whose
    # opening tag was already consumed).
    t = (t.replace("[CHAPTER]", "")
          .replace("[/CHAPTER]", "")
          .replace("[TERMINOLOGY]", "")
          .replace("[/TERMINOLOGY]", "")
          .strip())
    return t


def _looks_like_body_fragment(text: str) -> bool:
    """Detects whether a draft is already a LaTeX body fragment (written directly by the
    current chapter_writer.txt — starts with \\section, no \\chapter/\\documentclass) vs.
    older-format content (Markdown, or an old-style fragment that starts with \\chapter{...})
    that still needs a conversion pass."""
    head = text.strip()[:300]
    if re.search(r'\\documentclass|\\begin\{document\}', head):
        return False  # old accidental full-document output, needs re-conversion
    return bool(re.match(r'\\section\s*\{', head))

def get_chapter_body_fragment(c: Course, chapter_number: int) -> Optional[str]:
    """Returns the LaTeX body fragment for a chapter — directly, with no LLM call, if the draft
    was already written as one; otherwise converts an older-format draft via one LLM call
    (routed through HEAVY_CALL_PROVIDER if set). Returns None if it can't produce one.
    Self-heals existing junk drafts: strips stray [TERMINOLOGY]/[CHAPTER] delimiters, escapes
    bare '%' characters (outside verbatim), and refuses one-line refusal content."""
    fname = c.drafts_path / f"ch{int(chapter_number):02d}.md"
    if not fname.exists():
        warn(f"No draft for chapter {chapter_number}; skipping it in the compiled PDF.")
        return None
    draft_content = fname.read_text(encoding="utf-8")

    # Self-heal legacy drafts that saved raw delimiter markers.
    cleaned = _strip_stray_delimiters(draft_content)

    # A draft that is just a refusal / one-liner / raw markdown is not usable.
    if not _looks_like_usable_chapter_body(cleaned):
        warn(f"Chapter {chapter_number} draft is not a usable LaTeX body fragment "
             f"(maybe an older refusal or markdown draft). Keeping it as-is instead of "
             f"feeding it to the PDF; {fname} can be regenerated with "
             f"'python generate.py chapter {c.id} {chapter_number}'.")
        return None

    if _looks_like_body_fragment(cleaned):
        cleaned = _sanitize_latex_percent(cleaned)
        return cleaned  # no LLM call needed

    # Fallback: older Markdown (or old \chapter{}-prefixed) drafts get converted to a bare
    # body fragment. One-time cost per such chapter.
    template = load_prompt("typesetter.txt")
    outline = load_json(c.outline_path)
    chap = next((ch for ch in outline.get("chapters", []) if int(ch.get("number")) == int(chapter_number)), None)
    context = {
        "chapter_number": chapter_number,
        "chapter_title": chap.get("title") if chap else f"Chapter {chapter_number}",
        "chapter_markdown": draft_content,
        "terminology_json": load_terminology(c.terminology_path)
    }
    prompt = render_prompt(template, context)
    try:
        resp = call_llm(prompt, temperature=0.0, provider=os.getenv("HEAVY_CALL_PROVIDER") or LLM_PROVIDER)
    except Exception as e:
        warn(f"Typesetting chapter {chapter_number} failed ({e}). Skipping it in the compiled PDF "
             f"(the markdown draft is still available at {fname}).")
        return None
    # Defensive: strip an accidental \chapter{...} line or document wrapper if the model added one anyway.
    resp = re.sub(r'\\documentclass.*?\\begin\{document\}', '', resp, flags=re.DOTALL)
    resp = re.sub(r'\\end\{document\}', '', resp)
    resp = re.sub(r'^\s*\\chapter\{[^}]*\}\s*', '', resp.strip())
    # Self-healing: write the converted body back so this draft now "looks like" a body fragment
    # and never triggers this LLM call again on future compiles/resumes.
    fname.write_text(resp, encoding="utf-8")
    return resp

def compile_chapter_pdf(c: Course, chapter_number: int, chapter_title: str) -> Optional[Path]:
    """Builds and compiles one chapter's content into its own PDF (no title page/ToC of its own
    — those live in the separately-compiled front matter). Returns the PDF path on success,
    None if it couldn't be produced (draft missing, LaTeX error, pdflatex missing). Page numbers
    start at 1 here — see finalize_chapter_pdf_page_numbers for the corrected final pass."""
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

def finalize_chapter_pdf_page_numbers(c: Course, chapter_number: int, chapter_title: str, start_page: int) -> Optional[Path]:
    """Recompiles an already-successfully-compiled chapter with its printed page numbers set to
    its true position in the merged book (e.g. chapter 2 starting at page 9, not page 1 again).
    No LLM call — by this point the draft is guaranteed to already be a body fragment (either
    originally, or self-healed by get_chapter_body_fragment's fallback conversion)."""
    pdf_path = c.tex_path / f"ch{int(chapter_number):02d}.pdf"
    tex_path = c.tex_path / f"ch{int(chapter_number):02d}.tex"
    fname = c.drafts_path / f"ch{int(chapter_number):02d}.md"
    if not fname.exists():
        return None
    body = fname.read_text(encoding="utf-8")
    # Same self-heal/sanitize as get_chapter_body_fragment so the final page-number pass
    # never reintroduces a bare % or stray delimiters that the first pass already cleaned.
    body = _strip_stray_delimiters(body)
    if _looks_like_body_fragment(body):
        body = _sanitize_latex_percent(body)
    document = build_chapter_body_document(c, chapter_number, chapter_title, body, start_page=start_page)
    tex_path.write_text(document, encoding="utf-8")
    return _run_pdflatex(c.tex_path, tex_path, pdf_path, error_label=f"chapter {chapter_number}")

def _run_pdflatex(work_dir: Path, tex_path: Path, expected_pdf: Path, error_label: str, passes: int = 2) -> Optional[Path]:
    """Runs pdflatex (twice by default so cross-references resolve; a 3-pass run is expected
    for a book with \tableofcontents and hyperref) in work_dir and returns the produced PDF
    path, or None if it couldn't be produced. Shared by chapter, front-matter, and book
    compiles. Extra passes are cheap and aimed at resolving the ToC/labels; the loop stops
    early once an error is seen so we never stall on 3 failing runs."""
    if os.getenv("DRY_RUN", "").lower() in ("1", "true", "yes"):
        try:
            from pypdf import PdfWriter
            writer = PdfWriter()
            writer.add_blank_page(width=595, height=842)  # a genuinely valid, mergeable PDF
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
        res: Optional[subprocess.CompletedProcess] = None
        for i in range(passes):
            try:
                res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            except FileNotFoundError:
                warn("'pdflatex' isn't installed or isn't on PATH. Install a LaTeX distribution "
                     "(e.g. MiKTeX on Windows, TeX Live on macOS/Linux) — your LaTeX source is "
                     f"still ready at {tex_path}.")
                return None
            last_output = res.stdout + b"\n" + res.stderr
            if res.returncode != 0:
                (tex_path.with_suffix("")).with_name(tex_path.stem + "_error.log").write_bytes(last_output)
        # nonstopmode doesn't always set a nonzero exit code even when real LaTeX errors occur
        # (e.g. an unescaped '%' silently truncating a table row). Scan the log for genuine
        # error lines ('! ...') rather than trusting the exit status alone.
        log_text = (last_output or b"").decode("utf-8", errors="replace")
        error_lines = [
            ln.strip() for ln in log_text.splitlines()
            if ln.strip().startswith("!")
            and "Emergency stop" not in ln
            and "==> Fatal error occurred" not in ln
        ]
        if error_lines or (res is not None and res.returncode != 0):
            error_log = tex_path.with_suffix(".error.log")
            error_log.write_bytes(last_output)
            status_desc = error_lines[0] if error_lines else (
                f"exit code {res.returncode}" if res is not None else "unknown"
            )
            warn(f"LaTeX error compiling {error_label}: {status_desc}. "
                 f"See {error_log} for the full log. The .tex source is kept for manual fixing.")
            return None
        if expected_pdf.exists():
            info(f"Compiled {error_label}: {expected_pdf}")
            return expected_pdf
        warn(f"{error_label.capitalize()} did not compile to PDF (see {tex_path.stem}_error.log "
             f"in {work_dir}). Its LaTeX source is still available for manual fixing.")
        return None
    finally:
        os.chdir(old_cwd)

def compile_front_matter(c: Course, entries: List[Tuple[int, str]], chapter_page_counts: Dict[int, int]) -> Optional[Path]:
    """Compiles the book's title page + one real, page-numbered table of contents, covering
    only the chapters that actually compiled successfully. Two-pass: first compile measures how
    many pages the front matter itself takes, then a second pass fills in correct chapter start
    pages computed from that plus each chapter's own page count."""
    front_tex = c.tex_path / "front_matter.tex"
    front_pdf = c.tex_path / "front_matter.pdf"

    # Pass 1: blank page numbers, just to measure the front matter's own length.
    draft_entries: List[Tuple[int, str, Optional[int]]] = [(num, title, None) for num, title in entries]
    front_tex.write_text(build_front_matter_document(c, draft_entries), encoding="utf-8")
    pass1_pdf = _run_pdflatex(c.tex_path, front_tex, front_pdf, error_label="front matter (pass 1)")
    if pass1_pdf is None:
        return None
    try:
        from pypdf import PdfReader
        front_page_count = len(PdfReader(str(pass1_pdf)).pages)
    except Exception as e:
        warn(f"Couldn't measure front matter length ({e}); falling back to a 1-page estimate, "
             f"so contents page numbers may be off by a page or two.")
        front_page_count = 1

    # Pass 2: real starting page for each chapter = front matter length + preceding chapters' pages.
    real_entries = []
    running_page = front_page_count + 1
    for num, title in entries:
        real_entries.append((num, title, running_page))
        running_page += chapter_page_counts.get(num, 1)
    front_tex.write_text(build_front_matter_document(c, real_entries), encoding="utf-8")
    return _run_pdflatex(c.tex_path, front_tex, front_pdf, error_label="front matter")

def assemble_course_pdf(c: Course, front_matter_pdf: Path, chapter_pdfs: List[Tuple[int, str, Path]]) -> bool:
    """Merges the front matter and every successfully-compiled chapter (in chapter-number order)
    into one continuous book PDF, with PDF bookmarks for navigation as a bonus. PDF-level
    merging never re-invokes LaTeX, so this step can't itself fail from a LaTeX error."""
    try:
        from pypdf import PdfWriter
    except Exception:
        warn("pypdf isn't installed, so the book can't be assembled into one PDF. "
             f"Run: pip install pypdf. Individual pieces are still in {c.tex_path}.")
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
    c = Course.load(course_id)
    require_course_files(c)
    state = load_json(c.generation_state_path)
    if not state.get("final_qa"):
        warn("Final QA wasn't run before compiling. Proceeding anyway.")
    outline = load_json(c.outline_path)
    chapters = outline.get("chapters", [])

    # Single-document build: title page + REAL \\tableofcontents (made clickable by hyperref
    # with hidelinks — no visible borders) + every chapter body in one document. This is the
    # only way the contents page is genuinely navigable in the PDF, rather than a hand-drawn
    # list merged from separately-compiled PDFs.
    chapter_fragments: List[Tuple[int, str, str]] = []
    for ch in chapters:
        num = int(ch.get("number"))
        title = ch.get("title") or f"Chapter {num}"
        body = get_chapter_body_fragment(c, num)
        if body is None:
            warn(f"Chapter {num} couldn't be converted to a LaTeX body fragment; skipping it in "
                 f"the compiled PDF (its draft is still at {c.drafts_path}).")
            continue
        chapter_fragments.append((num, title, body))

    if not chapter_fragments:
        warn("No chapters could be compiled to PDF this run. Your chapter drafts are still "
             f"available as markdown/LaTeX in {c.drafts_path} and {c.tex_path}.")
        return
    if len(chapter_fragments) < len(chapters):
        warn(f"Only {len(chapter_fragments)}/{len(chapters)} chapters compiled; the book will only "
             f"include the ones that succeeded. The rest are still available individually where "
             f"they got to.")

    book_document = build_single_book_document(c, chapter_fragments)
    c.tex_path.mkdir(parents=True, exist_ok=True)
    main_tex = c.tex_path / "main.tex"
    main_pdf = c.tex_path / "main.pdf"
    main_tex.write_text(book_document, encoding="utf-8")
    info(f"Wrote single-document book LaTeX: {main_tex}")

    # 3 passes: first builds the .toc, second/third resolve page numbers and hyperlink targets.
    pdf = _run_pdflatex(c.tex_path, main_tex, main_pdf, error_label="book", passes=3)
    if pdf is None:
        warn(f"Book PDF didn't compile; LaTeX source is still available at {main_tex}.")
        return
    c.output_pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.replace(c.output_pdf)
    info(f"Book compiled: {c.output_pdf}")
    state["compiled"] = True
    save_json(c.generation_state_path, state)

def cmd_build(course_id: str) -> None:
    run_pipeline(course_id)

def print_run_summary(c: Course) -> None:
    """Hands-off runs never hard-stop, so this is the one place that tells you what you
    actually got: a PDF, a LaTeX source fallback, or markdown drafts — plus any warnings."""
    save_run_warnings(c)
    state = load_json(c.generation_state_path)
    chapters_state = state.get("chapters", {})
    outline_chapters = load_json(c.outline_path).get("chapters", [])
    total = len(outline_chapters)
    complete = sum(1 for v in chapters_state.values() if v in ("complete", "complete_with_warnings"))
    with_warnings = sum(1 for v in chapters_state.values() if v == "complete_with_warnings")
    _safe_print("\n" + "─" * 50)
    _safe_print("Run summary")
    _safe_print("─" * 50)
    suffix = f" ({with_warnings} with QA warnings)" if with_warnings else ""
    _safe_print(f"Chapters generated: {complete}/{total}{suffix}")
    _safe_print(f"LLM calls made this run: {LLM_CALL_COUNT}")
    if state.get("compiled") and c.output_pdf.exists():
        _safe_print(f"PDF ready:   {c.output_pdf}")
    elif (c.path / "main.tex").exists():
        _safe_print("PDF compile didn't complete, but LaTeX source is ready:")
        _safe_print(f"   {c.path / 'main.tex'}")
        _safe_print(f"   {c.tex_path} (per-chapter .tex files)")
    else:
        _safe_print("No PDF or .tex output this run. Markdown drafts are available:")
        _safe_print(f"   {c.drafts_path}")
    if RUN_WARNINGS:
        _safe_print(f"\n{len(RUN_WARNINGS)} warning(s) were recorded:")
        for w in RUN_WARNINGS[:10]:
            _safe_print(f"   - {w}")
        if len(RUN_WARNINGS) > 10:
            _safe_print(f"   ... and {len(RUN_WARNINGS) - 10} more.")
        _safe_print(f"Full list saved to {c.path / 'run_warnings.json'}")
    _safe_print("─" * 50)

# --- Interactive mode and CLI ---
from typing import Iterable


def format_course_dir_name(course_code: str, course_title: str) -> str:
    safe_code = "".join(ch for ch in course_code if ch.isalnum())
    safe_title = "".join(ch if (ch.isalnum() or ch.isspace()) else "" for ch in course_title)
    title_slug = "_".join(safe_title.strip().split())
    name = f"{safe_code}_{title_slug}" if title_slug else safe_code
    return name


def normalize_chapter_title(title: str) -> str:
    """Convert common learning-outcome phrasing into concise chapter titles."""
    value = re.sub(r"\s+", " ", str(title or "")).strip()
    value = re.sub(r"[;.:]+(?=\s|$)", "", value).strip()
    value = re.sub(r"\s+(?:and|or)$", "", value, flags=re.IGNORECASE).strip()
    patterns = (
        (r"^(?:analyse|analyze) the concepts of (.+)$", r"Foundations of \1"),
        (r"^list major forms, types and root causes of (.+)$", r"Forms, Types and Root Causes of \1"),
        (r"^differentiate between (.+)$", r"\1"),
        (r"^enumerate (.+?) strategies$", r"\1 Strategies"),
        (r"^describe roles of (.+)$", r"\1"),
    )
    transformed = False
    for pattern, replacement in patterns:
        match = re.match(pattern, value, flags=re.IGNORECASE)
        if match:
            value = re.sub(pattern, replacement, value, flags=re.IGNORECASE)
            value = re.sub(r"\s+in$", "", value, flags=re.IGNORECASE).strip()
            value = value.title()
            value = re.sub(r"\b(Of|And|The|In|On|To)\b", lambda match: match.group().lower(), value)
            transformed = True
            break
    return value if transformed else (value[:1].upper() + value[1:] if value else "Chapter")


def parse_outline_text_to_chapters_heuristic(text: str) -> List[Dict[str, Any]]:
    # Regex-based fallback only. Used when the LLM-based extractor (the primary path) is
    # unavailable or fails to parse. See parse_outline_text_to_chapters_llm below.
    chapters: List[Dict[str, Any]] = []
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    chapter_re = re.compile(r'^(?:Chapter\s+)?(\d{1,3})[:\.)\s-]*(.*)$', re.IGNORECASE)
    md_heading_re = re.compile(r'^#{1,3}\s+(.*)$')
    numbered_re = re.compile(r'^(\d{1,3})[\.:]\s+(.*)$')
    # First pass: collect candidate headings
    for ln in lines:
        m = chapter_re.match(ln)
        if m:
            num = int(m.group(1))
            title = m.group(2).strip() or f"Chapter {num}"
            chapters.append({"number": num, "title": normalize_chapter_title(title), "topic": ""})
            continue
        m = md_heading_re.match(ln)
        if m:
            title = m.group(1).strip()
            # try to see if title begins with a number
            m2 = re.match(r'^(\d{1,3})[\s\-:\.)]+(.*)$', title)
            if m2:
                num = int(m2.group(1))
                t = m2.group(2).strip() or f"Chapter {num}"
                chapters.append({"number": num, "title": normalize_chapter_title(t), "topic": ""})
            else:
                # treat as a heading; if no explicit number assign next
                chapters.append({"number": len(chapters) + 1, "title": normalize_chapter_title(title), "topic": ""})
            continue
        m = numbered_re.match(ln)
        if m:
            num = int(m.group(1))
            title = m.group(2).strip()
            chapters.append({"number": num, "title": normalize_chapter_title(title), "topic": ""})
    # If still empty, fallback: split by blank lines take first sentence as title
    if not chapters:
        paragraphs = re.split(r"\n\s*\n", text)
        for i, p in enumerate(paragraphs[:50], start=1):
            first_line = p.strip().splitlines()[0][:120]
            if len(first_line) > 0:
                chapters.append({"number": i, "title": normalize_chapter_title(first_line), "topic": ""})
    # Ensure unique, sorted by number
    seen = set()
    unique: List[Dict[str, Any]] = []
    for ch in sorted(chapters, key=lambda x: x["number"]):
        if ch["number"] in seen:
            continue
        seen.add(ch["number"])
        unique.append(ch)
    return unique


def _extract_json(text: str) -> Any:
    """Best-effort JSON extraction from an LLM response: strips ```json fences if present,
    and if that still doesn't parse, grabs the first balanced {...} block. Raises ValueError
    if nothing parseable is found."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.lower().startswith("json"):
            t = t[4:]
        t = t.strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    start = t.find("{")
    if start != -1:
        depth = 0
        for i in range(start, len(t)):
            if t[i] == "{":
                depth += 1
            elif t[i] == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(t[start:i + 1])
    raise ValueError("No JSON object found in response")


def parse_outline_text_to_chapters_llm(text: str) -> List[Dict[str, Any]]:
    """Primary outline parser. Pasted/uploaded course outlines arrive in wildly inconsistent
    shapes — one unbroken paragraph, bullet lists, numbered learning outcomes mixed in with
    course content, headings, etc. — and no regex reliably handles all of that. This asks the
    model to read the raw text the way a human would and segment it into a sensible chapter
    list, distinguishing actual teachable content from things like numbered learning outcomes.
    Falls back to the regex heuristic if the call or the parse fails, so outline parsing itself
    is never a hard stop."""
    prompt = f"""You are an outline chapter extractor for a university course lecture-note generator.

You will be given raw, possibly badly formatted, pasted course outline or syllabus text (it may
have no line breaks, run-on sentences, or mixed sections). Your job is to identify the actual
TEACHABLE CHAPTERS the lecture notes should be organized into.

Course outlines typically mix together things like:
- Learning Outcomes (a numbered list of things students should be able to do) — these describe
  goals, NOT chapters. Do not turn each learning outcome into its own chapter. Never use a
  learning outcome as a chapter title.
- Course Contents / Course Description (the actual topics to be taught) — this is what chapters
  should be built from.

Rules for chapter titles:
- A title must be a SHORT NOUN PHRASE describing a topic, e.g. "Memory Management",
  "Linked Lists", "Process Scheduling", "File Systems". It must NOT start with a verb like
  "discuss", "apply", "implement", "choose", "analyse", "evaluate", "understand", "explain" —
  those are learning outcomes, not chapter titles.
- If the outline only contains learning outcomes and no separate course content, derive the
  chapters FROM THE TOPIC WORDS inside the outcomes. Example: an outcome reading "discuss the
  appropriate use of built-in data structures" becomes a chapter titled "Built-in Data
  Structures", with topic "arrays, dynamic arrays, tuples, dictionaries, sets, hash maps".
- Strip trailing punctuation (semicolons, commas, 'and', 'or') from titles.
- Capitalize titles as proper nouns.
- Never copy a sentence fragment verbatim as a title.

Break the taught content into a logical sequence of chapters. Group closely related short topics
into one chapter; split large topic lists into multiple chapters where they're distinct enough to
teach separately. Order chapters in a sensible teaching sequence (foundational topics first).

Respond with ONLY a JSON object, no markdown fences, no commentary, in exactly this shape:
{{
  "chapters": [
    {{"number": 1, "title": "Short chapter title", "topic": "Comma-separated subtopics this chapter covers"}},
    {{"number": 2, "title": "...", "topic": "..."}}
  ]
}}

Raw outline text:
---
{text}
---
"""
    try:
        resp = call_llm(prompt, temperature=0.0)
        data = _extract_json(resp)
        chapters_raw = data.get("chapters") if isinstance(data, dict) else None
        if not isinstance(chapters_raw, list) or not chapters_raw:
            raise ValueError("Response had no usable 'chapters' array")
        chapters: List[Dict[str, Any]] = []
        for i, ch in enumerate(chapters_raw, start=1):
            if not isinstance(ch, dict):
                continue
            raw_num = ch.get("number")
            try:
                num = int(raw_num) if raw_num is not None else i
            except (TypeError, ValueError):
                num = i
            title = normalize_chapter_title(str(ch.get("title") or f"Chapter {num}").strip() or f"Chapter {num}")
            topic = str(ch.get("topic") or "").strip()
            chapters.append({"number": num, "title": title, "topic": topic})
        # De-dupe/sort in case the model skipped or repeated numbers
        seen = set()
        unique: List[Dict[str, Any]] = []
        for ch in sorted(chapters, key=lambda c: c["number"]):
            if ch["number"] in seen:
                continue
            seen.add(ch["number"])
            unique.append(ch)
        if not unique:
            raise ValueError("Chapter list was empty after normalization")
        return unique
    except Exception as e:
        warn(f"LLM-based outline parsing failed ({e}); falling back to basic text parsing. "
             f"Check outline.json afterward — the chapter list may need manual adjustment.")
        return parse_outline_text_to_chapters_heuristic(text)


def parse_outline_file(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Outline file not found: {path}")
    ext = path.suffix.lower()
    text = ""
    if ext == ".json":
        data = load_json(path)
        for chapter in data.get("chapters", []):
            chapter["title"] = normalize_chapter_title(chapter.get("title", ""))
        return data
    elif ext in {".md", ".txt"}:
        text = path.read_text(encoding="utf-8")
    elif ext == ".docx":
        try:
            import docx
        except Exception:
            raise RuntimeError("python-docx is required to parse .docx outlines. Install via requirements.txt")
        doc = docx.Document(str(path))
        text = "\n".join(p.text for p in doc.paragraphs)
    elif ext == ".pdf":
        try:
            from PyPDF2 import PdfReader
        except Exception:
            raise RuntimeError("PyPDF2 is required to parse .pdf outlines. Install via requirements.txt")
        reader = PdfReader(str(path))
        pages = []
        for p in reader.pages:
            try:
                pages.append(p.extract_text() or "")
            except Exception:
                pages.append("")
        text = "\n".join(pages)
    else:
        # attempt to read as text
        text = path.read_text(encoding="utf-8", errors="ignore")
    chapters = parse_outline_text_to_chapters_llm(text)
    outline = {
        "course_code": "",
        "course_title": "",
        "department": "",
        "university": "",
        "academic_session": "",
        "level": "",
        "chapters": chapters,
    }
    return outline


def write_course_profile_from_metadata(path: Path, metadata: Dict[str, Any]) -> None:
    template = textwrap.dedent(f"""
    # Course Profile

    Course Code: {metadata.get('course_code','')}
    Course Title: {metadata.get('course_title','')}
    University: {metadata.get('university','')}
    Department: {metadata.get('department','')}
    Academic Session: {metadata.get('academic_session','')}
    Level: {metadata.get('level','')}
    Prepared by: {metadata.get('prepared_by','')}

    ## Style Rules

    * Use clear academic English.
    * Explain concepts before assuming familiarity.
    * Prefer explanation over memorization.
    * Avoid filler.
    * Do not use em dashes.
    * Use consistent terminology.
    * Do not use conversational language.
    * Do not unnecessarily repeat explanations.
    """)
    path.write_text(template, encoding="utf-8")


def run_pipeline(course_dir_name: str) -> str:
    """Runs terminology -> all chapters -> final QA -> compile for an existing course
    folder, hands-off, then prints the run summary. Used for both fresh and resumed courses."""
    c = Course.load(course_dir_name)
    terminology = load_terminology(c.terminology_path)
    if not terminology.get("terms"):
        cmd_terminology(course_dir_name)
    cmd_all(course_dir_name)
    cmd_qa(course_dir_name)
    cmd_compile(course_dir_name)
    print_run_summary(c)
    return course_dir_name


def interactive_setup() -> str:
    # Welcome box
    _safe_print("╭" + "─" * 42 + "╮")
    _safe_print("│       University Lecture Note Generator  │")
    _safe_print("╰" + "─" * 42 + "╯\n")
    _safe_print("Let's create your lecture note. Press Ctrl+C to exit at any time.\n")
    # Check for resumable courses
    COURSES_DIR.mkdir(exist_ok=True)
    incomplete = []
    for p in COURSES_DIR.iterdir():
        if p.is_dir():
            state_file = p / "generation_state.json"
            if state_file.exists():
                try:
                    st = load_json(state_file)
                    chapters = st.get("chapters", {})
                    total_complete = sum(1 for v in chapters.values() if v in ("complete", "complete_with_warnings"))
                    if chapters and total_complete < max(1, len(chapters)):
                        incomplete.append((p.name, total_complete, len(chapters)))
                except Exception:
                    pass
    if incomplete:
        print("An incomplete course was found:\n")
        for i, (name, done, total) in enumerate(incomplete, start=1):
            print(f"[{i}] {name} — {done} of {total} chapters completed")
        choice = input("Resume an existing course? [y/N]: ").strip().lower()
        if choice == "y" or choice == "yes":
            sel = input("Enter the number of the course to resume: ").strip()
            try:
                idx = int(sel) - 1
                resume_id = incomplete[idx][0]
                print(f"\nResuming {resume_id} — this runs straight through to the end.\n")
                return run_pipeline(resume_id)
            except Exception:
                print("Invalid selection; starting a new course.")
    # New course setup
    course_code = input("Course code: ").strip()
    course_title = input("Course title: ").strip()
    level = input("Your year/level: ").strip()
    prepared_by = input("Your name: ").strip()
    department = input("Department: ").strip()
    academic_session = input("Academic session: ").strip()
    university = input("University: ").strip()
    # Outline input: offer paste option or file path
    outline_path = None
    outline_text: Optional[str] = None
    outline_name = ""
    paste_choice = input("Paste the outline into the terminal instead of providing a file? [y/N]: ").strip().lower()
    if paste_choice in ("y", "yes"):
        print("Paste your outline below. End with a single line containing only END (or Ctrl+D/Ctrl+Z):")
        lines: List[str] = []
        try:
            while True:
                ln = input()
                if ln.strip() == "END":
                    break
                lines.append(ln)
        except EOFError:
            # user finished input with EOF
            pass
        outline_text = "\n".join(lines).strip()
        if not outline_text:
            print("No outline text detected. Please provide an outline file instead.")
            # fall back to file prompt
        else:
            outline_name = "pasted_outline.md"
    if not outline_text:
        # ask for a file path
        while True:
            p = input("Course outline file: ").strip()
            ppath = Path(p)
            if ppath.exists():
                outline_path = ppath
                outline_name = ppath.name
                break
            print(f"File not found: {p}. Please try again.")
    # Source materials (optional)
    print("\nSource materials are optional. The generator works from the course outline and learning outcomes alone.")
    print("If you have slides or readings you want the generator to use, provide a path. Otherwise press Enter to skip.")
    source_materials = None
    while True:
        s = input("Path to file or directory with source materials (leave blank to skip): ").strip()
        if not s:
            # user chose to skip
            break
        spath = Path(s)
        if spath.exists():
            source_materials = spath
            break
        print(f"Path not found: {s}. Enter a valid path or press Enter to skip.")
    # Confirmation
    print("\nCourse\n" + "─" * 40)
    print(f"Code:       {course_code}")
    print(f"Title:      {course_title}")
    print(f"Level:      {level}")
    print(f"Prepared by: {prepared_by}")
    print(f"Department: {department}")
    print(f"University: {university}")
    print(f"Session:    {academic_session}")
    # Outline may have been pasted (outline_path is None). Use outline_name if available.
    outline_display = None
    if 'outline_path' in locals() and outline_path:
        outline_display = outline_path.name
    elif 'outline_name' in locals() and outline_name:
        outline_display = outline_name
    else:
        outline_display = "none provided"
    print(f"Outline:    {outline_display}")
    print("─" * 40)
    ready = input("Ready to generate your lecture note? [Y/n]: ").strip().lower()
    if ready in ("n", "no"):
        print("Aborted by user.")
        sys.exit(0)
    # Create course dir name
    course_dir_name = format_course_dir_name(course_code, course_title)
    course_dir = COURSES_DIR / course_dir_name
    if course_dir.exists():
        print(f"Course directory {course_dir} already exists. Resuming or using existing data.")
    else:
        cmd_init(course_dir_name)
    # Parse outline and write outline.json
    if outline_path:
        parsed_outline = parse_outline_file(outline_path)
    else:
        # outline_text was provided via paste; parse heuristically
        parsed_outline = {
            "course_code": "",
            "course_title": "",
            "department": "",
            "university": "",
            "academic_session": "",
            "level": "",
            "chapters": parse_outline_text_to_chapters_llm(outline_text or "")
        }
    # populate metadata
    parsed_outline["course_code"] = course_code
    parsed_outline["course_title"] = course_title
    parsed_outline["department"] = department
    parsed_outline["university"] = university
    parsed_outline["academic_session"] = academic_session
    parsed_outline["level"] = level
    parsed_outline["prepared_by"] = prepared_by
    # Save outline.json
    (course_dir / "outline.json").write_text(json.dumps(parsed_outline, indent=2, ensure_ascii=False), encoding="utf-8")
    # If outline was pasted, save the original text for provenance
    if not outline_path and outline_text:
        (course_dir / (outline_name or "pasted_outline.md")).write_text(outline_text, encoding="utf-8")
    # Save profile
    write_course_profile_from_metadata(course_dir / "course_profile.md", parsed_outline)
    # Ensure terminology and generation_state exist
    save_json(course_dir / "terminology.json", {"terms": {}})
    save_json(course_dir / "generation_state.json", {"status": "in_progress", "chapters": {}, "final_qa": False, "compiled": False})
    # Copy prompts
    repo_prompts = PROMPTS_DIR
    if repo_prompts.exists():
        for file in repo_prompts.iterdir():
            if file.is_file():
                shutil.copy(file, course_dir / "prompts" / file.name)
    # Optionally copy source materials path into course folder for later use
    if source_materials:
        dest = course_dir / "sources"
        if source_materials.is_dir():
            shutil.copytree(source_materials, dest, dirs_exist_ok=True)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(source_materials, dest / source_materials.name)
    print("\nCourse initialized\n")
    print("Generating your lecture note now — this runs straight through, start to finish.\n")
    return run_pipeline(course_dir_name)


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Lecture note generator CLI")
    subparsers = parser.add_subparsers(dest="cmd")

    sp_init = subparsers.add_parser("init")
    sp_init.add_argument("course_id")

    sp_profile = subparsers.add_parser("profile")
    sp_profile.add_argument("course_id")

    sp_term = subparsers.add_parser("terminology")
    sp_term.add_argument("course_id")

    sp_ch = subparsers.add_parser("chapter")
    sp_ch.add_argument("course_id")
    sp_ch.add_argument("chapter_number", type=int)

    sp_all = subparsers.add_parser("all")
    sp_all.add_argument("course_id")

    sp_qa = subparsers.add_parser("qa")
    sp_qa.add_argument("course_id")

    sp_compile = subparsers.add_parser("compile")
    sp_compile.add_argument("course_id")

    sp_build = subparsers.add_parser("build")
    sp_build.add_argument("course_id")

    args = parser.parse_args(argv)
    # If no command provided, run interactive setup and generation
    if not args.cmd:
        try:
            interactive_setup()
            return
        except KeyboardInterrupt:
            print("\nAborted.")
            sys.exit(1)
    if args.cmd == "init":
        cmd_init(args.course_id)
    elif args.cmd == "profile":
        cmd_profile(args.course_id)
    elif args.cmd == "terminology":
        cmd_terminology(args.course_id)
    elif args.cmd == "chapter":
        cmd_chapter(args.course_id, args.chapter_number)
    elif args.cmd == "all":
        cmd_all(args.course_id)
    elif args.cmd == "qa":
        cmd_qa(args.course_id)
    elif args.cmd == "compile":
        cmd_compile(args.course_id)
    elif args.cmd == "build":
        cmd_build(args.course_id)
    else:
        error(f"Unknown command: {args.cmd}")
        sys.exit(1)

if __name__ == "__main__":
    main()