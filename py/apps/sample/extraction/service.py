"""Service layer for Task 2 document extraction."""

import asyncio
import base64
import binascii
import hashlib
import logging
import time
from collections import OrderedDict
from collections.abc import Sequence
from typing import Any
from typing import Protocol

from config import Settings
from model_client import ChatMessage
from model_client import ModelProviderError
from model_client import ModelProviderStatusError
from models import ExtractRequest
from models import ExtractResponse

from extraction.image_tools import PreparedImage
from extraction.image_tools import prepare_png_for_model
from extraction.schema_tools import normalize_to_schema
from extraction.schema_tools import output_skeleton
from extraction.schema_tools import parse_schema
from extraction.schema_tools import schema_field_guide

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_LOG_SNIPPET_LENGTH = 300
logger = logging.getLogger(__name__)


class ExtractionService:
    """Extract schema-shaped data from document images."""

    def __init__(
        self, *, settings: Settings | None = None, model_client: "ExtractionModelClient | None" = None
    ) -> None:
        self._settings = settings or Settings()
        self._model_client = model_client
        self._cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._cache_lock = asyncio.Lock()
        self._inflight: dict[str, asyncio.Task[dict[str, Any] | None]] = {}

    async def extract(self, request: ExtractRequest) -> ExtractResponse:
        """Extract fields requested by the per-document schema."""
        schema = parse_schema(request.json_schema)
        fallback = output_skeleton(schema)
        context = _request_log_context(request, schema)
        if request.content_format != "image_base64":
            _log_extract_fallback(context, reason="unsupported_content_format", content_format=request.content_format)
            return _response_from_fields(request.document_id, fallback)
        image_bytes = _decode_png_bytes(request.content)
        if image_bytes is None:
            _log_extract_fallback(context, reason="invalid_base64_or_png", content_length=len(request.content))
            return _response_from_fields(request.document_id, fallback)

        if self._model_client is None or not self._model_client.is_configured():
            _log_extract_fallback(context, reason="model_not_configured", image_bytes=len(image_bytes))
            return _response_from_fields(request.document_id, fallback)

        cache_key = _cache_key(request)
        fields = await self._get_or_extract_fields(cache_key, request, schema, image_bytes)
        if fields is None:
            _log_extract_fallback(context, reason="model_result_unavailable", image_bytes=len(image_bytes))
            return _response_from_fields(request.document_id, fallback)
        return _response_from_fields(request.document_id, fields)

    async def _get_or_extract_fields(
        self,
        cache_key: str,
        request: ExtractRequest,
        schema: dict[str, Any],
        image_bytes: bytes,
    ) -> dict[str, Any] | None:
        async with self._cache_lock:
            cached = self._get_cached_fields(cache_key)
            if cached is not None:
                return cached
            task = self._inflight.get(cache_key)
            if task is None:
                task = asyncio.create_task(self._extract_fields_from_model(request, schema, image_bytes))
                self._inflight[cache_key] = task
                task.add_done_callback(
                    lambda completed_task, key=cache_key: asyncio.create_task(
                        self._finalize_inflight_task(key, completed_task)
                    )
                )

        try:
            fields = await asyncio.shield(task)
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._finalize_inflight_task(cache_key, task)
            raise

        await self._finalize_inflight_task(cache_key, task)
        async with self._cache_lock:
            cached = self._get_cached_fields(cache_key)
            return cached if cached is not None else fields

    async def _finalize_inflight_task(
        self,
        cache_key: str,
        task: asyncio.Task[dict[str, Any] | None],
    ) -> None:
        async with self._cache_lock:
            if self._inflight.get(cache_key) is task:
                del self._inflight[cache_key]
            else:
                return
            if task.cancelled():
                return
            try:
                fields = task.result()
            except BaseException:
                return
            if fields is not None:
                self._store_cached_fields(cache_key, fields)

    async def _extract_fields_from_model(
        self,
        request: ExtractRequest,
        schema: dict[str, Any],
        image_bytes: bytes,
    ) -> dict[str, Any] | None:
        if self._model_client is None:
            return None
        prepared_image = prepare_png_for_model(
            image_bytes,
            detail=self._settings.extract_image_detail,
            image_format=self._settings.extract_image_format,
            jpeg_quality=self._settings.extract_jpeg_quality,
            max_dimension=self._settings.extract_image_max_dimension,
            low_contrast_threshold=self._settings.extract_low_contrast_threshold,
        )
        context = _request_log_context(request, schema)
        model_name = self._settings.model_name_for_path("/extract")
        start_time = time.perf_counter()
        logger.info(
            "telemetry=true event=extract_model_call_start document_hash=%s schema_hash=%s model=%s original_image_bytes=%d "
            "prepared_media_type=%s prepared_base64_chars=%d prepared_width=%s prepared_height=%s "
            "prepared_original_width=%s prepared_original_height=%s prepared_resized=%s "
            "prepared_auto_oriented=%s prepared_contrast_enhanced=%s image_detail=%s "
            "model_concurrency=%d max_retry_attempts=%d retry_base_delay_seconds=%.3f http_timeout_seconds=%.3f",
            context["document_hash"],
            context["schema_hash"],
            model_name,
            len(image_bytes),
            prepared_image.media_type,
            len(prepared_image.content_base64),
            prepared_image.width,
            prepared_image.height,
            prepared_image.original_width,
            prepared_image.original_height,
            prepared_image.resized,
            prepared_image.auto_oriented,
            prepared_image.contrast_enhanced,
            prepared_image.detail,
            self._settings.model_concurrency,
            self._settings.max_retry_attempts,
            self._settings.retry_base_delay_seconds,
            self._settings.http_timeout_seconds,
        )
        try:
            payload = await self._model_client.complete_json(
                messages=[
                    ChatMessage(
                        role="system",
                        content=(
                            "You are a high-precision document extraction engine. Return only one JSON object with "
                            "the exact top-level keys requested by the schema. Do not include markdown, prose, "
                            "citations, confidence fields, or extra keys. Use null when a requested value is not "
                            "visible. Do not infer missing values from context."
                        ),
                    ),
                    ChatMessage(role="user", content=_build_vision_content(request, schema, prepared_image)),
                ],
                model_name=model_name,
                temperature=0.0,
                max_tokens=max(self._settings.model_max_tokens, 2048),
            )
        except ModelProviderError as exc:
            duration_ms = _elapsed_ms(start_time)
            _log_model_call_failed(context, exc, duration_ms=duration_ms)
            return None
        except TimeoutError as exc:
            duration_ms = _elapsed_ms(start_time)
            _log_model_call_failed(context, exc, duration_ms=duration_ms)
            return None

        fields = normalize_to_schema(payload, schema)
        logger.info(
            "telemetry=true event=extract_model_call_success document_hash=%s schema_hash=%s duration_ms=%d normalized_field_count=%d",
            context["document_hash"],
            context["schema_hash"],
            _elapsed_ms(start_time),
            len(fields),
        )
        return fields

    def _get_cached_fields(self, cache_key: str) -> dict[str, Any] | None:
        if self._settings.extract_cache_max_entries <= 0:
            return None
        fields = self._cache.get(cache_key)
        if fields is None:
            return None
        self._cache.move_to_end(cache_key)
        return dict(fields)

    def _store_cached_fields(self, cache_key: str, fields: dict[str, Any]) -> None:
        max_entries = self._settings.extract_cache_max_entries
        if max_entries <= 0:
            return
        self._cache[cache_key] = dict(fields)
        self._cache.move_to_end(cache_key)
        while len(self._cache) > max_entries:
            self._cache.popitem(last=False)


class ExtractionModelClient(Protocol):
    """Subset of ModelClient used by the extraction service."""

    def is_configured(self) -> bool:
        """Return whether model calls can be made."""
        ...

    async def complete_json(
        self,
        *,
        messages: Sequence[ChatMessage],
        model_name: str,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Return a strict JSON model response."""
        ...


def _build_vision_content(
    request: ExtractRequest, schema: dict[str, Any], image: PreparedImage
) -> list[dict[str, Any]]:
    schema_text = request.json_schema or '{"type":"object","properties":{}}'
    image_note = _image_note(image)
    return [
        {
            "type": "text",
            "text": (
                "Extract every requested field from the attached document image.\n"
                "- Preserve exact visible strings, dates, names, IDs, addresses, and labels.\n"
                "- Parse currency, percentages, and grouped numbers as JSON numbers when the schema type is numeric.\n"
                "- Return booleans only when a checkbox/selection or explicit yes/no is visible; "
                "otherwise return null.\n"
                "- For tables and repeated sections, return all visible rows/items and keep columns aligned.\n"
                "- Do not include undefined blank rows, examples, instructions, or hallucinated values.\n"
                "- Use arrays/objects exactly as requested by the nested schema.\n\n"
                f"document_id: {request.document_id}\n"
                f"image: {image_note}\n\n"
                f"field_guide:\n{schema_field_guide(schema)}\n\n"
                f"json_schema:\n{schema_text}"
            ),
        },
        {
            "type": "image_url",
            "image_url": {"url": f"data:{image.media_type};base64,{image.content_base64}", "detail": image.detail},
        },
    ]


def _decode_png_bytes(value: str) -> bytes | None:
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError, TypeError):
        return None
    if not decoded.startswith(_PNG_SIGNATURE):
        return None
    return decoded


def _request_log_context(request: ExtractRequest, schema: dict[str, Any]) -> dict[str, str | int]:
    schema_text = request.json_schema or ""
    return {
        "document_hash": _short_hash(request.document_id),
        "schema_hash": _short_hash(schema_text),
        "schema_top_level_fields": len(schema.get("properties", {})) if isinstance(schema.get("properties"), dict) else 0,
    }


def _short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:12]


def _elapsed_ms(start_time: float) -> int:
    return int((time.perf_counter() - start_time) * 1000)


def _log_extract_fallback(context: dict[str, str | int], **fields: object) -> None:
    logger.warning(
        "telemetry=true event=extract_fallback_returned document_hash=%s schema_hash=%s schema_top_level_fields=%s %s",
        context["document_hash"],
        context["schema_hash"],
        context["schema_top_level_fields"],
        _format_log_fields(fields),
    )


def _log_model_call_failed(context: dict[str, str | int], exc: Exception, *, duration_ms: int) -> None:
    fields: dict[str, object] = {
        "duration_ms": duration_ms,
        "error_type": type(exc).__name__,
        "error": _safe_log_snippet(str(exc)),
    }
    if isinstance(exc, ModelProviderStatusError):
        fields["status_code"] = exc.status_code
        fields["retry_after"] = exc.retry_after_seconds or ""
        fields["provider_detail"] = _safe_log_snippet(exc.detail)
    logger.warning(
        "telemetry=true event=extract_model_call_failed document_hash=%s schema_hash=%s schema_top_level_fields=%s %s",
        context["document_hash"],
        context["schema_hash"],
        context["schema_top_level_fields"],
        _format_log_fields(fields),
    )


def _format_log_fields(fields: dict[str, object]) -> str:
    return " ".join(f"{key}={_safe_log_snippet(str(value))}" for key, value in fields.items())


def _safe_log_snippet(value: str) -> str:
    return value.replace("\n", "\\n").replace("\r", "\\r")[:_LOG_SNIPPET_LENGTH]


def _image_note(image: PreparedImage) -> str:
    actions: list[str] = []
    if image.auto_oriented:
        actions.append("auto-oriented")
    if image.contrast_enhanced:
        actions.append("low-contrast grayscale autocontrast")
    action_note = f", preprocessing={'; '.join(actions)}" if actions else ""
    if image.width is None or image.height is None:
        return f"PNG, detail={image.detail}{action_note}"
    if image.resized and image.original_width is not None and image.original_height is not None:
        return (
            f"PNG resized from {image.original_width}x{image.original_height} to "
            f"{image.width}x{image.height}, detail={image.detail}{action_note}"
        )
    return f"PNG {image.width}x{image.height}, detail={image.detail}{action_note}"


def _cache_key(request: ExtractRequest) -> str:
    digest = hashlib.sha256()
    digest.update(request.document_id.encode("utf-8", errors="replace"))
    digest.update(b"\0")
    digest.update(request.content.encode("ascii", errors="ignore"))
    digest.update(b"\0")
    digest.update((request.json_schema or "").encode("utf-8", errors="replace"))
    return digest.hexdigest()


def _response_from_fields(document_id: str, fields: dict[str, Any]) -> ExtractResponse:
    return ExtractResponse(document_id=document_id, **fields)
