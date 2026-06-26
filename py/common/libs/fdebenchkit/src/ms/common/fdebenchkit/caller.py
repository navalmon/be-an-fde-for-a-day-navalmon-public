"""Batched async HTTP caller with retries, timeouts, and latency tracking.

Calls a participant endpoint for each eval item in the configured task.
Runs ``concurrency`` requests in parallel using asyncio.Semaphore.
Records per-call latency for efficiency scoring.

Fairness features:
  * **Warm-up phase**: sends throwaway requests before timing to eliminate
    TCP/TLS handshake and model cold-start bias.
  * **Trimmed percentiles**: excludes top/bottom 5 % latency outliers so a
    single spike doesn't dominate the score.
  * **Cost header extraction**: reads ``X-Model-Name``, ``X-Prompt-Tokens``,
    ``X-Completion-Tokens`` from each response for measured cost scoring.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from dataclasses import field
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def _safe_int(value: str | None, default: int = 0) -> int:
    """Safely parse a string to int, returning default on failure."""
    if not value:
        return default
    try:
        return int(value.strip())
    except (ValueError, AttributeError):
        logger.warning("invalid_header_int: value=%r using_default=%d", value, default)
        return default


# Default timeout per individual HTTP call (seconds).
DEFAULT_TIMEOUT = 30.0
# Default concurrency (max parallel requests).
DEFAULT_CONCURRENCY = 10
# Default retry count for transient failures.
DEFAULT_MAX_RETRIES = 2
# Retry delay base (exponential backoff).
_RETRY_BASE_DELAY = 1.0
# Default warm-up request count.
DEFAULT_WARM_UP_REQUESTS = 3
# Default trim percentage for latency outliers.
DEFAULT_TRIM_PCT = 5.0
# Max response body size in bytes (10 MB). Protects against OOM from
# malicious or buggy participant APIs returning unbounded payloads.
MAX_RESPONSE_BYTES = 10 * 1024 * 1024
# Circuit breaker: consecutive failures before aborting remaining items.
# Prevents wasting minutes of timeout budget on a dead API.
DEFAULT_CIRCUIT_BREAKER_THRESHOLD = 10

# Default warm-up request
# A minimal valid Task 1 request used when the caller is invoked without
# a task-specific warm-up payload.
_DEFAULT_WARMUP_REQUEST: dict[str, Any] = {
    "ticket_id": "__warmup__",
    "subject": "System warm-up request",
    "description": "Warm-up request to establish connections. Please classify normally.",
    "reporter": {"name": "System", "email": "system@contoso.com", "department": "Mission Ops"},
    "created_at": "2026-01-01T00:00:00Z",
    "channel": "bridge_terminal",
    "attachments": [],
}


@dataclass
class TicketResult:
    """Result of calling the participant's API for a single ticket."""

    ticket_id: str
    response: dict[str, Any] | None = None
    latency_ms: float = 0.0
    error: str | None = None
    retries: int = 0
    # Cost tracking from response headers
    model_name: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0


def _trimmed_percentile(values: list[float], percentile: float, *, trim_pct: float = DEFAULT_TRIM_PCT) -> float:
    """Compute a percentile after symmetrically trimming outliers.

    Args:
        values: List of values (will be sorted internally).
        percentile: Target percentile (0–100), e.g. 50 for median.
        trim_pct: Percentage to trim from each end (0–49).

    Returns:
        The percentile value of the trimmed distribution, or 0.0 if empty.
    """
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    trim_count = int(n * trim_pct / 100)
    trimmed = sorted_vals[trim_count:-trim_count] if trim_count > 0 and 2 * trim_count < n else sorted_vals
    if not trimmed:
        return 0.0
    idx = int(len(trimmed) * percentile / 100)
    return trimmed[min(idx, len(trimmed) - 1)]


@dataclass
class CallResults:
    """Aggregate results from calling all tickets."""

    results: list[TicketResult] = field(default_factory=list)
    total_latency_ms: float = 0.0
    errors: int = 0

    @property
    def latency_p50_ms(self) -> float:
        """Trimmed median latency across successful calls (excludes top/bottom 5%)."""
        latencies = [r.latency_ms for r in self.results if r.response is not None]
        return _trimmed_percentile(latencies, 50)

    @property
    def latency_p95_ms(self) -> float:
        """Trimmed 95th percentile latency across successful calls."""
        latencies = [r.latency_ms for r in self.results if r.response is not None]
        return _trimmed_percentile(latencies, 95)

    @property
    def total_prompt_tokens(self) -> int:
        """Sum of prompt tokens across all successful calls."""
        return sum(r.prompt_tokens for r in self.results if r.response is not None)

    @property
    def total_completion_tokens(self) -> int:
        """Sum of completion tokens across all successful calls."""
        return sum(r.completion_tokens for r in self.results if r.response is not None)

    @property
    def primary_model(self) -> str:
        """Most frequently reported model name (empty string if none reported)."""
        models = [r.model_name for r in self.results if r.model_name]
        if not models:
            return ""
        # Return the most common model name
        from collections import (  # noqa: PLC0415 # deferred import to avoid top-level cost for rarely-used helper
            Counter,
        )

        return Counter(models).most_common(1)[0][0]


async def call_endpoint(
    endpoint_url: str,
    items: list[dict[str, Any]],
    *,
    endpoint_path: str = "/triage",
    identifier_field: str = "ticket_id",
    concurrency: int = DEFAULT_CONCURRENCY,
    timeout: float = DEFAULT_TIMEOUT,
    max_retries: int = DEFAULT_MAX_RETRIES,
    warm_up_requests: int = DEFAULT_WARM_UP_REQUESTS,
    warm_up_payload: dict[str, Any] | None = None,
    circuit_breaker_threshold: int = DEFAULT_CIRCUIT_BREAKER_THRESHOLD,
) -> CallResults:
    """Call a participant endpoint for each eval item with batched concurrency.

    Fairness features:
      1. Sends ``warm_up_requests`` throwaway requests first to eliminate
         TCP/TLS handshake and model cold-start bias from the timed set.
      2. Extracts ``X-Model-Name``, ``X-Prompt-Tokens``, ``X-Completion-Tokens``
         response headers for measured cost scoring.
      3. Uses trimmed percentiles (top/bottom 5%) to exclude outlier spikes.

    Circuit breaker:
      After ``circuit_breaker_threshold`` consecutive failures (reset on any
      success), remaining requests are cancelled immediately. This prevents
      wasting minutes of timeout budget on a dead API. Set to 0 to disable.

    Args:
        endpoint_url: Base URL of participant's API.
        items: List of request payloads to send.
        endpoint_path: Relative endpoint path such as ``/triage`` or ``/extract``.
        identifier_field: Stable request identifier key used to tag failures.
        concurrency: Max parallel HTTP requests.
        timeout: Per-request timeout in seconds.
        max_retries: Number of retries for transient failures (5xx, timeout).
        warm_up_requests: Number of throwaway requests before timed scoring.
        warm_up_payload: Optional task-specific request payload for warm-up calls.
        circuit_breaker_threshold: Consecutive failures before aborting. 0 = disabled.

    Returns:
        CallResults with per-ticket responses, latencies, and error counts.
    """
    endpoint = endpoint_path if endpoint_path.startswith("/") else f"/{endpoint_path}"
    request_url = endpoint_url.rstrip("/") + endpoint
    semaphore = asyncio.Semaphore(concurrency)
    results = CallResults()
    payload = warm_up_payload or _DEFAULT_WARMUP_REQUEST

    # Circuit breaker state shared across all coroutines in this batch.
    # asyncio is single-threaded so no lock is needed.
    abort_event = asyncio.Event() if circuit_breaker_threshold > 0 else None
    consecutive_failures = [0]  # mutable container for inner closure

    async def _call_with_breaker(item: dict[str, Any]) -> TicketResult:
        """Wrap _call_single with circuit breaker tracking."""
        ticket_result = await _call_single(
            client,
            semaphore,
            request_url,
            item,
            max_retries,
            identifier_field,
            abort_event=abort_event,
        )
        if abort_event is not None and ticket_result.error != "circuit_breaker_open":
            if ticket_result.error is not None:
                consecutive_failures[0] += 1
                if consecutive_failures[0] >= circuit_breaker_threshold:
                    abort_event.set()
                    logger.warning(
                        "circuit_breaker_open: consecutive_failures=%d threshold=%d endpoint=%s",
                        consecutive_failures[0],
                        circuit_breaker_threshold,
                        request_url,
                    )
            else:
                consecutive_failures[0] = 0  # reset on success
        return ticket_result

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
        # Warm-up phase: eliminate cold-start bias
        if warm_up_requests > 0:
            logger.info("warm_up_start: count=%d", warm_up_requests)
            warm_up_tasks = [
                _call_single(client, semaphore, request_url, payload, max_retries, identifier_field)
                for _ in range(warm_up_requests)
            ]
            warm_up_results = await asyncio.gather(*warm_up_tasks)
            warm_up_ok = sum(1 for r in warm_up_results if r.response is not None)
            logger.info("warm_up_done: ok=%d/%d", warm_up_ok, warm_up_requests)

        # Timed scoring phase with circuit breaker
        tasks = [_call_with_breaker(item) for item in items]
        ticket_results = await asyncio.gather(*tasks)

    results.results = list(ticket_results)
    results.errors = sum(1 for r in ticket_results if r.error is not None)
    results.total_latency_ms = sum(r.latency_ms for r in ticket_results)
    return results


async def _call_single(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    url: str,
    item: dict[str, Any],
    max_retries: int,
    identifier_field: str,
    *,
    abort_event: asyncio.Event | None = None,
) -> TicketResult:
    """Call the endpoint for a single request payload with retry logic.

    If *abort_event* is set (circuit breaker tripped), returns immediately
    with ``error="circuit_breaker_open"`` instead of making an HTTP call.
    """
    ticket_id = str(item.get(identifier_field, "unknown"))
    result = TicketResult(ticket_id=ticket_id)

    # Fast-exit before acquiring the semaphore
    if abort_event is not None and abort_event.is_set():
        result.error = "circuit_breaker_open"
        return result

    async with semaphore:
        # Re-check after acquiring; breaker may have tripped while waiting.
        if abort_event is not None and abort_event.is_set():
            result.error = "circuit_breaker_open"
            return result
        for attempt in range(max_retries + 1):
            start = time.monotonic()
            try:
                resp = await client.post(url, json=item)
                elapsed_ms = (time.monotonic() - start) * 1000
                result.latency_ms = elapsed_ms
                result.retries = attempt

                if resp.status_code == 200:
                    # Guard against oversized responses (OOM protection)
                    content_length = resp.headers.get("content-length")
                    if content_length and _safe_int(content_length) > MAX_RESPONSE_BYTES:
                        result.error = f"response_too_large: {content_length} bytes"
                        return result
                    if len(resp.content) > MAX_RESPONSE_BYTES:
                        result.error = f"response_too_large: {len(resp.content)} bytes"
                        return result

                    result.response = resp.json()
                    # Extract cost-tracking headers (safely parse ints)
                    result.model_name = resp.headers.get("X-Model-Name", "")
                    result.prompt_tokens = _safe_int(resp.headers.get("X-Prompt-Tokens"))
                    result.completion_tokens = _safe_int(resp.headers.get("X-Completion-Tokens"))
                    return result

                # Retryable: 5xx or 429
                if (resp.status_code >= 500 or resp.status_code == 429) and attempt < max_retries:
                    delay = _RETRY_BASE_DELAY * (2**attempt)
                    logger.warning(
                        "ticket_retry: ticket=%s status=%d attempt=%d delay=%.1fs",
                        ticket_id,
                        resp.status_code,
                        attempt + 1,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue

                # Non-retryable error
                result.error = f"HTTP {resp.status_code}"
                return result

            except httpx.TimeoutException:
                elapsed_ms = (time.monotonic() - start) * 1000
                result.latency_ms = elapsed_ms
                result.retries = attempt
                if attempt < max_retries:
                    delay = _RETRY_BASE_DELAY * (2**attempt)
                    logger.warning("ticket_timeout: ticket=%s attempt=%d", ticket_id, attempt + 1)
                    await asyncio.sleep(delay)
                    continue
                result.error = "timeout"
                return result

            except httpx.HTTPError as exc:
                elapsed_ms = (time.monotonic() - start) * 1000
                result.latency_ms = elapsed_ms
                result.retries = attempt
                result.error = str(exc)
                return result

    return result


async def check_health(endpoint_url: str, *, timeout: float = 10.0) -> bool:
    """Verify the participant's API is reachable via GET /health."""
    health_url = endpoint_url.rstrip("/") + "/health"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
            resp = await client.get(health_url)
            return resp.status_code == 200
    except httpx.HTTPError:
        return False
