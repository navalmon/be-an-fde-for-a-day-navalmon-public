# Copyright (c) Microsoft. All rights reserved.
from abc import ABC
from enum import StrEnum
from typing import Any


class FdeCustomException(Exception, ABC):
    """Class for custom exceptions in the application."""

    def __init__(self, error_code: StrEnum, message: str, data: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.data = data or {}
