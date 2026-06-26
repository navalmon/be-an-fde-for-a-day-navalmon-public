# Copyright (c) Microsoft. All rights reserved.
from datetime import timedelta
from datetime import timezone
from enum import StrEnum
from unittest.mock import patch

import freezegun
from fastapi.testclient import TestClient

from ms.common.fastapi import create_fastapi_app
from ms.common.fastapi.exception.error_code_mapper import ErrorCodeMapper


class MockErrorCodeMapper(ErrorCodeMapper):
    @property
    def mappings(self) -> dict[StrEnum, ErrorCodeMapper.ErrorCodePropertyBag]:
        return {}


@freezegun.freeze_time("Jan 31st, 2020 13:45:00")
def test_creates_healthcheck_endpoint() -> None:
    app = create_fastapi_app(
        title="Test App",
        error_mapper=MockErrorCodeMapper(),
    )

    client = TestClient(app)

    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["timestamp"] == "2020-01-31T13:45:00Z"
    assert data["buildId"] is None


@freezegun.freeze_time("Jan 31st, 2020 13:45:00")
def test_creates_app_with_custom_healthcheck_timezone() -> None:
    app = create_fastapi_app(
        title="Test App",
        error_mapper=MockErrorCodeMapper(),
        healthcheck_timezone=timezone(timedelta(hours=-5)),
    )

    client = TestClient(app)

    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["timestamp"] == "2020-01-31T08:45:00-05:00"
    assert data["buildId"] is None


@freezegun.freeze_time("Jan 31st, 2020 13:45:00")
def test_healthcheck_includes_build_id_when_set() -> None:
    with patch("ms.common.fastapi.settings.settings.BUILD_ID", "abc123def456"):
        app = create_fastapi_app(
            title="Test App",
            error_mapper=MockErrorCodeMapper(),
        )

        client = TestClient(app)

        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["timestamp"] == "2020-01-31T13:45:00Z"
        assert data["buildId"] == "abc123def456"
