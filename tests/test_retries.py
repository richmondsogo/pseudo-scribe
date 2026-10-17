"""Characterization tests for transient error detection and retry/backoff logic."""

import pytest
import time
from unittest.mock import patch
import generate


def test_is_transient_error():
    assert generate._is_transient_error(Exception("503 Service Unavailable")) is True
    assert generate._is_transient_error(Exception("429 Too Many Requests")) is True
    assert generate._is_transient_error(Exception("Resource_exhausted quota exceeded")) is True
    assert generate._is_transient_error(Exception("Connection reset by peer")) is True
    assert generate._is_transient_error(Exception("Request timed out")) is True

    # Non-transient errors
    assert generate._is_transient_error(Exception("Invalid API key")) is False
    assert generate._is_transient_error(ValueError("Bad arguments")) is False
    assert generate._is_transient_error(Exception("401 Unauthorized")) is False


def test_parse_retry_after_seconds():
    # Gemini retryDelay format
    assert generate._parse_retry_after_seconds("'retryDelay': '12.5s'") == 12.5
    assert generate._parse_retry_after_seconds('"retryDelay": "5s"') == 5.0

    # HTTP Retry-After header format
    assert generate._parse_retry_after_seconds("Some error | Retry-After header: 45s") == 45.0

    # Phrasing: try again in XhYmZs
    assert generate._parse_retry_after_seconds("Please try again in 5s") == 5.0
    assert generate._parse_retry_after_seconds("Please try again in 2m30s") == 150.0
    assert generate._parse_retry_after_seconds("Please try again in 1h10m5s") == 4205.0

    # No recognizable wait
    assert generate._parse_retry_after_seconds("Generic error occurred") is None


def test_call_with_retry_immediate_success():
    call_count = 0

    def fn():
        nonlocal call_count
        call_count += 1
        return "success"

    result = generate.call_with_retry(fn)
    assert result == "success"
    assert call_count == 1


def test_call_with_retry_succeeds_after_transient_error():
    attempts = 0

    def fn():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise Exception("503 Service Temporarily Unavailable")
        return "recovered"

    with patch.object(time, "sleep", return_value=None):
        result = generate.call_with_retry(fn, max_retries=5, base_delay=0.01)

    assert result == "recovered"
    assert attempts == 3


def test_call_with_retry_fails_immediately_on_non_transient():
    attempts = 0

    def fn():
        nonlocal attempts
        attempts += 1
        raise ValueError("Invalid authentication key")

    with pytest.raises(ValueError, match="Invalid authentication key"):
        generate.call_with_retry(fn, max_retries=5)

    assert attempts == 1


def test_call_with_retry_exceeds_max_auto_retry_wait():
    def fn():
        raise Exception("Rate limit exceeded: please try again in 25m0s")

    with pytest.raises(RuntimeError, match="longer than this run will wait automatically"):
        generate.call_with_retry(fn, max_retries=5)
