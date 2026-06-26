"""Tests for shared FastAPI app foundation behavior."""

from datetime import UTC
from datetime import datetime
from datetime import timedelta
from email.utils import format_datetime

import httpx
import pytest
from config import Settings
from fastapi.testclient import TestClient
from main import MODEL_HEADER_NAME
from main import create_app
from resilience import _retry_delay_seconds
from resilience import retry_after_seconds
from resilience import run_with_retries


def _triage_payload() -> dict:
    return {
        "ticket_id": "SIG-1001",
        "subject": "Briefing request",
        "description": "Please prepare a routine mission briefing for tomorrow.",
        "reporter": {
            "name": "Avery Quinn",
            "email": "avery.quinn@cdss.space",
            "department": "Mission Ops",
        },
        "created_at": "2026-01-01T00:00:00Z",
        "channel": "bridge_terminal",
        "attachments": [],
    }


def test_scored_endpoint_includes_model_header() -> None:
    settings = Settings(default_model_name="test-model")
    client = TestClient(create_app(settings))

    response = client.post("/triage", json=_triage_payload())

    assert response.status_code == 200
    assert response.headers[MODEL_HEADER_NAME] == "test-model"


def test_task_specific_model_header_overrides_default() -> None:
    settings = Settings(default_model_name="default-model", extract_model_name="vision-model")
    client = TestClient(create_app(settings))

    response = client.post(
        "/extract",
        json={
            "document_id": "DOC-1001",
            "content_format": "image_base64",
            "content": "iVBORw0KGgo=",
            "json_schema": None,
        },
    )

    assert response.status_code == 200
    assert response.headers[MODEL_HEADER_NAME] == "vision-model"


def test_malformed_json_returns_400_with_model_header() -> None:
    settings = Settings(default_model_name="test-model")
    client = TestClient(create_app(settings))

    response = client.post(
        "/triage",
        content='{"broken"',
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 400
    assert response.headers[MODEL_HEADER_NAME] == "test-model"
    assert response.json()["detail"]


def test_empty_body_returns_422_with_model_header() -> None:
    settings = Settings(default_model_name="test-model")
    client = TestClient(create_app(settings))

    response = client.post("/triage", json={})

    assert response.status_code == 422
    assert response.headers[MODEL_HEADER_NAME] == "test-model"
    assert response.json()["detail"]


def test_wrong_content_type_returns_415_with_model_header() -> None:
    settings = Settings(default_model_name="test-model")
    client = TestClient(create_app(settings))

    response = client.post(
        "/triage",
        content='{"ticket_id": "SIG-1001"}',
        headers={"Content-Type": "text/plain"},
    )

    assert response.status_code == 415
    assert response.headers[MODEL_HEADER_NAME] == "test-model"


def test_lifespan_creates_and_closes_shared_http_client() -> None:
    app = create_app(Settings(default_model_name="lifespan-model"))

    with TestClient(app):
        assert not app.state.clients.http_client.is_closed
        assert app.state.clients.settings.default_model_name == "lifespan-model"

    assert app.state.clients.http_client.is_closed


def test_retry_after_seconds_parses_http_date() -> None:
    retry_at = datetime.now(UTC) + timedelta(seconds=60)
    retry_after = retry_after_seconds(httpx.Headers({"Retry-After": format_datetime(retry_at, usegmt=True)}))

    assert retry_after is not None
    assert retry_after > 0


def test_retry_after_seconds_rejects_non_finite_seconds() -> None:
    assert retry_after_seconds(httpx.Headers({"Retry-After": "NaN"})) is None
    assert retry_after_seconds(httpx.Headers({"Retry-After-Ms": "Infinity"})) is None


def test_retry_after_seconds_falls_back_from_invalid_ms_to_seconds() -> None:
    assert retry_after_seconds(httpx.Headers({"Retry-After-Ms": "Infinity", "Retry-After": "30"})) == 30


def test_retry_delay_caps_explicit_retry_after() -> None:
    request = httpx.Request("POST", "https://model.example.invalid")
    response = httpx.Response(429, headers={"Retry-After": "60"}, request=request)
    error = httpx.HTTPStatusError("throttled", request=request, response=response)

    assert _retry_delay_seconds(error, base_delay_seconds=1, attempt=1, max_delay_seconds=10) == 10


@pytest.mark.asyncio
async def test_retry_helper_retries_transient_failure() -> None:
    attempts = 0

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TimeoutError("transient")
        return "ok"

    result = await run_with_retries(
        operation,
        max_attempts=2,
        base_delay_seconds=0,
        retryable_exceptions=(TimeoutError,),
    )

    assert result == "ok"
    assert attempts == 2


@pytest.mark.asyncio
async def test_retry_helper_honors_retry_after_for_429() -> None:
    attempts = 0
    request = httpx.Request("POST", "https://model.example.invalid")
    response = httpx.Response(429, headers={"Retry-After": "0"}, request=request)

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.HTTPStatusError("throttled", request=request, response=response)
        return "ok"

    result = await run_with_retries(
        operation,
        max_attempts=2,
        base_delay_seconds=10,
    )

    assert result == "ok"
    assert attempts == 2


@pytest.mark.asyncio
async def test_retry_helper_does_not_retry_non_transient_status() -> None:
    attempts = 0
    request = httpx.Request("POST", "https://model.example.invalid")
    response = httpx.Response(400, request=request)

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        raise httpx.HTTPStatusError("bad request", request=request, response=response)

    with pytest.raises(httpx.HTTPStatusError):
        await run_with_retries(
            operation,
            max_attempts=2,
            base_delay_seconds=0,
        )

    assert attempts == 1
