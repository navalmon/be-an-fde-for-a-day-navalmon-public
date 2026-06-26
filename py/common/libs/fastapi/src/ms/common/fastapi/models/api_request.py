# Copyright (c) Microsoft. All rights reserved.
from typing import Any

from pydantic import ConfigDict
from pydantic.alias_generators import to_camel

from ms.common.fastapi.models.utils import recursively_get_nested_models
from ms.common.models.base import FrozenBaseModel


class ApiRequestBaseModel(FrozenBaseModel):
    """
    All API request models should inherit from this class.

    This allows us to keep fields in snake_case in our internal Python
    code while allowing the models to accept camelCase fields when receiving
    requests from clients.
    """

    model_config = ConfigDict(
        # Auto-generate camelCase aliases for all fields.
        alias_generator=to_camel,
        # Allow populating fields by their snake_case name (useful when creating
        # instances internally in tests).
        validate_by_name=True,
        # Only allow populating fields by their camelCase alias,
        # not by their snake_case name.
        validate_by_alias=True,
    )

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        for model in recursively_get_nested_models(cls):
            if not issubclass(model, ApiRequestBaseModel):
                raise TypeError(f"Nested model `{model.__name__}` must inherit from `{ApiRequestBaseModel.__name__}`")
