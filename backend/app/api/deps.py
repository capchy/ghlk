"""Dependency wiring and shared query parameters.

No ``from __future__ import annotations`` here: FastAPI inspects these
signatures at runtime.
"""

import threading
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import Depends, HTTPException, Query, Request
from sqlalchemy.engine import Engine

from app.core.config import Settings
from app.core.timeutils import AnalysisWindow, custom_window, ensure_utc, rolling_window
from app.services.accuracy_service import AccuracyService
from app.services.dashboard_service import DashboardService
from app.services.forecast_service import ForecastService
from app.services.ingestion_service import IngestionService
from app.services.status_service import StatusService


@dataclass
class Container:
    settings: Settings
    ingestion: IngestionService
    accuracy: AccuracyService
    forecasts: ForecastService
    dashboard: DashboardService
    status: StatusService
    engine: Engine


_container_lock = threading.Lock()


def get_container(request: Request) -> Container:
    state = request.app.state
    container = getattr(state, "container", None)
    if container is None:
        # Lifespan did not run (some serverless runtimes skip it): build once, lazily.
        with _container_lock:
            container = getattr(state, "container", None)
            if container is None:
                container = state.container = state.container_factory()
    return container


ContainerDep = Annotated[Container, Depends(get_container)]


def to_utc_or_422(value: datetime | None, name: str) -> datetime | None:
    if value is None:
        return None
    try:
        return ensure_utc(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{name}: {exc}") from exc


def build_window(
    settings: Settings,
    window_days: int | None,
    start_date: date | None,
    end_date: date | None,
    end: datetime | None = None,
) -> AnalysisWindow:
    if (start_date is None) != (end_date is None):
        raise HTTPException(status_code=422, detail="start_date and end_date must be supplied together")
    if start_date is not None and end_date is not None:
        if (end_date - start_date).days > 366:
            raise HTTPException(status_code=422, detail="custom range may not exceed 366 days")
        try:
            return custom_window(start_date, end_date, ZoneInfo(settings.business_timezone))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return rolling_window(window_days or settings.default_analysis_window_days, end)


class AnalysisQuery:
    """Window and decision-cutoff parameters shared by the accuracy endpoints."""

    def __init__(
        self,
        container: ContainerDep,
        window_days: Annotated[
            int | None, Query(ge=1, le=366, description="Rolling window length in days (default 28)")
        ] = None,
        start_date: Annotated[
            date | None, Query(description="Custom range start (Europe/London date, inclusive)")
        ] = None,
        end_date: Annotated[date | None, Query(description="Custom range end (Europe/London date, inclusive)")] = None,
        decision_cutoff: Annotated[
            time | None,
            Query(description="Day-ahead cutoff HH:MM on D-1 (overrides DECISION_CUTOFF_TIME)"),
        ] = None,
    ) -> None:
        settings = container.settings
        self.window = build_window(settings, window_days, start_date, end_date)
        self.cutoff = decision_cutoff or settings.decision_cutoff_time

    def required_cutoff(self) -> time:
        if self.cutoff is None:
            raise HTTPException(
                status_code=422,
                detail="A day-ahead decision cutoff is required for historical accuracy. Configure "
                "DECISION_CUTOFF_TIME or pass decision_cutoff=HH:MM (interpreted in DECISION_CUTOFF_TIMEZONE).",
            )
        return self.cutoff


AnalysisDep = Annotated[AnalysisQuery, Depends()]
