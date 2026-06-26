# Copyright (c) Microsoft. All rights reserved.
import logging
from datetime import UTC
from datetime import datetime
from datetime import timezone

from starlette.types import Lifespan

from ms.common.fastapi.exception.error_code_mapper import ErrorCodeMapper
from ms.common.fastapi.middleware.exception_handler import ExceptionHandlerMiddleware
from ms.common.fastapi.models.health_response import HealthResponse
from ms.common.fastapi.settings import settings

from fastapi import FastAPI
from fastapi.routing import APIRoute


def custom_generate_unique_id(route: APIRoute) -> str:
    """
    Generate a unique operation ID for OpenAPI schema.

    Examples:
        POST score() → post-score → postScore()
        GET health() → get-health → getHealth()
    """
    method = list(route.methods)[0].lower() if route.methods else "unknown"
    name = route.name.replace("_", "-")
    return f"{method}-{name}"


def create_fastapi_app(
    title: str,
    error_mapper: ErrorCodeMapper,
    description: str | None = None,
    healthcheck_timezone: timezone = UTC,
    lifespan: Lifespan[FastAPI] | None = None,
    docs_url: str | None = "/docs",
    redoc_url: str | None = "/redoc",
    openapi_url: str | None = "/openapi.json",
) -> FastAPI:
    logging.basicConfig(
        level=settings.LOG_LEVEL,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        # Log to a file in CI environments to capture logs for test runs
        filename="app.log" if settings.CI else None,
    )

    app = FastAPI(
        title=title,
        description=description or "",  # FastAPI requires a string description (even if it's empty)
        lifespan=lifespan,
        generate_unique_id_function=custom_generate_unique_id,
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url=openapi_url,
    )

    app.add_middleware(
        ExceptionHandlerMiddleware,
        error_mapper=error_mapper,
    )

    # Define a method to handle the health check endpoint
    # so that the generated OpenAPI schema includes it with the correct operation ID (instead of "get-<lambda>")
    def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            timestamp=datetime.now(tz=healthcheck_timezone),
            build_id=settings.BUILD_ID,
        )

    app.add_api_route(
        "/health",
        endpoint=health,
        methods=["GET"],
    )

    return app
