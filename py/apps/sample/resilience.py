"""Small resilience helpers shared by task implementations."""

import asyncio
import logging
import math
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Iterable
from datetime import UTC
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import TypeVar

import httpx

logger = logging.getLogger(__name__)

T = TypeVar("T")


def retry_after_seconds(headers: httpx.Headers) -> float | None:
    """Parse a Retry-After or Retry-After-Ms header value in seconds, if present and valid."""
    retry_after_ms = headers.get("Retry-After-Ms")
    if retry_after_ms is not None:
        try:
            milliseconds = float(retry_after_ms)
        except ValueError:
            logger.warning("invalid_retry_after_ms_header: value=%r", retry_after_ms)
        else:
            if math.isfinite(milliseconds):
                return max(milliseconds / 1000, 0.0)
            else:
                logger.warning("invalid_retry_after_ms_header: value=%r", retry_after_ms)

    value = headers.get("Retry-After")
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            logger.warning("invalid_retry_after_header: value=%r", value)
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        return max((retry_at - datetime.now(UTC)).total_seconds(), 0.0)
    if not math.isfinite(seconds):
        logger.warning("invalid_retry_after_header: value=%r", value)
        return None
    return max(seconds, 0.0)


def _is_retryable_exception(exc: Exception, retryable_exceptions: tuple[type[Exception], ...]) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        return status_code == 429 or status_code >= 500
    return isinstance(exc, retryable_exceptions)


def _retry_delay_seconds(exc: Exception, *, base_delay_seconds: float, attempt: int, max_delay_seconds: float) -> float:
    if isinstance(exc, httpx.HTTPStatusError):
        retry_after = retry_after_seconds(exc.response.headers)
        if retry_after is not None:
            return min(retry_after, max_delay_seconds)
    return min(base_delay_seconds * (2 ** (attempt - 1)), max_delay_seconds)


async def run_with_retries(
    operation: Callable[[], Awaitable[T]],
    *,
    max_attempts: int,
    base_delay_seconds: float,
    max_delay_seconds: float = 10.0,
    retryable_exceptions: Iterable[type[Exception]] = (
        httpx.TimeoutException,
        httpx.TransportError,
        httpx.HTTPStatusError,
    ),
) -> T:
    """Run an async operation with bounded retries for transient failures."""
    retryable = tuple(retryable_exceptions)
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            return await operation()
        except Exception as exc:
            if not _is_retryable_exception(exc, retryable):
                raise
            last_error = exc
            if attempt == max_attempts:
                break
            delay = _retry_delay_seconds(
                exc,
                base_delay_seconds=base_delay_seconds,
                attempt=attempt,
                max_delay_seconds=max_delay_seconds,
            )
            logger.warning(
                "retryable_operation_failed: attempt=%d max_attempts=%d retry_in_seconds=%.2f error=%s",
                attempt,
                max_attempts,
                delay,
                type(exc).__name__,
            )
            await asyncio.sleep(delay)

    if last_error is None:
        msg = "retry operation exhausted without capturing an exception"
        raise RuntimeError(msg)
    raise last_error
