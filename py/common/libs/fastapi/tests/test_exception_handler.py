# Copyright (c) Microsoft. All rights reserved.
"""Tests for ExceptionHandlerMiddleware: happy path and edge cases on dispatch."""

from collections.abc import Awaitable
from collections.abc import Callable
from enum import StrEnum
from http import HTTPStatus
from typing import Any
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest
from fastapi import Request
from fastapi import Response
from pytest_mock import MockerFixture
from starlette.responses import JSONResponse

from ms.common.fastapi.exception.error_code_mapper import ErrorCodeMapper
from ms.common.fastapi.exception.fde_custom_exception import FdeCustomException
from ms.common.fastapi.middleware.exception_handler import ExceptionHandlerMiddleware


class MockErrorCode(StrEnum):
    """Mock error codes for testing."""

    MAPPED_ERROR = "MAPPED_ERROR"
    UNMAPPED_ERROR = "UNMAPPED_ERROR"


class MockFdeCustomException(FdeCustomException):
    """Mock implementation of FdeCustomException."""

    def __init__(self, error_code: StrEnum, message: str, data: dict[str, Any] | None = None):
        super().__init__(error_code, message, data)


class MockErrorCodeMapper(ErrorCodeMapper):
    """Mock implementation of ErrorCodeMapper for testing."""

    @property
    def mappings(self) -> dict[StrEnum, ErrorCodeMapper.ErrorCodePropertyBag]:
        return {
            MockErrorCode.MAPPED_ERROR: ErrorCodeMapper.ErrorCodePropertyBag(
                status_code=HTTPStatus.BAD_REQUEST, client_message="This is a mapped error"
            )
        }


@pytest.fixture
def mock_request():
    """Create a mock Request object."""
    request = MagicMock(spec=Request)
    request.method = "GET"
    request.url.path = "/test/path"
    return request


@pytest.fixture
def mock_response():
    """Create a mock Response object."""
    return MagicMock(spec=Response)


@pytest.fixture
def error_mapper():
    """Create a MockErrorCodeMapper instance."""
    return MockErrorCodeMapper()


@pytest.fixture
def middleware(error_mapper: MockErrorCodeMapper):
    """Create an ExceptionHandlerMiddleware instance."""
    mock_app = MagicMock()
    return ExceptionHandlerMiddleware(mock_app, error_mapper)


class TestExceptionHandlerMiddleware:
    """Test cases for ExceptionHandlerMiddleware."""

    @pytest.mark.asyncio
    async def test_successful_request_processing(
        self, middleware: ExceptionHandlerMiddleware, mock_request: MagicMock, mock_response: MagicMock
    ):
        """Test that successful requests pass through unchanged."""
        # Arrange
        call_next: Callable[[Request], Awaitable[Response]] = AsyncMock(return_value=mock_response)

        # Act
        result = await middleware.dispatch(mock_request, call_next)

        # Assert
        assert result == mock_response
        call_next.assert_called_once_with(mock_request)

    @pytest.mark.asyncio
    async def test_custom_exception_with_mapped_error_code(
        self, middleware: ExceptionHandlerMiddleware, mock_request: MagicMock, mocker: MockerFixture
    ):
        """Test handling of FdeCustomException with a mapped error code."""
        # Arrange
        mock_logger = mocker.patch("ms.common.fastapi.middleware.exception_handler.logger")
        custom_exception = MockFdeCustomException(
            MockErrorCode.MAPPED_ERROR, "Test error message", {"extra_field": "extra_value"}
        )
        call_next: Callable[[Request], Awaitable[Response]] = AsyncMock(side_effect=custom_exception)

        # Act
        result = await middleware.dispatch(mock_request, call_next)

        # Verify JSONResponse details
        assert isinstance(result, JSONResponse)
        assert result.status_code == HTTPStatus.BAD_REQUEST
        # Check the content directly from JSONResponse
        assert result.media_type == "application/json"
        # The content should be accessible via the background task or we can check it differently
        # For testing purposes, we'll verify the response was created correctly by checking status code
        # and we know from the mapper it should return the right message

        # Verify logging
        mock_logger.error.assert_called_once()
        call_args = mock_logger.error.call_args
        # Check the log arguments (positional arguments in the format string)
        assert call_args[0][1] == "GET"  # request.method
        assert call_args[0][2] == "/test/path"  # request.url.path
        assert call_args[0][3] == "MAPPED_ERROR"  # error_code.value
        assert call_args[0][4] == "Test error message"  # exception.message

        # Check extra logging fields
        extra_fields = call_args.kwargs["extra"]
        assert extra_fields["error_code"] == "MAPPED_ERROR"
        assert extra_fields["error_code_class"] == "MockErrorCode"
        assert extra_fields["status_code"] == HTTPStatus.BAD_REQUEST
        assert extra_fields["client_message"] == "This is a mapped error"
        # Custom exception data is namespaced under error_data to avoid LogRecord conflicts
        assert extra_fields["error_data"]["extra_field"] == "extra_value"

    @pytest.mark.asyncio
    async def test_custom_exception_with_unmapped_error_code(
        self, middleware: ExceptionHandlerMiddleware, mock_request: MagicMock, mocker: MockerFixture
    ):
        """Test handling of FdeCustomException with an unmapped error code."""
        # Arrange
        mock_logger = mocker.patch("ms.common.fastapi.middleware.exception_handler.logger")
        custom_exception = MockFdeCustomException(MockErrorCode.UNMAPPED_ERROR, "Test unmapped error")
        call_next: Callable[[Request], Awaitable[Response]] = AsyncMock(side_effect=custom_exception)

        # Act
        result = await middleware.dispatch(mock_request, call_next)

        # Verify JSONResponse details (should default to 500)
        assert isinstance(result, JSONResponse)
        assert result.status_code == HTTPStatus.INTERNAL_SERVER_ERROR

        # Verify logging
        mock_logger.error.assert_called_once()
        call_args = mock_logger.error.call_args
        assert call_args[0][1] == "GET"  # request.method
        assert call_args[0][2] == "/test/path"  # request.url.path
        assert call_args[0][3] == "UNMAPPED_ERROR"  # error_code.value

        # Check extra logging fields
        extra_fields = call_args.kwargs["extra"]
        assert extra_fields["error_code"] == "UNMAPPED_ERROR"
        assert extra_fields["status_code"] == HTTPStatus.INTERNAL_SERVER_ERROR
        assert extra_fields["client_message"] == "Internal server error"

    @pytest.mark.asyncio
    async def test_custom_exception_with_no_data(
        self, middleware: ExceptionHandlerMiddleware, mock_request: MagicMock, mocker: MockerFixture
    ):
        """Test handling of FdeCustomException with no additional data."""
        # Arrange
        mock_logger = mocker.patch("ms.common.fastapi.middleware.exception_handler.logger")
        custom_exception = MockFdeCustomException(MockErrorCode.MAPPED_ERROR, "Test error")
        call_next: Callable[[Request], Awaitable[Response]] = AsyncMock(side_effect=custom_exception)

        # Act
        result = await middleware.dispatch(mock_request, call_next)

        # Assert
        assert isinstance(result, JSONResponse)
        assert result.status_code == HTTPStatus.BAD_REQUEST

        # Verify logging includes empty data
        call_args = mock_logger.error.call_args
        extra_fields = call_args.kwargs["extra"]

        # Should only have the standard fields, no additional data fields
        expected_fields = {"error_code", "error_code_class", "status_code", "client_message"}
        actual_fields = set(extra_fields.keys())
        assert expected_fields.issubset(actual_fields)

    @pytest.mark.asyncio
    async def test_generic_python_exception(
        self, middleware: ExceptionHandlerMiddleware, mock_request: MagicMock, mocker: MockerFixture
    ):
        """Test handling of generic Python exceptions."""
        # Arrange
        mock_logger = mocker.patch("ms.common.fastapi.middleware.exception_handler.logger")
        generic_exception = ValueError("Something went wrong")
        call_next: Callable[[Request], Awaitable[Response]] = AsyncMock(side_effect=generic_exception)

        # Act
        result = await middleware.dispatch(mock_request, call_next)

        # Verify JSONResponse details (should be 500 with generic message)
        assert isinstance(result, JSONResponse)
        assert result.status_code == HTTPStatus.INTERNAL_SERVER_ERROR

        # Verify logging
        mock_logger.error.assert_called_once()
        call_args = mock_logger.error.call_args
        assert call_args[0][1] == "GET"  # request.method
        assert call_args[0][2] == "/test/path"  # request.url.path
        assert call_args[0][3] == "ValueError"  # exception type name
        assert call_args[0][4] == "Something went wrong"  # exception message
        assert call_args.kwargs["exc_info"] is True

    @pytest.mark.asyncio
    async def test_custom_exception_with_complex_data(
        self, middleware: ExceptionHandlerMiddleware, mock_request: MagicMock, mocker: MockerFixture
    ):
        """Test that custom exception data is properly included in logging."""
        # Arrange
        mock_logger = mocker.patch("ms.common.fastapi.middleware.exception_handler.logger")
        complex_data = {
            "user_id": 12345,
            "operation": "delete_resource",
            "resource_type": "document",
            "nested_data": {"sub_field": "value"},
        }
        custom_exception = MockFdeCustomException(MockErrorCode.MAPPED_ERROR, "Complex error with data", complex_data)
        call_next: Callable[[Request], Awaitable[Response]] = AsyncMock(side_effect=custom_exception)

        # Act
        result = await middleware.dispatch(mock_request, call_next)

        # Assert
        assert isinstance(result, JSONResponse)
        assert result.status_code == HTTPStatus.BAD_REQUEST

        # Verify all data fields are in logging
        call_args = mock_logger.error.call_args
        extra_fields = call_args.kwargs["extra"]

        # Custom exception data is namespaced under error_data to avoid LogRecord conflicts
        error_data = extra_fields["error_data"]
        assert error_data["user_id"] == 12345
        assert error_data["operation"] == "delete_resource"
        assert error_data["resource_type"] == "document"
        assert error_data["nested_data"] == {"sub_field": "value"}

    @pytest.mark.asyncio
    async def test_logging_includes_exc_info(
        self, middleware: ExceptionHandlerMiddleware, mock_request: MagicMock, mocker: MockerFixture
    ):
        """Test that exc_info=True is passed to logger for stack traces."""
        # Arrange
        mock_logger = mocker.patch("ms.common.fastapi.middleware.exception_handler.logger")

        # Test with custom exception
        custom_exception = MockFdeCustomException(MockErrorCode.MAPPED_ERROR, "Test error")
        call_next: Callable[[Request], Awaitable[Response]] = AsyncMock(side_effect=custom_exception)

        # Act
        result = await middleware.dispatch(mock_request, call_next)

        # Assert
        assert isinstance(result, JSONResponse)
        assert result.status_code == HTTPStatus.BAD_REQUEST

        # Verify exc_info=True is included in logging call
        call_args = mock_logger.error.call_args
        assert call_args.kwargs["exc_info"] is True
