"""OpenAI-compatible model-provider adapter for task services."""

import asyncio
import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from typing import Literal
from typing import NoReturn
from urllib.parse import urlsplit
from urllib.parse import urlunsplit

import httpx
from config import Settings
from resilience import run_with_retries

ChatRole = Literal["system", "user", "assistant"]
ResponseFormat = Literal["text", "json_object"]
ChatContent = str | list[dict[str, Any]]
ModelApiStyle = Literal["chat_completions", "responses"]

_JSON_BLOCK_PATTERN = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.IGNORECASE | re.DOTALL)
_MAX_JSON_DEPTH = 100
_REASONING_MODEL_PATTERN = re.compile(r"^o\d(?:\b|-)", re.IGNORECASE)


class ModelProviderError(RuntimeError):
    """Base class for model-provider failures."""


class ModelProviderStatusError(ModelProviderError):
    """Raised when the model provider returns a non-success HTTP status."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        detail: str,
        retry_after_seconds: str | None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail
        self.retry_after_seconds = retry_after_seconds


class ModelProviderNotConfigured(ModelProviderError):
    """Raised when model-provider settings are missing."""


class ModelResponseError(ModelProviderError):
    """Raised when the provider response cannot be parsed."""


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """Chat message sent to an OpenAI-compatible chat-completions endpoint."""

    role: ChatRole
    content: ChatContent

    def as_payload(self) -> dict[str, Any]:
        """Serialize the message for the provider request."""
        return {"role": self.role, "content": self.content}


def parse_json_object(text: str) -> dict[str, Any]:
    """Parse a JSON object from plain text, fenced JSON, or a wrapped JSON object."""
    candidate = _json_candidate(text)
    try:
        return _parse_json_candidate(candidate)
    except ModelResponseError as first_error:
        embedded = _first_balanced_json_object(candidate)
        if embedded is None or embedded == candidate.strip():
            raise first_error from first_error.__cause__
        return _parse_json_candidate(embedded)


def _parse_json_candidate(candidate: str) -> dict[str, Any]:
    try:
        parsed = json.loads(candidate, parse_constant=_reject_json_constant)
    except (ValueError, RecursionError) as exc:
        msg = _json_parse_error_message(candidate)
        raise ModelResponseError(msg) from exc
    if not isinstance(parsed, dict):
        msg = "model response JSON must be an object"
        raise ModelResponseError(msg)
    _ensure_finite_json_values(parsed)
    return parsed


def _json_parse_error_message(candidate: str) -> str:
    stripped = candidate.lstrip()
    first_char = stripped[:1] or "<empty>"
    return f"model response did not contain valid JSON: chars={len(candidate)} first_non_ws={first_char!r}"


def _json_candidate(text: str) -> str:
    stripped = text.strip()
    match = _JSON_BLOCK_PATTERN.search(stripped)
    if match is not None:
        return match.group(1).strip()
    return stripped


def _first_balanced_json_object(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None

    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
            if depth < 0:
                return None

    return None


def _reject_json_constant(value: str) -> NoReturn:
    msg = f"model response contained non-standard JSON constant: {value}"
    raise ModelResponseError(msg)


def _ensure_finite_json_values(value: Any, *, depth: int = 0) -> None:
    if depth > _MAX_JSON_DEPTH:
        msg = "model response JSON exceeded maximum nesting depth"
        raise ModelResponseError(msg)
    if isinstance(value, float) and not math.isfinite(value):
        msg = "model response contained non-finite JSON number"
        raise ModelResponseError(msg)
    if isinstance(value, dict):
        for item in value.values():
            _ensure_finite_json_values(item, depth=depth + 1)
    elif isinstance(value, list):
        for item in value:
            _ensure_finite_json_values(item, depth=depth + 1)


@dataclass(slots=True)
class ModelClient:
    """Small async adapter around OpenAI-compatible chat-completions and responses APIs."""

    settings: Settings
    http_client: httpx.AsyncClient
    model_semaphore: asyncio.Semaphore

    def is_configured(self) -> bool:
        """Return whether the adapter has enough settings to call a provider."""
        return bool(self.settings.model_base_url and self.settings.model_api_key)

    async def complete_text(
        self,
        *,
        messages: Sequence[ChatMessage],
        model_name: str,
        response_format: ResponseFormat = "text",
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> str:
        """Call the model provider and return text content from the first choice."""
        if not messages:
            msg = "at least one model message is required"
            raise ValueError(msg)

        api_style = self._api_style(model_name)
        payload = (
            _responses_payload(
                messages=messages,
                model_name=model_name,
                response_format=response_format,
                temperature=temperature,
                max_tokens=max_tokens or self.settings.model_max_tokens,
            )
            if api_style == "responses"
            else _chat_completions_payload(
                messages=messages,
                model_name=model_name,
                response_format=response_format,
                temperature=temperature,
                max_tokens=max_tokens or self.settings.model_max_tokens,
            )
        )
        url = self._responses_url() if api_style == "responses" else self._chat_completions_url()

        async def operation() -> str:
            async with self.model_semaphore:
                response = await self.http_client.post(
                    url,
                    headers=self._headers(),
                    json=payload,
                )
            response.raise_for_status()
            response_payload = _response_json_object(response)
            if api_style == "responses":
                return _extract_responses_content(response_payload)
            return _extract_chat_content(response_payload)

        try:
            return await run_with_retries(
                operation,
                max_attempts=self.settings.max_retry_attempts,
                base_delay_seconds=self.settings.retry_base_delay_seconds,
            )
        except httpx.HTTPStatusError as exc:
            raise _model_provider_status_error(exc) from exc
        except httpx.HTTPError as exc:
            msg = "model provider HTTP request failed"
            raise ModelProviderError(msg) from exc

    async def complete_json(
        self,
        *,
        messages: Sequence[ChatMessage],
        model_name: str,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Call the model provider and parse the response as a JSON object."""
        text = await self.complete_text(
            messages=messages,
            model_name=model_name,
            response_format="json_object",
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return parse_json_object(text)

    def _headers(self) -> dict[str, str]:
        if not self.settings.model_api_key:
            msg = "FDE_MODEL_API_KEY is required before calling a model provider"
            raise ModelProviderNotConfigured(msg)
        return {
            "Authorization": f"Bearer {self.settings.model_api_key}",
            "api-key": self.settings.model_api_key,
            "Content-Type": "application/json",
        }

    def _chat_completions_url(self) -> str:
        return self._model_api_url("chat/completions")

    def _responses_url(self) -> str:
        return self._model_api_url("responses")

    def _model_api_url(self, suffix: Literal["chat/completions", "responses"]) -> str:
        if not self.settings.model_base_url:
            msg = "FDE_MODEL_BASE_URL is required before calling a model provider"
            raise ModelProviderNotConfigured(msg)
        base_url = self.settings.model_base_url.strip()
        parsed = urlsplit(base_url)
        path = parsed.path.rstrip("/")
        if path.endswith(f"/{suffix}"):
            return base_url
        if suffix == "responses" and "/openai/deployments/" in path:
            return urlunsplit((parsed.scheme, parsed.netloc, "/openai/v1/responses", "", parsed.fragment))
        if path.endswith("/chat/completions"):
            path = path[: -len("/chat/completions")]
        elif path.endswith("/responses"):
            path = path[: -len("/responses")]
        api_path = f"{path}/{suffix}" if path else f"/{suffix}"
        return urlunsplit((parsed.scheme, parsed.netloc, api_path, parsed.query, parsed.fragment))

    def _api_style(self, model_name: str) -> ModelApiStyle:
        configured = self.settings.model_api_style
        if configured != "auto":
            return configured

        path = urlsplit(self.settings.model_base_url.strip()).path.rstrip("/")
        if path.endswith("/responses"):
            return "responses"
        if _is_reasoning_model(model_name):
            return "responses"
        if path.endswith("/chat/completions"):
            return "chat_completions"
        return "chat_completions"


def _extract_chat_content(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        msg = "model response missing choices"
        raise ModelResponseError(msg)

    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        msg = "model response choice must be an object"
        raise ModelResponseError(msg)

    message = first_choice.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return _join_content_parts(content)

    text = first_choice.get("text")
    if isinstance(text, str):
        return text

    msg = "model response choice missing text content"
    raise ModelResponseError(msg)


def _extract_responses_content(payload: dict[str, Any]) -> str:
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text:
        return output_text

    text_parts: list[str] = []
    output = payload.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if isinstance(content, list):
                text_parts.extend(_text_from_responses_content_parts(content))
            elif isinstance(content, str):
                text_parts.append(content)
            elif item.get("type") in ("output_text", "text"):
                text = item.get("text")
                if isinstance(text, str):
                    text_parts.append(text)

    if text_parts:
        return "".join(text_parts)

    msg = "responses API response missing text content"
    raise ModelResponseError(msg)


def _response_json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json(parse_constant=_reject_json_constant)
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        msg = "model provider returned invalid JSON"
        raise ModelResponseError(msg) from exc
    if not isinstance(payload, dict):
        msg = "model provider response must be a JSON object"
        raise ModelResponseError(msg)
    _ensure_finite_json_values(payload)
    return payload


def _chat_completions_payload(
    *,
    messages: Sequence[ChatMessage],
    model_name: str,
    response_format: ResponseFormat,
    temperature: float,
    max_tokens: int,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model_name,
        "messages": [message.as_payload() for message in messages],
    }
    if _is_reasoning_model(model_name):
        payload["max_completion_tokens"] = max_tokens
    else:
        payload["temperature"] = temperature
        payload["max_tokens"] = max_tokens
    if response_format == "json_object":
        payload["response_format"] = {"type": "json_object"}
    return payload


def _responses_payload(
    *,
    messages: Sequence[ChatMessage],
    model_name: str,
    response_format: ResponseFormat,
    temperature: float,
    max_tokens: int,
) -> dict[str, Any]:
    input_messages: list[dict[str, Any]] = []
    instructions: list[str] = []
    for message in messages:
        if message.role == "system":
            instructions.append(_content_as_instruction_text(message.content))
        else:
            input_messages.append(
                {
                    "role": message.role,
                    "content": _responses_content_parts(message.content),
                }
            )

    payload: dict[str, Any] = {
        "model": model_name,
        "input": input_messages,
        "max_output_tokens": max_tokens,
    }
    if instructions:
        payload["instructions"] = "\n\n".join(instructions)
    if not _is_reasoning_model(model_name):
        payload["temperature"] = temperature
    if response_format == "json_object":
        payload["text"] = {"format": {"type": "json_object"}}
    return payload


def _content_as_instruction_text(content: ChatContent) -> str:
    if isinstance(content, str):
        return content
    text_parts = [
        part.get("text", "") for part in content if isinstance(part, dict) and isinstance(part.get("text"), str)
    ]
    if text_parts:
        return "\n".join(text_parts)
    msg = "system model messages must contain text"
    raise ValueError(msg)


def _responses_content_parts(content: ChatContent) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "input_text", "text": content}]

    parts: list[dict[str, Any]] = []
    for part in content:
        if not isinstance(part, dict):
            msg = "model message content parts must be objects"
            raise ValueError(msg)
        part_type = part.get("type")
        if part_type in ("text", "input_text"):
            text = part.get("text")
            if not isinstance(text, str):
                msg = "text content parts must include text"
                raise ValueError(msg)
            parts.append({"type": "input_text", "text": text})
        elif part_type in ("image_url", "input_image"):
            image_url = part.get("image_url")
            detail = part.get("detail")
            if isinstance(image_url, dict):
                detail = detail or image_url.get("detail")
                image_url = image_url.get("url")
            if not isinstance(image_url, str):
                msg = "image content parts must include an image URL"
                raise ValueError(msg)
            image_part = {"type": "input_image", "image_url": image_url}
            if detail in {"auto", "low", "high"}:
                image_part["detail"] = detail
            parts.append(image_part)
        else:
            msg = f"unsupported model message content part type: {part_type!r}"
            raise ValueError(msg)
    return parts


def _text_from_responses_content_parts(content_parts: list[Any]) -> list[str]:
    text_parts: list[str] = []
    for part in content_parts:
        if isinstance(part, dict):
            text = part.get("text")
            if isinstance(text, str):
                text_parts.append(text)
        elif isinstance(part, str):
            text_parts.append(part)
    return text_parts


def _join_content_parts(content_parts: list[Any]) -> str:
    text_parts: list[str] = []
    for part in content_parts:
        if isinstance(part, dict):
            text = part.get("text")
            if isinstance(text, str):
                text_parts.append(text)
        elif isinstance(part, str):
            text_parts.append(part)
    if not text_parts:
        msg = "model response content parts did not include text"
        raise ModelResponseError(msg)
    return "".join(text_parts)


def _is_reasoning_model(model_name: str) -> bool:
    return bool(_REASONING_MODEL_PATTERN.match(model_name.strip()))


def _model_provider_status_error(exc: httpx.HTTPStatusError) -> ModelProviderError:
    response = exc.response
    detail = _provider_error_detail(response)
    msg = f"model provider returned HTTP {response.status_code}"
    if detail:
        msg = f"{msg}: {detail}"
    return ModelProviderStatusError(
        msg,
        status_code=response.status_code,
        detail=detail,
        retry_after_seconds=response.headers.get("Retry-After-Ms") or response.headers.get("Retry-After"),
    )


def _provider_error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        detail = response.text
    else:
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            detail = error["message"]
        elif isinstance(payload, dict) and isinstance(payload.get("message"), str):
            detail = payload["message"]
        else:
            detail = response.text
    return detail[:500].strip()
