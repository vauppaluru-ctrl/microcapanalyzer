"""Shared helpers: rate limiting, text truncation, retry logic."""

import time
import math
from typing import Any, Callable, TypeVar

T = TypeVar("T")

SEC_RATE_LIMIT_SLEEP = 0.15  # seconds between SEC requests
MAX_DOC_CHARS = 8000


def sec_sleep():
    time.sleep(SEC_RATE_LIMIT_SLEEP)


def truncate(text: str, max_chars: int = MAX_DOC_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n... [TRUNCATED — {len(text) - max_chars} additional chars omitted]"


def retry_with_backoff(
    fn: Callable[[], T],
    max_retries: int = 3,
    base_delay: float = 1.0,
    label: str = "",
) -> T:
    """Exponential backoff retry. Raises on final failure."""
    for attempt in range(max_retries):
        try:
            return fn()
        except Exception as exc:
            if attempt == max_retries - 1:
                raise
            delay = base_delay * math.pow(2, attempt)
            if label:
                print(f"[retry] {label} failed (attempt {attempt + 1}): {exc}. Retrying in {delay:.1f}s…")
            time.sleep(delay)
    raise RuntimeError("unreachable")


def fmt_millions(n: float | None) -> str:
    if n is None:
        return "N/A"
    if n >= 1e9:
        return f"${n / 1e9:.2f}B"
    if n >= 1e6:
        return f"${n / 1e6:.1f}M"
    return f"${n:,.0f}"


def fmt_shares(n: float | None) -> str:
    if n is None:
        return "N/A"
    if n >= 1e9:
        return f"{n / 1e9:.2f}B"
    if n >= 1e6:
        return f"{n / 1e6:.1f}M"
    if n >= 1e3:
        return f"{n / 1e3:.1f}K"
    return f"{n:,.0f}"


def fmt_pct(n: float | None, decimals: int = 1) -> str:
    if n is None:
        return "N/A"
    return f"{n:.{decimals}f}%"


def safe_div(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return a / b
