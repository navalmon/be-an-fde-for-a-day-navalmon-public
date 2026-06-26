# Copyright (c) Microsoft. All rights reserved.
from typing import Any

from pydantic import BaseModel  # noqa: TID251 # required for model introspection utilities


def _is_pydantic_model(obj: Any) -> bool:
    try:
        return issubclass(obj, BaseModel)
    except TypeError:
        return False


def recursively_get_nested_models(field_type: Any) -> list[type[BaseModel]]:
    models: list[type[BaseModel]] = []

    if _is_pydantic_model(field_type):
        models.append(field_type)
        for field in field_type.model_fields.values():
            models.extend(recursively_get_nested_models(field.annotation))
    elif hasattr(field_type, "__args__"):
        # Types like list[Model], dict[str, Model], etc. have an __args__ attribute
        # with the type's args. For example:
        # list[Model] -> __args__ = (Model,)
        # dict[str, Model] -> __args__ = (str, Model)
        for arg in field_type.__args__:
            models.extend(recursively_get_nested_models(arg))

    return models
