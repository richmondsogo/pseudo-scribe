"""LLM provider integration (Gemini, Groq, OpenRouter, DeepSeek) and dry-run mocks."""

from __future__ import annotations

import json
import os
import re

from pseudoscribe import models
from pseudoscribe.config import (
    HTTP_TIMEOUT_SECONDS,
    LLM_PROVIDER,
    LLM_TEMPERATURE,
)
from pseudoscribe.retries import call_with_retry


def _call_gemini(prompt: str, model: str | None, temperature: float) -> str:
    """Call Google Gemini via the google-genai SDK."""
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
        raise RuntimeError(f"Gemini (google.genai) call failed: {e}") from e

    out = getattr(resp, "text", None)
    if not out:
        out = str(resp)
    return out


def _call_compatible_chat_completions(
    provider_label: str,
    url: str,
    api_key: str,
    model: str,
    prompt: str,
    temperature: float,
    extra_headers: dict[str, str] | None = None,
) -> str:
    """Shared HTTP client for OpenAI-compatible chat completion endpoints."""
    body_dict = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) PseudoScribe/1.0",
    }
    if extra_headers:
        headers.update(extra_headers)

    timeout = float(os.getenv("HTTP_TIMEOUT_SECONDS", str(HTTP_TIMEOUT_SECONDS)))

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
                raise RuntimeError(msg) from e
            except _requests.exceptions.RequestException as e:
                raise RuntimeError(f"{provider_label} request failed: {e}") from e
        else:
            import urllib.error
            import urllib.request

            req = urllib.request.Request(
                url,
                data=json.dumps(body_dict).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout) as http_resp:
                    data = json.loads(http_resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8", errors="ignore")
                retry_after = e.headers.get("Retry-After") if e.headers else None
                msg = f"{e.code} {e.reason}. {err_body}"
                if retry_after:
                    msg += f" | Retry-After header: {retry_after}s"
                raise RuntimeError(msg) from e
            except (urllib.error.URLError, OSError, TimeoutError) as e:
                reason = getattr(e, "reason", e)
                raise RuntimeError(f"{provider_label} request failed: {reason}") from e

        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(f"Unexpected {provider_label} response shape: {data}") from e

    try:
        return call_with_retry(do_request)
    except Exception as e:
        raise RuntimeError(f"{provider_label} call failed: {e}") from e


def _call_groq(prompt: str, model: str | None, temperature: float) -> str:
    """Call Groq API using Llama or other open models."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY must be set to use the groq provider")
    gmodel = model or os.getenv("GROQ_MODEL") or "llama-3.3-70b-versatile"
    return _call_compatible_chat_completions(
        "Groq",
        "https://api.groq.com/openai/v1/chat/completions",
        api_key,
        gmodel,
        prompt,
        temperature,
    )


def _call_openrouter(prompt: str, model: str | None, temperature: float) -> str:
    """Call OpenRouter unified gateway."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY must be set to use the openrouter provider")
    gmodel = model or os.getenv("OPENROUTER_MODEL") or "openrouter/free"
    extra_headers = {
        "HTTP-Referer": os.getenv("OPENROUTER_SITE_URL", "https://github.com/richmondsogo/Pseudo-Scribe"),
        "X-Title": os.getenv("OPENROUTER_SITE_NAME", "Pseudo-Scribe"),
    }
    return _call_compatible_chat_completions(
        "OpenRouter",
        "https://openrouter.ai/api/v1/chat/completions",
        api_key,
        gmodel,
        prompt,
        temperature,
        extra_headers=extra_headers,
    )


def _call_deepseek(prompt: str, model: str | None, temperature: float) -> str:
    """Call DeepSeek Chat or Reasoner API."""
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY must be set to use the deepseek provider")
    dmodel = model or os.getenv("DEEPSEEK_MODEL") or "deepseek-chat"
    return _call_compatible_chat_completions(
        "DeepSeek",
        "https://api.deepseek.com/chat/completions",
        api_key,
        dmodel,
        prompt,
        temperature,
    )


def call_llm(
    prompt: str,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    provider: str | None = None,
) -> str:
    """Dispatch prompt to configured provider or canned dry-run handler."""
    provider = (provider or LLM_PROVIDER).lower()
    with models._LLM_CALL_COUNT_LOCK:
        models.LLM_CALL_COUNT += 1

    temperature = temperature if temperature is not None else LLM_TEMPERATURE

    dry = os.getenv("DRY_RUN", "").lower() in ("1", "true", "yes")
    if dry:
        low = (prompt or "").lower()
        if "terminology conflict checker" in low or "decide whether the two definitions" in low:
            return json.dumps({"conflict": False, "reasoning": "Dry-run: assume no conflict."})

        if "outline chapter extractor" in low:
            return json.dumps(
                {
                    "chapters": [
                        {"number": 1, "title": "Dry-run Chapter One", "topic": "sample topic A, sample topic B"},
                        {"number": 2, "title": "Dry-run Chapter Two", "topic": "sample topic C"},
                        {"number": 3, "title": "Dry-run Chapter Three", "topic": "sample topic D, sample topic E"},
                    ]
                }
            )

        if "terminology extractor" in low or "extract a compact terminology" in low:
            return json.dumps(
                {
                    "terms": {
                        "example_term": {
                            "preferred_term": "example_term",
                            "definition": "An example term used in dry run.",
                            "definition_status": "defined",
                            "introduced_in": 1,
                        }
                    }
                }
            )

        if "you are writing one chapter" in low or "[terminology]" in prompt or "[chapter]" in prompt:
            term_json = {
                "new_term": {
                    "preferred_term": "new_term",
                    "definition": "A dry-run example term.",
                    "definition_status": "defined",
                    "introduced_in": None,
                }
            }
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
            return f"[TERMINOLOGY]\n{json.dumps(term_json)}\n[/TERMINOLOGY]\n\n[CHAPTER]\n{chapter_latex}\n[/CHAPTER]"

        if "you are the editor of a university lecture note series" in low or "chapter to improve:" in low:
            m = re.search(r"Chapter to improve:\s*(.*)", prompt, re.DOTALL)
            if m and m.group(1).strip():
                return m.group(1).strip()
            return "\\section{Overview}\nSample enhanced content in dry-run.\n"

        if "chapter qa assistant" in low or "chapter_markdown" in low:
            return json.dumps({"status": "PASS", "issues": [], "warnings": []})

        if "final-course qa" in low or "final course markdown" in low or "final_course_markdown" in low:
            return json.dumps(
                {
                    "status": "PASS",
                    "critical_issues": [],
                    "warnings": [],
                    "coverage": {"complete": True, "missing": []},
                    "terminology_consistent": True,
                    "cross_chapter_consistent": True,
                }
            )

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
