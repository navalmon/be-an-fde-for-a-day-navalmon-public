# Copyright (c) Microsoft. All rights reserved.
import logging
from collections.abc import Awaitable
from collections.abc import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from ms.common.fastapi.exception.error_code_mapper import ErrorCodeMapper
from ms.common.fastapi.exception.fde_custom_exception import FdeCustomException

from fastapi import Request
from fastapi import Response

logger = logging.getLogger(__name__)


class ExceptionHandlerMiddleware(BaseHTTPMiddleware):
    """Middleware to handle exceptions globally across the application."""

    def __init__(self, app: ASGIApp, error_mapper: ErrorCodeMapper):
        """
        Initialize the exception handler middleware.

        Args:
            app: The ASGI application
            error_mapper: Mapper to convert error codes to HTTP status codes
        """
        super().__init__(app)
        self.error_mapper = error_mapper

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        """
        Process the request and handle any exceptions that occur.

        Args:
            request: The incoming HTTP request
            call_next: The next middleware or endpoint in the chain

        Returns:
            HTTP response, either from successful processing or error handling
        """
        try:
            response: Response = await call_next(request)
            return response
        except FdeCustomException as custom_exception:
            # Get status code and client message from the error mapper
            status_code = self.error_mapper.get_status_code(custom_exception.error_code)
            client_message = self.error_mapper.get_client_message(custom_exception.error_code)

            # Log the custom exception with full context including data
            logger.error(
                "Uncaught error in request %s %s: %s: %s",
                request.method,
                request.url.path,
                custom_exception.error_code.value,
                custom_exception.message,
                exc_info=True,
                extra={
                    "error_code": custom_exception.error_code.value,
                    "error_code_class": type(custom_exception.error_code).__name__,
                    "status_code": status_code,
                    "client_message": client_message,
                    "error_data": custom_exception.data,  # Namespaced to avoid LogRecord conflicts
                },
            )

            # Return a proper JSON response with the error details
            return JSONResponse(
                status_code=status_code,
                content={"detail": client_message},
            )
        except Exception as uncaught_exception:
            # Log the exception with full context
            logger.error(
                "Uncaught error in request %s %s: %s: %s",
                request.method,
                request.url.path,
                type(uncaught_exception).__name__,
                str(uncaught_exception),
                exc_info=True,
            )

            # Return a generic 500 error response for non-custom exceptions
            return JSONResponse(
                status_code=500,
                content={"detail": "Internal server error"},
            )
