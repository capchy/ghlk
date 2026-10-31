from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import ContainerDep, to_utc_or_422
from app.schemas.forecast import EvolutionOut, EvolutionPoint, ForecastOut

router = APIRouter(prefix="/forecasts", tags=["forecasts"])


@router.get("", response_model=ForecastOut)
async def forecast_for_delivery_date(
    container: ContainerDep,
    delivery_date: Annotated[date | None, Query(description="Europe/London delivery date (default: tomorrow)")] = None,
    as_of: Annotated[
        datetime | None, Query(description="Only use publications at or before this instant (ISO 8601 with offset)")
    ] = None,
) -> ForecastOut:
    """Latest eligible forecast for each period of a delivery date."""
    target = delivery_date or container.forecasts.default_delivery_date()
    forecast = await container.forecasts.upcoming(target, to_utc_or_422(as_of, "as_of"))
    return ForecastOut.of(forecast)


@router.get("/evolution", response_model=EvolutionOut)
async def forecast_evolution(
    container: ContainerDep,
    target_start_time: Annotated[datetime, Query(description="Target period start, ISO 8601 with offset")],
) -> EvolutionOut:
    """Every stored publication for one target period, in publication order."""
    target = to_utc_or_422(target_start_time, "target_start_time")
    assert target is not None
    points = container.forecasts.evolution(target)
    return EvolutionOut(
        target_start_time=target,
        publication_count=len(points),
        series=[EvolutionPoint(publish_time=p, forecast_mw=mw) for p, mw in points],
        message=None
        if points
        else "No stored forecast publications for this period. Check the timestamp is on the forecast grid "
        "(WINDFOR periods start on the hour) and that the data has been ingested.",
    )
