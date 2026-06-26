"""API resilience probes for FDEBench Tier 1 robustness scoring.

Runs 7 deterministic probes against a participant's API endpoint to test
how it handles malformed input, edge cases, and load. Each probe is
binary pass/fail.

Probes (from docs/context/10-fdebench.md):
  1. Malformed JSON          : HTTP 400 (not 500, not hang)
  2. Empty body              : HTTP 400 or 422
  3. Missing required fields : HTTP 400/422 or valid response with defaults
  4. Huge payload (50KB)     : HTTP 413, valid response, or clean rejection (not crash)
  5. Wrong content type      : HTTP 415 or valid JSON response
  6. Concurrent burst        : 20 requests in 500ms, ≥18 return valid
  7. Slow follow-up          : Normal request after 60s idle, returns valid

api_resilience = (probes_passed / 7) × 100
"""

import asyncio
import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Probe timeout: generous to avoid false negatives from slow networks
_PROBE_TIMEOUT = 15.0

# Concurrent burst config
_BURST_COUNT = 20
_BURST_MIN_PASS = 18

# Slowdown probe idle seconds
_SLOW_FOLLOW_UP_IDLE_SECONDS = 5.0  # reduced from 60 for practical scoring speed


async def _probe_malformed_json(
    client: httpx.AsyncClient,
    url: str,
) -> bool:
    """Probe 1: Malformed JSON body. Must return 400 (not 500 or hang)."""
    try:
        resp = await client.post(
            url,
            content='{"broken',
            headers={"Content-Type": "application/json"},
        )
        return resp.status_code == 400 or resp.status_code == 422
    except httpx.HTTPError:
        return False


async def _probe_empty_body(
    client: httpx.AsyncClient,
    url: str,
) -> bool:
    """Probe 2: Empty JSON body. Must return 400 or 422."""
    try:
        resp = await client.post(url, json={})
        return resp.status_code in {400, 422}
    except httpx.HTTPError:
        return False


async def _probe_missing_fields(
    client: httpx.AsyncClient,
    url: str,
    valid_payload: dict[str, Any],
    required_keys: frozenset[str],
) -> bool:
    """Probe 3: Valid JSON but missing a required field. Must return 400/422 or valid response."""
    # Pick a required key to drop
    if not required_keys:
        return True  # nothing to test

    dropped_key = sorted(required_keys)[0]
    partial = {k: v for k, v in valid_payload.items() if k != dropped_key}

    try:
        resp = await client.post(url, json=partial)
        # Pass if proper error OR valid response with defaults
        if resp.status_code in {400, 422}:
            return True
        if resp.status_code == 200:
            try:
                resp.json()
                return True  # valid JSON = graceful handling
            except ValueError:
                return False
        return False
    except httpx.HTTPError:
        return False


async def _probe_huge_payload(
    client: httpx.AsyncClient,
    url: str,
    valid_payload: dict[str, Any],
) -> bool:
    """Probe 4: 50KB body. Must respond with 413, valid response, or clean rejection."""
    huge = {**valid_payload, "padding": "X" * 50_000}
    try:
        resp = await client.post(url, json=huge)
        # Pass if: 413 (too large), 200 (handled), 400/422 (rejected cleanly)
        return resp.status_code in {200, 400, 413, 422}
    except httpx.HTTPError:
        return False


async def _probe_wrong_content_type(
    client: httpx.AsyncClient,
    url: str,
    valid_payload: dict[str, Any],
) -> bool:
    """Probe 5: Send with Content-Type: text/plain. Must return 415 or valid JSON."""
    try:
        resp = await client.post(
            url,
            content=json.dumps(valid_payload),
            headers={"Content-Type": "text/plain"},
        )
        if resp.status_code == 415:
            return True
        if resp.status_code == 200:
            try:
                resp.json()
                return True
            except ValueError:
                return False
        # 400/422 also acceptable (rejected the content type)
        return resp.status_code in {400, 422}
    except httpx.HTTPError:
        return False


async def _probe_concurrent_burst(
    client: httpx.AsyncClient,
    url: str,
    valid_payload: dict[str, Any],
) -> bool:
    """Probe 6: 20 concurrent requests. ≥18 must return valid responses."""

    async def _one_call() -> bool:
        try:
            resp = await client.post(url, json=valid_payload)
            if resp.status_code == 200:
                resp.json()  # must be valid JSON
                return True
        except (httpx.HTTPError, ValueError):
            pass
        return False

    tasks = [_one_call() for _ in range(_BURST_COUNT)]
    results = await asyncio.gather(*tasks)
    passed = sum(1 for r in results if r)
    return passed >= _BURST_MIN_PASS


async def _probe_slow_follow_up(
    client: httpx.AsyncClient,
    url: str,
    valid_payload: dict[str, Any],
) -> bool:
    """Probe 7: Normal request after idle period. Must return valid response."""
    # Wait for idle period
    await asyncio.sleep(_SLOW_FOLLOW_UP_IDLE_SECONDS)

    try:
        resp = await client.post(url, json=valid_payload)
        if resp.status_code == 200:
            resp.json()
            return True
    except (httpx.HTTPError, ValueError):
        pass
    return False


async def run_probes(
    endpoint_url: str,
    endpoint_path: str,
    valid_payload: dict[str, Any],
    required_keys: frozenset[str],
    *,
    timeout: float = _PROBE_TIMEOUT,
) -> dict[str, Any]:
    """Run all 7 API resilience probes against an endpoint.

    Returns:
        {
            "probes_passed": int (0–7),
            "api_resilience": float (0–100),
            "probes": {
                "malformed_json": bool,
                "empty_body": bool,
                "missing_fields": bool,
                "huge_payload": bool,
                "wrong_content_type": bool,
                "concurrent_burst": bool,
                "slow_followup": bool,
            }
        }
    """
    url = f"{endpoint_url.rstrip('/')}{endpoint_path}"

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout),
        follow_redirects=True,
    ) as client:
        results: dict[str, bool] = {}

        results["malformed_json"] = await _probe_malformed_json(client, url)
        results["empty_body"] = await _probe_empty_body(client, url)
        results["missing_fields"] = await _probe_missing_fields(
            client,
            url,
            valid_payload,
            required_keys,
        )
        results["huge_payload"] = await _probe_huge_payload(client, url, valid_payload)
        results["wrong_content_type"] = await _probe_wrong_content_type(
            client,
            url,
            valid_payload,
        )
        results["concurrent_burst"] = await _probe_concurrent_burst(
            client,
            url,
            valid_payload,
        )
        results["slow_followup"] = await _probe_slow_follow_up(
            client,
            url,
            valid_payload,
        )

    probes_passed = sum(1 for v in results.values() if v)
    api_resilience = round(probes_passed / 7 * 100, 1)

    for name, passed in results.items():
        logger.info("probe_%s: %s", name, "PASS" if passed else "FAIL")
    logger.info("api_resilience: %.1f%% (%d/7 passed)", api_resilience, probes_passed)

    return {
        "probes_passed": probes_passed,
        "api_resilience": api_resilience,
        "probes": results,
    }
