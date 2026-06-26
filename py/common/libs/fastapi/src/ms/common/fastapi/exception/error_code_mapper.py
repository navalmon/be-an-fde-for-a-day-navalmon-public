# Copyright (c) Microsoft. All rights reserved.
from abc import ABC
from abc import abstractmethod
from enum import StrEnum
from http import HTTPStatus

from ms.common.models.base import FrozenBaseModel


class ErrorCodeMapper(ABC):
    """Interface for mapping error codes to HTTP status codes and error messages."""

    class ErrorCodePropertyBag(FrozenBaseModel):
        status_code: HTTPStatus
        client_message: str

    @property
    @abstractmethod
    def mappings(self) -> dict[StrEnum, ErrorCodePropertyBag]: ...

    def get_status_code(self, error_code: StrEnum) -> HTTPStatus:
        """
        Get the HTTP status code for a given error code.

        Args:
            error_code: The error code enum value

        Returns:
            HTTP status code (e.g., 404, 400, 500)
        """
        return (
            self.mappings[error_code].status_code if error_code in self.mappings else HTTPStatus.INTERNAL_SERVER_ERROR
        )

    def get_client_message(self, error_code: StrEnum) -> str:
        """
        Get the client error message for a given error code.

        Args:
            error_code: The error code enum value

        Returns:
            Client error message string
        """
        return self.mappings[error_code].client_message if error_code in self.mappings else "Internal server error"
