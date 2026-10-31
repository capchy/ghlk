from datetime import date, datetime, time
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import ContainerDep, build_window, to_utc_or_422
from app.schemas.dashboard import DashboardOut

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard", response_model=DashboardOut)
async def dashboard(
    container: ContainerDep,
    delivery_date: Annotated[date | None, Query(description="Europe/London delivery date (default: tomorrow)")] = None,
    as_of: Annotated[
        datetime | None,
        Query(description="Forecast publications at or before this instant; also ends the rolling window"),
    ] = None,
    window_days: Annotated[int | None, Query(ge=1, le=366)] = None,
    start_date: Annotated[date | None, Query()] = None,
    end_date: Annotated[date | None, Query()] = None,
    decision_cutoff: Annotated[time | None, Query(description="HH:MM on D-1")] = None,
) -> DashboardOut:
    """Upcoming forecast plus rolling historical accuracy in one call."""
    settings = container.settings
    as_of_utc = to_utc_or_422(as_of, "as_of")
    window = build_window(settings, window_days, start_date, end_date, end=as_of_utc)
    result = await container.dashboard.build(
        delivery_date or container.forecasts.default_delivery_date(),
        as_of_utc,
        window,
        decision_cutoff or settings.decision_cutoff_time,
    )
    return DashboardOut.of(result)
