# Copyright (c) Microsoft. All rights reserved.
"""
Test infrastructure for ErrorCodeMapper class.

This module provides the test infrastructure needed to test the abstract ErrorCodeMapper class,
including concrete test implementations, test error codes, and fixtures.
"""

from enum import StrEnum
from http import HTTPStatus

from ms.common.fastapi.exception.error_code_mapper import ErrorCodeMapper


class MockErrorCode(StrEnum):
    """Mock error codes for testing ErrorCodeMapper functionality."""

    NOT_FOUND = "NOT_FOUND"
    UNAUTHORIZED = "UNAUTHORIZED"
    BAD_REQUEST = "BAD_REQUEST"
    FORBIDDEN = "FORBIDDEN"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    # This code will intentionally NOT be included in standard mappings
    UNKNOWN_CODE = "UNKNOWN_CODE"


class MockErrorCodeMapper(ErrorCodeMapper):
    """Concrete implementation of ErrorCodeMapper for testing purposes."""

    def __init__(self, custom_mappings: dict[StrEnum, ErrorCodeMapper.ErrorCodePropertyBag] | None = None):
        """
        Initialize with custom mappings for testing.

        Args:
            custom_mappings: Optional custom mappings to use instead of default ones
        """
        self._custom_mappings = custom_mappings or {}

    @property
    def mappings(self) -> dict[StrEnum, ErrorCodeMapper.ErrorCodePropertyBag]:
        """Return the configured mappings."""
        return self._custom_mappings


class TestGetStatusCode:
    """Test cases for the get_status_code() method."""

    def test_get_status_code_with_valid_error_codes(self):
        """Test get_status_code returns correct status codes for valid error codes."""
        mapper = MockErrorCodeMapper(
            {
                MockErrorCode.NOT_FOUND: ErrorCodeMapper.ErrorCodePropertyBag(
                    status_code=HTTPStatus.NOT_FOUND, client_message="The requested resource was not found"
                ),
                MockErrorCode.UNAUTHORIZED: ErrorCodeMapper.ErrorCodePropertyBag(
                    status_code=HTTPStatus.UNAUTHORIZED, client_message="Authentication is required"
                ),
                MockErrorCode.BAD_REQUEST: ErrorCodeMapper.ErrorCodePropertyBag(
                    status_code=HTTPStatus.BAD_REQUEST, client_message="The request is invalid"
                ),
                MockErrorCode.FORBIDDEN: ErrorCodeMapper.ErrorCodePropertyBag(
                    status_code=HTTPStatus.FORBIDDEN, client_message="Access to this resource is forbidden"
                ),
                MockErrorCode.INTERNAL_ERROR: ErrorCodeMapper.ErrorCodePropertyBag(
                    status_code=HTTPStatus.INTERNAL_SERVER_ERROR, client_message="An internal server error occurred"
                ),
                MockErrorCode.VALIDATION_ERROR: ErrorCodeMapper.ErrorCodePropertyBag(
                    status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                    client_message="Validation failed for the provided data",
                ),
                MockErrorCode.RATE_LIMITED: ErrorCodeMapper.ErrorCodePropertyBag(
                    status_code=HTTPStatus.TOO_MANY_REQUESTS,
                    client_message="Too many requests. Please try again later.",
                ),
            }
        )

        # Test each valid error code returns expected status
        assert mapper.get_status_code(MockErrorCode.NOT_FOUND) == HTTPStatus.NOT_FOUND
        assert mapper.get_status_code(MockErrorCode.UNAUTHORIZED) == HTTPStatus.UNAUTHORIZED
        assert mapper.get_status_code(MockErrorCode.BAD_REQUEST) == HTTPStatus.BAD_REQUEST
        assert mapper.get_status_code(MockErrorCode.FORBIDDEN) == HTTPStatus.FORBIDDEN
        assert mapper.get_status_code(MockErrorCode.INTERNAL_ERROR) == HTTPStatus.INTERNAL_SERVER_ERROR
        assert mapper.get_status_code(MockErrorCode.VALIDATION_ERROR) == HTTPStatus.UNPROCESSABLE_ENTITY
        assert mapper.get_status_code(MockErrorCode.RATE_LIMITED) == HTTPStatus.TOO_MANY_REQUESTS

    def test_get_status_code_with_empty_mappings(self):
        """Test get_status_code returns INTERNAL_SERVER_ERROR when mappings are empty."""
        empty_mapper = MockErrorCodeMapper()

        # Test that any error code returns default when mappings are empty
        assert empty_mapper.get_status_code(MockErrorCode.NOT_FOUND) == HTTPStatus.INTERNAL_SERVER_ERROR
        assert empty_mapper.get_status_code(MockErrorCode.UNAUTHORIZED) == HTTPStatus.INTERNAL_SERVER_ERROR
        assert empty_mapper.get_status_code(MockErrorCode.UNKNOWN_CODE) == HTTPStatus.INTERNAL_SERVER_ERROR


class TestGetClientMessage:
    """Test cases for the get_client_message() method."""

    def test_get_client_message_with_valid_error_codes(self):
        """Test get_client_message returns correct messages for valid error codes."""
        mapper = MockErrorCodeMapper(
            {
                MockErrorCode.NOT_FOUND: ErrorCodeMapper.ErrorCodePropertyBag(
                    status_code=HTTPStatus.NOT_FOUND, client_message="The requested resource was not found"
                ),
                MockErrorCode.UNAUTHORIZED: ErrorCodeMapper.ErrorCodePropertyBag(
                    status_code=HTTPStatus.UNAUTHORIZED, client_message="Authentication is required"
                ),
                MockErrorCode.BAD_REQUEST: ErrorCodeMapper.ErrorCodePropertyBag(
                    status_code=HTTPStatus.BAD_REQUEST, client_message="The request is invalid"
                ),
                MockErrorCode.FORBIDDEN: ErrorCodeMapper.ErrorCodePropertyBag(
                    status_code=HTTPStatus.FORBIDDEN, client_message="Access to this resource is forbidden"
                ),
                MockErrorCode.INTERNAL_ERROR: ErrorCodeMapper.ErrorCodePropertyBag(
                    status_code=HTTPStatus.INTERNAL_SERVER_ERROR, client_message="An internal server error occurred"
                ),
                MockErrorCode.VALIDATION_ERROR: ErrorCodeMapper.ErrorCodePropertyBag(
                    status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                    client_message="Validation failed for the provided data",
                ),
                MockErrorCode.RATE_LIMITED: ErrorCodeMapper.ErrorCodePropertyBag(
                    status_code=HTTPStatus.TOO_MANY_REQUESTS,
                    client_message="Too many requests. Please try again later.",
                ),
            }
        )

        # Test each valid error code returns expected message
        assert mapper.get_client_message(MockErrorCode.NOT_FOUND) == "The requested resource was not found"
        assert mapper.get_client_message(MockErrorCode.UNAUTHORIZED) == "Authentication is required"
        assert mapper.get_client_message(MockErrorCode.BAD_REQUEST) == "The request is invalid"
        assert mapper.get_client_message(MockErrorCode.FORBIDDEN) == "Access to this resource is forbidden"
        assert mapper.get_client_message(MockErrorCode.INTERNAL_ERROR) == "An internal server error occurred"
        assert mapper.get_client_message(MockErrorCode.VALIDATION_ERROR) == "Validation failed for the provided data"
        assert mapper.get_client_message(MockErrorCode.RATE_LIMITED) == "Too many requests. Please try again later."

    def test_get_client_message_with_empty_mappings(self):
        """Test get_client_message returns default message when mappings are empty."""
        empty_mapper = MockErrorCodeMapper()

        # Test that any error code returns default message when mappings are empty
        assert empty_mapper.get_client_message(MockErrorCode.NOT_FOUND) == "Internal server error"
        assert empty_mapper.get_client_message(MockErrorCode.UNAUTHORIZED) == "Internal server error"
        assert empty_mapper.get_client_message(MockErrorCode.UNKNOWN_CODE) == "Internal server error"
