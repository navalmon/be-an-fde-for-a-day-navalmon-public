"""JSON-schema helpers for Task 2 document extraction."""

import json
import re
from typing import Any

Schema = dict[str, Any]

_CURRENCY_AND_GROUPING = re.compile(r"[$€£¥₹,\s]")
_PERCENT = re.compile(r"%$")
_MAX_FIELD_GUIDE_LINES = 120


def parse_schema(schema_text: str | None) -> Schema:
    """Parse a request JSON schema string, returning an empty object schema on failure."""
    if not schema_text:
        return {"type": "object", "properties": {}}
    try:
        parsed = json.loads(schema_text)
    except (TypeError, ValueError, RecursionError):
        return {"type": "object", "properties": {}}
    if not isinstance(parsed, dict):
        return {"type": "object", "properties": {}}
    return parsed


def output_skeleton(schema: Schema) -> dict[str, Any]:
    """Build a schema-shaped fallback object with safe null/default values."""
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return {}
    return {str(key): _skeleton_value(value if isinstance(value, dict) else {}) for key, value in properties.items()}


def normalize_to_schema(payload: dict[str, Any], schema: Schema) -> dict[str, Any]:
    """Return payload fields coerced to the shape and primitive types requested by schema."""
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return {}
    normalized: dict[str, Any] = {}
    for key, subschema in properties.items():
        if not isinstance(key, str):
            continue
        schema_obj = subschema if isinstance(subschema, dict) else {}
        normalized[key] = _normalize_value(payload.get(key), schema_obj)
    return normalized


def schema_field_guide(schema: Schema) -> str:
    """Return compact path/type hints for the extraction prompt."""
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return "(schema has no declared properties)"
    lines: list[str] = []
    required = _required_fields(schema)
    for key, subschema in properties.items():
        if isinstance(key, str):
            _append_field_guide_line(
                lines,
                key,
                subschema if isinstance(subschema, dict) else {},
                required=key in required,
            )
    return "\n".join(lines[:_MAX_FIELD_GUIDE_LINES])


def _skeleton_value(schema: Schema) -> Any:
    schema_type = _primary_type(schema)
    if schema_type == "object":
        return output_skeleton(schema)
    if schema_type == "array":
        return []
    return None


def _append_field_guide_line(lines: list[str], path: str, schema: Schema, *, required: bool) -> None:
    if len(lines) >= _MAX_FIELD_GUIDE_LINES:
        return
    schema_type = _primary_type(schema) or "any"
    marker = " required" if required else ""
    details: list[str] = []
    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and enum_values:
        details.append("enum=" + "|".join(str(item) for item in enum_values[:10]))
    description = schema.get("description")
    if isinstance(description, str) and description.strip():
        details.append(description.strip())
    suffix = f" — {'; '.join(details)}" if details else ""
    lines.append(f"- {path}: {schema_type}{marker}{suffix}")

    if schema_type == "object":
        child_properties = schema.get("properties")
        if isinstance(child_properties, dict):
            required_fields = _required_fields(schema)
            for key, subschema in child_properties.items():
                if isinstance(key, str):
                    _append_field_guide_line(
                        lines,
                        f"{path}.{key}",
                        subschema if isinstance(subschema, dict) else {},
                        required=key in required_fields,
                    )
    elif schema_type == "array":
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            _append_field_guide_line(lines, f"{path}[]", item_schema, required=False)


def _required_fields(schema: Schema) -> set[str]:
    required = schema.get("required")
    if not isinstance(required, list):
        return set()
    return {item for item in required if isinstance(item, str)}


def _normalize_value(value: Any, schema: Schema) -> Any:
    if value is None:
        return _skeleton_value(schema)

    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and enum_values:
        matched = _match_enum(value, enum_values)
        return matched if matched is not None else value

    schema_type = _primary_type(schema)
    if schema_type == "object":
        if not isinstance(value, dict):
            return output_skeleton(schema)
        return normalize_to_schema(value, schema)
    if schema_type == "array":
        if not isinstance(value, list):
            return []
        item_schema = schema.get("items")
        item_schema = item_schema if isinstance(item_schema, dict) else {}
        return [_normalize_value(item, item_schema) for item in value]
    if schema_type in {"number", "integer"}:
        return _coerce_number(value, integer=schema_type == "integer")
    if schema_type == "boolean":
        return _coerce_bool(value)
    if schema_type == "string":
        return str(value).strip() if not isinstance(value, str) else value.strip()
    return value


def _primary_type(schema: Schema) -> str | None:
    schema_type = schema.get("type")
    if isinstance(schema_type, str):
        return schema_type
    if isinstance(schema_type, list):
        for item in schema_type:
            if isinstance(item, str) and item != "null":
                return item
    if "properties" in schema:
        return "object"
    if "items" in schema:
        return "array"
    return None


def _coerce_number(value: Any, *, integer: bool) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return int(value) if integer else float(value)
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    negative = candidate.startswith("(") and candidate.endswith(")")
    candidate = candidate.strip("()")
    candidate = _PERCENT.sub("", candidate)
    candidate = _CURRENCY_AND_GROUPING.sub("", candidate)
    try:
        number = float(candidate)
    except ValueError:
        return None
    if negative:
        number = -number
    return int(number) if integer else number


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "y", "checked", "selected", "1", "present"}:
            return True
        if normalized in {"false", "no", "n", "unchecked", "unselected", "0", "absent"}:
            return False
    return None


def _match_enum(value: Any, enum_values: list[Any]) -> Any | None:
    if value in enum_values:
        return value
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    for enum_value in enum_values:
        if isinstance(enum_value, str) and enum_value.strip().lower() == normalized:
            return enum_value
    return None
