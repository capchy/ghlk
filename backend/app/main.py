"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.deps import Container
from app.api.v1.endpoints import health
from app.api.v1.router import api_router
from app.clients.elexon_client import ElexonClient, ElexonError
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.db.database import init_db, make_engine, make_session_factory
from app.db.repositories import ActualRepository, ForecastRepository
from app.services.accuracy_service import AccuracyService
from app.services.dashboard_service import DashboardService
from app.services.forecast_service import ForecastService
from app.services.ingestion_service import IngestionService
from app.services.status_service import StatusService


def build_client(settings: Settings) -> ElexonClient:
    return ElexonClient(
        settings.elexon_base_url,
        timeout=settings.elexon_timeout_seconds,
        max_retries=settings.elexon_max_retries,
        backoff_base=settings.elexon_backoff_base_seconds,
        max_retry_after=settings.elexon_max_retry_after_seconds,
        fuelhh_chunk_days=settings.elexon_fuelhh_chunk_days,
        windfor_chunk_days=settings.elexon_windfor_chunk_days,
    )


def build_container(settings: Settings, client: ElexonClient | None = None) -> Container:
    engine = make_engine(settings.database_url)
    init_db(engine)
    sessions = make_session_factory(engine)
    elexon = client or build_client(settings)
    actuals, forecasts = ActualRepository(sessions), ForecastRepository(sessions)
    accuracy = AccuracyService(settings, actuals, forecasts)
    forecast_service = ForecastService(settings, elexon, forecasts)
    return Container(
        settings=settings,
        ingestion=IngestionService(settings, elexon, actuals, forecasts),
        accuracy=accuracy,
        forecasts=forecast_service,
        dashboard=DashboardService(settings, forecast_service, accuracy),
        status=StatusService(settings, actuals, forecasts),
        engine=engine,
    )


def create_app(settings: Settings | None = None, client: ElexonClient | None = None) -> FastAPI:
    """Build the app; ``client`` may be injected (tests use a mocked transport)."""
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        container = build_container(settings, client)
        app.state.container = container
        try:
            yield
        finally:
            container.engine.dispose()

    app = FastAPI(
        title="GB Wind Forecast Accuracy API",
        version="1.0.0",
        description=(
            "Elexon WINDFOR wind generation forecasts compared with FUELHH actual wind outturn over a rolling "
            "28-day window, plus the latest forecast for upcoming delivery dates. Graph-ready JSON for a React "
            "dashboard. Does not calculate electricity procurement volumes."
        ),
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.exception_handler(ElexonError)
    async def elexon_error(request: Request, exc: ElexonError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": f"Elexon Insights API unavailable: {exc}"})

    # Serverless runtimes may skip ASGI lifespan events; build the container on
    # first use in that case (see app.api.deps.get_container).
    app.state.container_factory = lambda: build_container(settings, client)
    app.include_router(health.router)
    app.include_router(api_router)
    return app


app = create_app()
