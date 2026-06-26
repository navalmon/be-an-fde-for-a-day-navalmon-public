# Copyright (c) Microsoft. All rights reserved.
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from ms.common.fastapi.models.api_response import ApiResponseBaseModel
from ms.common.models.base import FrozenBaseModel


def test_serializes_to_camel_case():
    class MyResponse(ApiResponseBaseModel):
        my_field: int
        another_field: str

    response = MyResponse(
        my_field=123,
        another_field="test",
    )

    assert response.model_dump(by_alias=True) == {
        "myField": 123,
        "anotherField": "test",
    }


def test_serializes_nested_models_to_camel_case():
    class Nested(ApiResponseBaseModel):
        nested_field: str

    class MyResponse(ApiResponseBaseModel):
        my_field: int
        another_field: Nested

    response = MyResponse(
        my_field=123,
        another_field=Nested(nested_field="nested"),
    )

    assert response.model_dump(by_alias=True) == {
        "myField": 123,
        "anotherField": {"nestedField": "nested"},
    }


def test_throws_if_nested_model_does_not_inherit():
    class Nested(FrozenBaseModel):
        nested_field: str

    expected_error = "Nested model `Nested` must inherit from `ApiResponseBaseModel`"

    with pytest.raises(TypeError, match=expected_error):

        class C1(ApiResponseBaseModel):  # pyright: ignore[reportUnusedClass]
            my_field: int
            another_field: Nested

    with pytest.raises(TypeError, match=expected_error):

        class C2(ApiResponseBaseModel):  # pyright: ignore[reportUnusedClass]
            my_field: int
            another_field: list[Nested]

    with pytest.raises(TypeError, match=expected_error):

        class C3(ApiResponseBaseModel):  # pyright: ignore[reportUnusedClass]
            my_field: int
            another_field: dict[str, Nested]

    with pytest.raises(TypeError, match=expected_error):

        class C4(ApiResponseBaseModel):  # pyright: ignore[reportUnusedClass]
            my_field: int
            another_field: dict[str, list[Nested]]


def test_throws_validation_error_when_populated_by_alias():
    class MyResponse(ApiResponseBaseModel):
        my_field: int
        another_field: str

    with pytest.raises(ValidationError):
        MyResponse(
            myField=123,  # pyright: ignore[reportCallIssue]
            anotherField="test",  # pyright: ignore[reportCallIssue]
        )


def test_fastapi_response_serializes_to_camel_case():
    class MyResponse(ApiResponseBaseModel):
        my_field: int
        another_field: str

    app = FastAPI()

    @app.get("/")
    async def endpoint():  # pyright: ignore[reportUnusedFunction]
        return MyResponse(
            my_field=123,
            another_field="test",
        )

    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"myField": 123, "anotherField": "test"}


def test_fastapi_response_serializes_nested_models_to_camel_case():
    class Nested(ApiResponseBaseModel):
        nested_field: str

    class MyResponse(ApiResponseBaseModel):
        my_field: int
        another_field: Nested

    app = FastAPI()

    @app.get("/")
    async def endpoint():  # pyright: ignore[reportUnusedFunction]
        return MyResponse(
            my_field=123,
            another_field=Nested(nested_field="nested"),
        )

    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {
        "myField": 123,
        "anotherField": {"nestedField": "nested"},
    }
