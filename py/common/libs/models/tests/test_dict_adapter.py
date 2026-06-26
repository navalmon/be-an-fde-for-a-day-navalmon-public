# Copyright (c) Microsoft. All rights reserved.
from datetime import datetime
from typing import Any
from typing import Literal

import pytest
from pydantic import Field
from pydantic import ValidationError

from ms.common.models.base import FrozenBaseModel
from ms.common.models.dict_adapter import dict_adapter
from ms.common.models.dict_adapter import dict_adapter_method


@pytest.mark.parametrize(
    (
        "model_dump_mode",
        "model_dump_by_alias",
        "expected_output",
    ),
    [
        (
            "python",
            True,
            {"date_alias": datetime(2023, 4, 27)},
        ),
        (
            "python",
            False,
            {"date": datetime(2023, 4, 27)},
        ),
        (
            "json",
            True,
            {"date_alias": "2023-04-27T00:00:00"},
        ),
        (
            "json",
            False,
            {"date": "2023-04-27T00:00:00"},
        ),
    ],
)
def test_dict_adapter(
    model_dump_mode: Literal["python", "json"],
    model_dump_by_alias: bool,
    expected_output: dict[str, Any],
):
    class InputModel(FrozenBaseModel):
        year: int
        month: int
        day: int

    class OutputModel(FrozenBaseModel):
        date: datetime = Field(serialization_alias="date_alias")

    @dict_adapter(
        InputModel,
        model_dump_mode=model_dump_mode,
        model_dump_by_alias=model_dump_by_alias,
    )
    def some_function(input_model: InputModel) -> OutputModel:
        return OutputModel(
            date=datetime(
                year=input_model.year,
                month=input_model.month,
                day=input_model.day,
            ),
        )

    result = some_function({"year": 2023, "month": 4, "day": 27})

    assert result == expected_output


@pytest.mark.parametrize(
    (
        "model_dump_mode",
        "model_dump_by_alias",
        "expected_output",
    ),
    [
        (
            "python",
            True,
            {"date_alias": datetime(2023, 4, 27)},
        ),
        (
            "python",
            False,
            {"date": datetime(2023, 4, 27)},
        ),
        (
            "json",
            True,
            {"date_alias": "2023-04-27T00:00:00"},
        ),
        (
            "json",
            False,
            {"date": "2023-04-27T00:00:00"},
        ),
    ],
)
def test_dict_adapter_on_method(
    model_dump_mode: Literal["python", "json"],
    model_dump_by_alias: bool,
    expected_output: dict[str, Any],
):
    class InputModel(FrozenBaseModel):
        year: int
        month: int
        day: int

    class OutputModel(FrozenBaseModel):
        date: datetime = Field(serialization_alias="date_alias")

    class MyClass:
        @dict_adapter_method(
            InputModel,
            model_dump_mode=model_dump_mode,
            model_dump_by_alias=model_dump_by_alias,
        )
        def some_method(self, input_model: InputModel) -> OutputModel:
            return OutputModel(
                date=datetime(
                    year=input_model.year,
                    month=input_model.month,
                    day=input_model.day,
                )
            )

    result = MyClass().some_method({"year": 2023, "month": 4, "day": 27})
    assert result == expected_output


def test_dict_adapter_on_class_method():
    class InputModel(FrozenBaseModel):
        a: str
        b: str

    class OutputModel(FrozenBaseModel):
        concatenated: str

    class MyClass:
        @classmethod
        @dict_adapter_method(InputModel)
        def some_method(cls, input_model: InputModel) -> OutputModel:
            return OutputModel(concatenated=input_model.a + input_model.b)

    result = MyClass.some_method({"a": "Hello, ", "b": "World!"})
    assert result == {"concatenated": "Hello, World!"}


def test_dict_adapter_on_static_method():
    class InputModel(FrozenBaseModel):
        a: str
        b: str

    class OutputModel(FrozenBaseModel):
        concatenated: str

    class MyClass:
        @staticmethod
        @dict_adapter(InputModel)
        def some_method(input_model: InputModel) -> OutputModel:
            return OutputModel(concatenated=input_model.a + input_model.b)

    class_result = MyClass.some_method({"a": "Hello, ", "b": "World!"})
    instance_result = MyClass().some_method({"a": "Hello, ", "b": "World!"})

    assert class_result == {"concatenated": "Hello, World!"}
    assert instance_result == {"concatenated": "Hello, World!"}


def test_dict_adapter_raises_validation_error():
    class InputModel(FrozenBaseModel):
        x: int
        y: int

    class OutputModel(FrozenBaseModel):
        sum: int

    @dict_adapter(InputModel)
    def some_function(input_model: InputModel) -> OutputModel:
        return OutputModel(sum=input_model.x + input_model.y)

    with pytest.raises(ValidationError):
        some_function({"x": "not-an-int", "y": 4})


def test_dict_adapter_method_raises_validation_error():
    class InputModel(FrozenBaseModel):
        x: int
        y: int

    class OutputModel(FrozenBaseModel):
        sum: int

    class MyClass:
        @dict_adapter_method(InputModel)
        def some_method(self, input_model: InputModel) -> OutputModel:
            return OutputModel(sum=input_model.x + input_model.y)

    with pytest.raises(ValidationError):
        MyClass().some_method({"x": "not-an-int", "y": 4})
