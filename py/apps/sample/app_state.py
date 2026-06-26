"""Application-scoped clients and resources."""

import asyncio
from dataclasses import dataclass

import httpx
from config import Settings
from extraction.service import ExtractionService
from model_client import ModelClient
from orchestration.service import OrchestrationService
from triage.service import TriageService


@dataclass(slots=True)
class AppClients:
    """Clients initialized once per FastAPI application lifespan."""

    settings: Settings
    http_client: httpx.AsyncClient
    model_semaphore: asyncio.Semaphore
    model_client: ModelClient

    async def close(self) -> None:
        """Release app-scoped network resources."""
        await self.http_client.aclose()


@dataclass(slots=True)
class AppServices:
    """Task services used by FastAPI route handlers."""

    triage: TriageService
    extraction: ExtractionService
    orchestration: OrchestrationService


def create_app_clients(settings: Settings) -> AppClients:
    """Create shared clients for task services."""
    http_client = httpx.AsyncClient(timeout=httpx.Timeout(settings.http_timeout_seconds))
    model_semaphore = asyncio.Semaphore(settings.model_concurrency)
    return AppClients(
        settings=settings,
        http_client=http_client,
        model_semaphore=model_semaphore,
        model_client=ModelClient(
            settings=settings,
            http_client=http_client,
            model_semaphore=model_semaphore,
        ),
    )


def create_app_services(
    settings: Settings | None = None,
    model_client: ModelClient | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> AppServices:
    """Create task service instances."""
    return AppServices(
        triage=TriageService(settings=settings, model_client=model_client),
        extraction=ExtractionService(settings=settings, model_client=model_client),
        orchestration=OrchestrationService(settings=settings, http_client=http_client),
    )
