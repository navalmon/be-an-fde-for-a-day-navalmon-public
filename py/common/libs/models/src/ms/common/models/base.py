# Copyright (c) Microsoft. All rights reserved.
"""Base Pydantic models with common configurations.

This module provides base model classes that enforce consistent behavior
across all Pydantic models in the FDE project.
"""

from pydantic import BaseModel  # noqa: TID251 # required to define FrozenBaseModel base class
from pydantic import ConfigDict


class FrozenBaseModel(BaseModel):
    """A Pydantic BaseModel with frozen configuration.

    This base class ensures that all model instances are immutable after creation,
    preventing accidental modifications and ensuring data integrity throughout
    the application lifecycle.

    Example:
        ```python
        from ms.common.models.base import FrozenBaseModel

        class User(FrozenBaseModel):
            name: str
            age: int

        user = User(name="Alice", age=30)
        # user.name = "Bob"  # This would raise a ValidationError
        ```

    Attributes:
        model_config: Pydantic configuration with frozen=True to make instances immutable.
    """

    model_config = ConfigDict(frozen=True)
