"""Configuration handling and environment variable parsing for Pseudo-Scribe."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Load .env if python-dotenv is available
try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
COURSES_DIR: Path = REPO_ROOT / "courses"
PROMPTS_DIR: Path = REPO_ROOT / "prompts"


def get_courses_dir() -> Path:
    """Return configured courses directory, respecting generate.COURSES_DIR monkeypatches."""
    import sys

    gen_mod = sys.modules.get("generate")
    if gen_mod and hasattr(gen_mod, "COURSES_DIR"):
        return Path(gen_mod.COURSES_DIR)
    return COURSES_DIR


def get_prompts_dir() -> Path:
    """Return configured prompts directory, respecting generate.PROMPTS_DIR monkeypatches."""
    import sys

    gen_mod = sys.modules.get("generate")
    if gen_mod and hasattr(gen_mod, "PROMPTS_DIR"):
        return Path(gen_mod.PROMPTS_DIR)
    return PROMPTS_DIR


def _get_bool(key: str, default: str = "0", alt_key: str | None = None) -> bool:
    val = os.getenv(key)
    if val is None and alt_key:
        val = os.getenv(alt_key)
    if val is None:
        val = default
    return val.strip().lower() in ("1", "true", "yes")


def _get_int(key: str, default: int, min_val: int | None = None) -> int:
    val = os.getenv(key)
    if val is None:
        return default
    try:
        parsed = int(val)
        if min_val is not None:
            return max(min_val, parsed)
        return parsed
    except ValueError:
        return default


def _get_float(key: str, default: float) -> float:
    val = os.getenv(key)
    if val is None:
        return default
    try:
        return float(val)
    except ValueError:
        return default


@dataclass
class Settings:
    """Strongly-typed runtime configuration snapshot."""

    llm_provider: str
    llm_model: str
    llm_temperature: float
    max_chapter_retries: int
    max_api_retries: int
    api_retry_base_delay: float
    max_auto_retry_wait_seconds: float
    final_qa_excerpt_chars: int
    heavy_call_provider: str | None
    enable_chapter_enhance: bool
    enable_conflict_check: bool
    max_concurrent_chapters: int
    http_timeout_seconds: float
    dry_run: bool

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            llm_provider=os.getenv("LLM_PROVIDER", "gemini").lower(),
            llm_model=os.getenv("LLM_MODEL", "gemini-3.6-flash"),
            llm_temperature=_get_float("LLM_TEMPERATURE", 0.2),
            max_chapter_retries=_get_int("MAX_CHAPTER_RETRIES", 2, min_val=1),
            max_api_retries=_get_int("MAX_API_RETRIES", 5, min_val=0),
            api_retry_base_delay=_get_float("API_RETRY_BASE_DELAY", 2.0),
            max_auto_retry_wait_seconds=_get_float("MAX_AUTO_RETRY_WAIT_SECONDS", 90.0),
            final_qa_excerpt_chars=_get_int("FINAL_QA_EXCERPT_CHARS", 1000, min_val=100),
            heavy_call_provider=os.getenv("HEAVY_CALL_PROVIDER"),
            enable_chapter_enhance=_get_bool("ENABLE_CHAPTER_ENHANCE", "0", alt_key="ENABLE_CHAPTER_QA"),
            enable_conflict_check=_get_bool("ENABLE_CONFLICT_CHECK", "0"),
            max_concurrent_chapters=_get_int("MAX_CONCURRENT_CHAPTERS", 1, min_val=1),
            http_timeout_seconds=_get_float("HTTP_TIMEOUT_SECONDS", 180.0),
            dry_run=_get_bool("DRY_RUN", "0"),
        )


# Backward-compatible module-level globals mirroring original generate.py
LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "gemini").lower()
LLM_MODEL: str = os.getenv("LLM_MODEL", "gemini-3.6-flash")
LLM_TEMPERATURE: float = _get_float("LLM_TEMPERATURE", 0.2)
MAX_CHAPTER_RETRIES: int = _get_int("MAX_CHAPTER_RETRIES", 2)
MAX_API_RETRIES: int = _get_int("MAX_API_RETRIES", 5)
API_RETRY_BASE_DELAY: float = _get_float("API_RETRY_BASE_DELAY", 2.0)
MAX_AUTO_RETRY_WAIT_SECONDS: float = _get_float("MAX_AUTO_RETRY_WAIT_SECONDS", 90.0)
FINAL_QA_EXCERPT_CHARS: int = _get_int("FINAL_QA_EXCERPT_CHARS", 1000)
HEAVY_CALL_PROVIDER: str | None = os.getenv("HEAVY_CALL_PROVIDER")
ENABLE_CHAPTER_ENHANCE: bool = _get_bool("ENABLE_CHAPTER_ENHANCE", "0", alt_key="ENABLE_CHAPTER_QA")
ENABLE_CONFLICT_CHECK: bool = _get_bool("ENABLE_CONFLICT_CHECK", "0")
MAX_CONCURRENT_CHAPTERS: int = _get_int("MAX_CONCURRENT_CHAPTERS", 1, min_val=1)
HTTP_TIMEOUT_SECONDS: float = _get_float("HTTP_TIMEOUT_SECONDS", 180.0)
DRY_RUN: bool = _get_bool("DRY_RUN", "0")
