"""FDEBench starter API with shared app foundation.

Run:
    cd py
    make setup     # one time, install deps
    make run       # start on :8000

Score:
    make eval      # score all 3 tasks (in a second terminal)

Every task endpoint returns schema-compliant JSON, so the eval harness runs
end to end. The app foundation centralizes settings, shared HTTP clients,
cost-scoring headers, and protocol-level validation behavior.
"""

from collections.abc import AsyncIterator
from collections.abc import Awaitable
from collections.abc import Callable
from contextlib import asynccontextmanager

from app_state import AppServices
from app_state import create_app_clients
from app_state import create_app_services
from config import Settings
from config import get_settings
from fastapi import FastAPI
from fastapi import Request
from fastapi import Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from models import ExtractRequest
from models import ExtractResponse
from models import OrchestrateRequest
from models import OrchestrateResponse
from models import TriageRequest
from models import TriageResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

SCORED_ENDPOINTS = frozenset({"/triage", "/extract", "/orchestrate"})
MODEL_HEADER_NAME = "X-Model-Name"


class ScoredEndpointMiddleware(BaseHTTPMiddleware):
    """Apply scoring headers and protocol checks consistently across task endpoints."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        super().__init__(app)
        self._settings = settings

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        if request.url.path in SCORED_ENDPOINTS and request.method == "POST":
            content_type = request.headers.get("content-type", "")
            if content_type and "application/json" not in content_type.lower():
                return self._json_response_for_path(
                    request.url.path,
                    status_code=415,
                    content={"detail": "Unsupported content type. Use application/json."},
                )

        response = await call_next(request)
        if request.url.path in SCORED_ENDPOINTS and MODEL_HEADER_NAME not in response.headers:
            response.headers[MODEL_HEADER_NAME] = self._settings.model_name_for_path(request.url.path)
        return response

    def _json_response_for_path(self, path: str, *, status_code: int, content: dict) -> JSONResponse:
        return JSONResponse(
            status_code=status_code,
            content=content,
            headers={MODEL_HEADER_NAME: self._settings.model_name_for_path(path)},
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create shared clients once per process and close them on shutdown."""
    settings = app.state.settings if hasattr(app.state, "settings") else get_settings()
    app.state.clients = create_app_clients(settings)
    app.state.services = create_app_services(
        settings=settings,
        model_client=app.state.clients.model_client,
        http_client=app.state.clients.http_client,
    )
    try:
        yield
    finally:
        await app.state.clients.close()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the FastAPI application."""
    resolved_settings = settings or get_settings()
    services = create_app_services(settings=resolved_settings)
    app = FastAPI(title=resolved_settings.service_name, lifespan=lifespan)
    app.state.settings = resolved_settings
    app.state.services = services
    app.add_middleware(ScoredEndpointMiddleware, settings=resolved_settings)
    app.add_exception_handler(RequestValidationError, request_validation_exception_handler)
    register_routes(app, resolved_settings, services)
    return app


def _add_headers(response: Response, settings: Settings, path: str) -> None:
    """Add cost-tracking headers. The platform reads X-Model-Name for cost scoring."""
    response.headers[MODEL_HEADER_NAME] = settings.model_name_for_path(path)


def _validation_status_code(exc: RequestValidationError) -> int:
    """Return 400 for malformed JSON and 422 for schema validation errors."""
    for error in exc.errors():
        if error.get("type") == "json_invalid":
            return 400
    return 422


async def request_validation_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Return stable JSON for validation failures and preserve model cost headers."""
    if not isinstance(exc, RequestValidationError):
        raise exc

    settings = request.app.state.settings if hasattr(request.app.state, "settings") else get_settings()
    headers = {}
    if request.url.path in SCORED_ENDPOINTS:
        headers[MODEL_HEADER_NAME] = settings.model_name_for_path(request.url.path)
    return JSONResponse(
        status_code=_validation_status_code(exc),
        content={"detail": jsonable_encoder(exc.errors())},
        headers=headers,
    )


def register_routes(app: FastAPI, settings: Settings, services: AppServices) -> None:
    """Register all API routes."""

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.post("/triage")
    async def triage(req: TriageRequest, response: Response, request: Request) -> TriageResponse:
        _add_headers(response, settings, "/triage")
        active_services = request.app.state.services if hasattr(request.app.state, "services") else services
        return await active_services.triage.triage(req)

    @app.post("/extract")
    async def extract(req: ExtractRequest, response: Response, request: Request) -> ExtractResponse:
        _add_headers(response, settings, "/extract")
        active_services = request.app.state.services if hasattr(request.app.state, "services") else services
        return await active_services.extraction.extract(req)

    @app.post("/orchestrate")
    async def orchestrate(req: OrchestrateRequest, response: Response, request: Request) -> OrchestrateResponse:
        _add_headers(response, settings, "/orchestrate")
        active_services = request.app.state.services if hasattr(request.app.state, "services") else services
        return await active_services.orchestration.orchestrate(req)


app = create_app()
