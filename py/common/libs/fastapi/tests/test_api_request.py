# Copyright (c) Microsoft. All rights reserved.
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ms.common.fastapi.models.api_request import ApiRequestBaseModel
from ms.common.models.base import FrozenBaseModel


def test_deserializes_from_camel_case():
    class MyRequest(ApiRequestBaseModel):
        my_field: int
        another_field: str

    request = MyRequest.model_validate(
        {
            "myField": 123,
            "anotherField": "test",
        }
    )

    assert request.my_field == 123
    assert request.another_field == "test"


def test_deserializes_nested_models_from_camel_case():
    class Nested(ApiRequestBaseModel):
        nested_field: str

    class MyRequest(ApiRequestBaseModel):
        my_field: int
        another_field: Nested

    request = MyRequest.model_validate(
        {
            "myField": 123,
            "anotherField": {"nestedField": "nested"},
        }
    )

    assert request.my_field == 123
    assert request.another_field.nested_field == "nested"


def test_throws_if_nested_model_does_not_inherit():
    class Nested(FrozenBaseModel):
        nested_field: str

    expected_error = "Nested model `Nested` must inherit from `ApiRequestBaseModel`"

    with pytest.raises(TypeError, match=expected_error):

        class C1(ApiRequestBaseModel):  # pyright: ignore[reportUnusedClass]
            my_field: int
            another_field: Nested

    with pytest.raises(TypeError, match=expected_error):

        class C2(ApiRequestBaseModel):  # pyright: ignore[reportUnusedClass]
            my_field: int
            another_field: list[Nested]

    with pytest.raises(TypeError, match=expected_error):

        class C3(ApiRequestBaseModel):  # pyright: ignore[reportUnusedClass]
            my_field: int
            another_field: dict[str, Nested]

    with pytest.raises(TypeError, match=expected_error):

        class C4(ApiRequestBaseModel):  # pyright: ignore[reportUnusedClass]
            my_field: int
            another_field: dict[str, list[Nested]]


def test_allows_populating_by_name():
    class MyRequest(ApiRequestBaseModel):
        my_field: int
        another_field: str

    MyRequest(
        my_field=123,
        another_field="test",
    )


def test_fastapi_request_deserializes_from_camel_case():
    class MyRequest(ApiRequestBaseModel):
        my_field: int
        another_field: str

    app = FastAPI()

    payload = {
        "myField": 123,
        "anotherField": "test",
    }

    @app.post("/")
    async def endpoint(req: MyRequest):  # pyright: ignore[reportUnusedFunction]
        assert req.my_field == 123
        assert req.another_field == "test"
        return "ok"

    client = TestClient(app)
    response = client.post("/", json=payload)
    assert response.status_code == 200
    assert response.json() == "ok"


def test_fastapi_request_deserializes_nested_models_from_camel_case():
    class Nested(ApiRequestBaseModel):
        nested_field: str

    class MyRequest(ApiRequestBaseModel):
        my_field: int
        another_field: Nested

    app = FastAPI()

    payload = {
        "myField": 123,
        "anotherField": {"nestedField": "nested"},
    }

    @app.post("/")
    async def endpoint(req: MyRequest):  # pyright: ignore[reportUnusedFunction]
        assert req.my_field == 123
        assert req.another_field.nested_field == "nested"
        return "ok"

    client = TestClient(app)
    response = client.post("/", json=payload)
    assert response.status_code == 200
    assert response.json() == "ok"
