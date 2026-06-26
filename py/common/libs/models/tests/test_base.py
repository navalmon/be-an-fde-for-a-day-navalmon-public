# Copyright (c) Microsoft. All rights reserved.
import pytest
from pydantic import ValidationError

from ms.common.models.base import FrozenBaseModel


def test_raises_validation_error_on_modification():
    class MyModel(FrozenBaseModel):
        field: int

    m = MyModel(field=10)

    with pytest.raises(ValidationError):
        m.field = 20
