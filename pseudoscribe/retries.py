"""Transient error detection and retry/backoff strategies for network and provider API calls."""

from __future__ import annotations

import random
import re
import time
from collections.abc import Callable
from typing import TypeVar

from pseudoscribe.config import (
    API_RETRY_BASE_DELAY,
    MAX_API_RETRIES,
    MAX_AUTO_RETRY_WAIT_SECONDS,
)
from pseudoscribe.utils import info

T = TypeVar("T")


def _is_transient_error(exc: Exception) -> bool:
    """Determine whether an error is transient (e.g. rate limit, connection reset, timeout)."""
    msg = str(exc).lower()
    transient_markers = (
        "500",
        "internal",
        "502",
        "503",
        "504",
        "unavailable",
        "429",
        "rate limit",
        "resource_exhausted",
        "quota",
        "timeout",
        "timed out",
        "deadline",
        "connection",
        "reset",
    )
    return any(marker in msg for marker in transient_markers)


def _parse_retry_after_seconds(error_text: str) -> float | None:
    """Extract server-suggested delay (in seconds) from Gemini, Groq, or HTTP headers."""
    text = error_text or ""
    m = re.search(r"retryDelay['\"]?\s*:\s*['\"](\d+(?:\.\d+)?)s['\"]", text)
    if m:
        return float(m.group(1))

    m = re.search(r"Retry-After header:\s*(\d+(?:\.\d+)?)s", text, re.IGNORECASE)
    if m:
        return float(m.group(1))

    m = re.search(
        r"try again in\s+(?:(\d+)h)?(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s)?",
        text,
        re.IGNORECASE,
    )
    if m and any(m.groups()):
        total = float(m.group(1) or 0) * 3600 + float(m.group(2) or 0) * 60 + float(m.group(3) or 0)
        if total > 0:
            return total

    return None


def call_with_retry(
    fn: Callable[[], T],
    max_retries: int = MAX_API_RETRIES,
    base_delay: float = API_RETRY_BASE_DELAY,
) -> T:
    """Execute a callable with exponential backoff on transient network or API errors.

    If the provider specifies a wait period that exceeds MAX_AUTO_RETRY_WAIT_SECONDS,
    this function raises immediately to allow the user to resume later.
    """
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
                        f"{e} | The provider says to retry in ~{suggested / 60:.1f} min (around "
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
