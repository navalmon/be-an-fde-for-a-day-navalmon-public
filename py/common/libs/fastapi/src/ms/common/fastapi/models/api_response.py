# Copyright (c) Microsoft. All rights reserved.
from typing import Any

from pydantic import ConfigDict
from pydantic.alias_generators import to_camel

from ms.common.fastapi.models.utils import recursively_get_nested_models
from ms.common.models.base import FrozenBaseModel


class ApiResponseBaseModel(FrozenBaseModel):
    """
    All API response models should inherit from this class.

    This allows us to keep fields in snake_case in our internal Python
    code while having them serialized to camelCase automatically when
    returning responses to clients.
    """

    model_config = ConfigDict(
        # Auto-generate camelCase aliases for all fields.
        # By default, FastAPI serializes models with the `by_alias` option set to `True`.
        alias_generator=to_camel,
        # Only allow populating fields by their snake_case name,
        # not by their camelCase alias.
        validate_by_name=True,
        # Don't allow populating fields by their camelCase alias.
        validate_by_alias=False,
    )

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        for model in recursively_get_nested_models(cls):
            if not issubclass(model, ApiResponseBaseModel):
                raise TypeError(f"Nested model `{model.__name__}` must inherit from `{ApiResponseBaseModel.__name__}`")
