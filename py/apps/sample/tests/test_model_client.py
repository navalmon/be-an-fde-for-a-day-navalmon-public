"""Tests for the OpenAI-compatible model-provider adapter."""

import asyncio
import json

import httpx
import pytest
from app_state import create_app_clients
from config import Settings
from model_client import ChatMessage
from model_client import ModelClient
from model_client import ModelProviderError
from model_client import ModelProviderNotConfigured
from model_client import ModelProviderStatusError
from model_client import ModelResponseError
from model_client import parse_json_object


def test_parse_json_object_from_fenced_block() -> None:
    assert parse_json_object('```json\n{"category": "Mission Briefing Request"}\n```') == {
        "category": "Mission Briefing Request",
    }


def test_parse_json_object_rejects_invalid_json() -> None:
    with pytest.raises(ModelResponseError):
        parse_json_object("not json")


def test_parse_json_object_recovers_wrapped_plain_json() -> None:
    assert parse_json_object('prefix {"ok": true} suffix') == {"ok": True}


def test_parse_json_object_recovers_wrapped_json_with_braces_in_strings() -> None:
    assert parse_json_object('Here is JSON: {"text": "keep {this} literal", "ok": true}\nThanks') == {
        "text": "keep {this} literal",
        "ok": True,
    }


def test_parse_json_object_rejects_non_finite_numbers() -> None:
    with pytest.raises(ModelResponseError):
        parse_json_object('{"score": NaN}')
    with pytest.raises(ModelResponseError):
        parse_json_object('{"score": 1e999}')


def test_parse_json_object_rejects_oversized_integers() -> None:
    oversized_integer = "1" * 5000

    with pytest.raises(ModelResponseError):
        parse_json_object(f'{{"score": {oversized_integer}}}')


def test_parse_json_object_rejects_deep_json() -> None:
    deep_json = '{"x":' * 2000 + "0" + "}" * 2000

    with pytest.raises(ModelResponseError):
        parse_json_object(deep_json)


def test_model_client_preserves_full_chat_completions_url_with_query() -> None:
    client = ModelClient(
        settings=Settings(
            model_base_url="https://model.example.test/openai/deployments/dep/chat/completions?api-version=2024-02-15-preview",
            model_api_key="secret-key",
        ),
        http_client=httpx.AsyncClient(),
        model_semaphore=asyncio.Semaphore(1),
    )

    assert (
        client._chat_completions_url()
        == "https://model.example.test/openai/deployments/dep/chat/completions?api-version=2024-02-15-preview"
    )
    asyncio.run(client.http_client.aclose())


def test_model_client_derives_responses_url_from_chat_completions_url() -> None:
    client = ModelClient(
        settings=Settings(
            model_base_url="https://model.example.test/openai/deployments/dep/chat/completions?api-version=2024-02-15-preview",
            model_api_key="secret-key",
        ),
        http_client=httpx.AsyncClient(),
        model_semaphore=asyncio.Semaphore(1),
    )

    assert client._responses_url() == "https://model.example.test/openai/v1/responses"
    asyncio.run(client.http_client.aclose())


@pytest.mark.asyncio
async def test_model_client_requires_configuration() -> None:
    client = ModelClient(
        settings=Settings(model_base_url="", model_api_key=""),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200))),
        model_semaphore=asyncio.Semaphore(1),
    )

    with pytest.raises(ModelProviderNotConfigured):
        await client.complete_text(
            messages=[ChatMessage(role="user", content="hello")],
            model_name="test-model",
        )

    await client.http_client.aclose()


@pytest.mark.asyncio
async def test_model_client_posts_chat_completion_and_retries_429() -> None:
    attempts = 0
    captured_payloads: list[dict] = []
    captured_headers: list[httpx.Headers] = []
    captured_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        captured_urls.append(str(request.url))
        captured_headers.append(request.headers)
        captured_payloads.append(json.loads(request.content))

        if attempts == 1:
            return httpx.Response(
                429,
                headers={"Retry-After": "0"},
                request=request,
                json={"error": "throttled"},
            )
        return httpx.Response(
            200,
            request=request,
            json={"choices": [{"message": {"content": '{"ok": true}'}}]},
        )

    settings = Settings(
        model_base_url="https://model.example.test/openai/v1",
        model_api_key="secret-key",
        max_retry_attempts=2,
        retry_base_delay_seconds=0,
    )
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = ModelClient(
        settings=settings,
        http_client=http_client,
        model_semaphore=asyncio.Semaphore(1),
    )

    result = await client.complete_json(
        messages=[ChatMessage(role="user", content="Return JSON.")],
        model_name="gpt-test",
    )

    assert result == {"ok": True}
    assert attempts == 2
    assert captured_urls == [
        "https://model.example.test/openai/v1/chat/completions",
        "https://model.example.test/openai/v1/chat/completions",
    ]
    assert captured_headers[-1]["authorization"] == "Bearer secret-key"
    assert captured_headers[-1]["api-key"] == "secret-key"
    assert captured_payloads[-1]["model"] == "gpt-test"
    assert captured_payloads[-1]["response_format"] == {"type": "json_object"}

    await http_client.aclose()


@pytest.mark.asyncio
async def test_model_client_preserves_multimodal_message_content() -> None:
    captured_payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            request=request,
            json={"choices": [{"message": {"content": '{"ok": true}'}}]},
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = ModelClient(
        settings=Settings(model_base_url="https://model.example.test/openai/v1", model_api_key="secret-key"),
        http_client=http_client,
        model_semaphore=asyncio.Semaphore(1),
    )

    result = await client.complete_json(
        messages=[
            ChatMessage(
                role="user",
                content=[
                    {"type": "text", "text": "extract"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}},
                ],
            )
        ],
        model_name="gpt-vision-test",
    )

    assert result == {"ok": True}
    assert captured_payloads[0]["messages"][0]["content"][1]["type"] == "image_url"

    await http_client.aclose()


@pytest.mark.asyncio
async def test_model_client_posts_reasoning_models_to_responses_api() -> None:
    captured_payloads: list[dict] = []
    captured_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_urls.append(str(request.url))
        captured_payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            request=request,
            json={"output_text": '{"ok": true}'},
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = ModelClient(
        settings=Settings(model_base_url="https://model.example.test/openai/v1", model_api_key="secret-key"),
        http_client=http_client,
        model_semaphore=asyncio.Semaphore(1),
    )

    result = await client.complete_json(
        messages=[
            ChatMessage(role="system", content="Return JSON only."),
            ChatMessage(
                role="user",
                content=[
                    {"type": "text", "text": "extract"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,iVBORw0KGgo=", "detail": "high"},
                    },
                ],
            ),
        ],
        model_name="o4-mini",
    )

    assert result == {"ok": True}
    assert captured_urls == ["https://model.example.test/openai/v1/responses"]
    payload = captured_payloads[0]
    assert payload["model"] == "o4-mini"
    assert payload["instructions"] == "Return JSON only."
    assert payload["input"] == [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "extract"},
                {"type": "input_image", "image_url": "data:image/png;base64,iVBORw0KGgo=", "detail": "high"},
            ],
        }
    ]
    assert payload["text"] == {"format": {"type": "json_object"}}
    assert payload["max_output_tokens"] == 1024
    assert "temperature" not in payload

    await http_client.aclose()


@pytest.mark.asyncio
async def test_model_client_auto_routes_reasoning_models_from_azure_chat_url_to_responses() -> None:
    captured_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_urls.append(str(request.url))
        return httpx.Response(
            200,
            request=request,
            json={"output_text": '{"ok": true}'},
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = ModelClient(
        settings=Settings(
            model_base_url="https://model.example.test/openai/deployments/dep/chat/completions?api-version=2024-02-15-preview",
            model_api_key="secret-key",
        ),
        http_client=http_client,
        model_semaphore=asyncio.Semaphore(1),
    )

    result = await client.complete_json(
        messages=[ChatMessage(role="user", content="Return JSON.")],
        model_name="o4-mini",
    )

    assert result == {"ok": True}
    assert captured_urls == ["https://model.example.test/openai/v1/responses"]

    await http_client.aclose()


@pytest.mark.asyncio
async def test_model_client_extracts_responses_output_content_text() -> None:
    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                request=request,
                json={
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": '{"ok": true}'}],
                        }
                    ]
                },
            )
        )
    )
    client = ModelClient(
        settings=Settings(
            model_base_url="https://model.example.test/openai/v1",
            model_api_key="secret-key",
            model_api_style="responses",
        ),
        http_client=http_client,
        model_semaphore=asyncio.Semaphore(1),
    )

    result = await client.complete_json(
        messages=[ChatMessage(role="user", content="Return JSON.")],
        model_name="gpt-test",
    )

    assert result == {"ok": True}

    await http_client.aclose()


@pytest.mark.asyncio
async def test_model_client_wraps_provider_http_status_errors() -> None:
    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                400,
                request=request,
                json={"error": {"message": "Unsupported parameter: max_tokens"}},
            )
        )
    )
    client = ModelClient(
        settings=Settings(model_base_url="https://model.example.test/openai/v1", model_api_key="secret-key"),
        http_client=http_client,
        model_semaphore=asyncio.Semaphore(1),
    )

    with pytest.raises(ModelProviderStatusError, match="HTTP 400: Unsupported parameter") as exc_info:
        await client.complete_json(
            messages=[ChatMessage(role="user", content="Return JSON.")],
            model_name="gpt-test",
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Unsupported parameter: max_tokens"

    await http_client.aclose()


@pytest.mark.asyncio
async def test_model_client_rejects_invalid_provider_json() -> None:
    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, request=request, content=b"not-json"))
    )
    client = ModelClient(
        settings=Settings(model_base_url="https://model.example.test/openai/v1", model_api_key="secret-key"),
        http_client=http_client,
        model_semaphore=asyncio.Semaphore(1),
    )

    with pytest.raises(ModelResponseError):
        await client.complete_text(
            messages=[ChatMessage(role="user", content="hello")],
            model_name="gpt-test",
        )

    await http_client.aclose()


@pytest.mark.asyncio
async def test_model_client_rejects_invalid_provider_encoding() -> None:
    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, request=request, content=b"\xff"))
    )
    client = ModelClient(
        settings=Settings(model_base_url="https://model.example.test/openai/v1", model_api_key="secret-key"),
        http_client=http_client,
        model_semaphore=asyncio.Semaphore(1),
    )

    with pytest.raises(ModelResponseError):
        await client.complete_text(
            messages=[ChatMessage(role="user", content="hello")],
            model_name="gpt-test",
        )

    await http_client.aclose()


@pytest.mark.asyncio
async def test_model_client_rejects_provider_envelope_non_finite_numbers() -> None:
    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                request=request,
                content=b'{"choices":[{"message":{"content":"ok"}}],"usage":{"total_tokens":NaN}}',
            )
        )
    )
    client = ModelClient(
        settings=Settings(model_base_url="https://model.example.test/openai/v1", model_api_key="secret-key"),
        http_client=http_client,
        model_semaphore=asyncio.Semaphore(1),
    )

    with pytest.raises(ModelResponseError):
        await client.complete_text(
            messages=[ChatMessage(role="user", content="hello")],
            model_name="gpt-test",
        )

    await http_client.aclose()


@pytest.mark.asyncio
async def test_model_client_rejects_provider_envelope_oversized_numbers() -> None:
    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                request=request,
                content=b'{"choices":[{"message":{"content":"ok"}}],"usage":{"total_tokens":1e999}}',
            )
        )
    )
    client = ModelClient(
        settings=Settings(model_base_url="https://model.example.test/openai/v1", model_api_key="secret-key"),
        http_client=http_client,
        model_semaphore=asyncio.Semaphore(1),
    )

    with pytest.raises(ModelResponseError):
        await client.complete_text(
            messages=[ChatMessage(role="user", content="hello")],
            model_name="gpt-test",
        )

    await http_client.aclose()


@pytest.mark.asyncio
async def test_model_client_rejects_provider_envelope_oversized_integers() -> None:
    oversized_integer = "1" * 5000
    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                request=request,
                content=f'{{"choices":[{{"message":{{"content":"ok"}}}}],"usage":{{"total_tokens":{oversized_integer}}}}}'.encode(),
            )
        )
    )
    client = ModelClient(
        settings=Settings(model_base_url="https://model.example.test/openai/v1", model_api_key="secret-key"),
        http_client=http_client,
        model_semaphore=asyncio.Semaphore(1),
    )

    with pytest.raises(ModelResponseError):
        await client.complete_text(
            messages=[ChatMessage(role="user", content="hello")],
            model_name="gpt-test",
        )

    await http_client.aclose()


@pytest.mark.asyncio
async def test_model_client_rejects_deep_provider_envelope() -> None:
    deep_value = '{"x":' * 2000 + "0" + "}" * 2000
    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                request=request,
                content=f'{{"choices":[{{"message":{{"content":"ok"}}}}],"usage":{deep_value}}}'.encode(),
            )
        )
    )
    client = ModelClient(
        settings=Settings(model_base_url="https://model.example.test/openai/v1", model_api_key="secret-key"),
        http_client=http_client,
        model_semaphore=asyncio.Semaphore(1),
    )

    with pytest.raises(ModelResponseError):
        await client.complete_text(
            messages=[ChatMessage(role="user", content="hello")],
            model_name="gpt-test",
        )

    await http_client.aclose()


@pytest.mark.asyncio
async def test_model_client_rejects_non_object_provider_json() -> None:
    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, request=request, json=[]))
    )
    client = ModelClient(
        settings=Settings(model_base_url="https://model.example.test/openai/v1", model_api_key="secret-key"),
        http_client=http_client,
        model_semaphore=asyncio.Semaphore(1),
    )

    with pytest.raises(ModelResponseError):
        await client.complete_text(
            messages=[ChatMessage(role="user", content="hello")],
            model_name="gpt-test",
        )

    await http_client.aclose()


def test_app_clients_include_model_client() -> None:
    clients = create_app_clients(Settings(model_base_url="https://model.example.test", model_api_key="secret-key"))

    assert clients.model_client.is_configured()
    assert clients.model_client.http_client is clients.http_client
    assert clients.model_client.model_semaphore is clients.model_semaphore
