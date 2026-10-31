from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from app.schemas.common import SOURCE
from app.services.accuracy_service import ErrorRange, LevelErrorRanges
from app.services.forecast_service import UpcomingForecast


class EstimatedActualRange(BaseModel):
    lower_mw: float = Field(description="forecast - P90(error), floored at 0")
    upper_mw: float = Field(description="forecast - P10(error), floored at 0")


class LevelEstimatedActualRange(EstimatedActualRange):
    band_forecast_min_mw: float | None = Field(description="Forecast-level band used; null = open-ended")
    band_forecast_max_mw: float | None
    band_sample_count: int


class ForecastItem(BaseModel):
    start_time: datetime
    publish_time: datetime
    generation_mw: float
    settlement_date: date | None = None
    settlement_period: int | None = None
    estimated_actual_range: EstimatedActualRange | None = Field(
        default=None, description="Illustrative empirical historical range from all errors; dashboard only"
    )
    estimated_actual_range_by_level: LevelEstimatedActualRange | None = Field(
        default=None,
        description="Same, using only historical errors of forecasts at a similar level; dashboard only",
    )


class ForecastOut(BaseModel):
    source: str = SOURCE
    delivery_date: date
    day_start: datetime
    day_end: datetime
    as_of: datetime | None
    selection: str = Field(description="How the publication for each period was chosen")
    data_source: str
    available: bool
    complete: bool
    resolution_minutes: int | None
    expected_periods: int | None
    selected_publish_times: list[datetime] = Field(description="Publication(s) the items come from")
    latest_publish_time: datetime | None
    items: list[ForecastItem]
    warnings: list[str]

    @classmethod
    def of(
        cls,
        f: UpcomingForecast,
        error_range: ErrorRange | None = None,
        level_ranges: LevelErrorRanges | None = None,
    ) -> ForecastOut:
        items = []
        for row in f.items.itertuples(index=False):
            rng = error_range.actual_range_for(row.forecast_mw) if error_range else None
            band = level_ranges.band_for(row.forecast_mw) if level_ranges else None
            level_rng = band.error_range.actual_range_for(row.forecast_mw) if band else None
            items.append(
                ForecastItem(
                    start_time=row.start_time.to_pydatetime(),
                    publish_time=row.publish_time.to_pydatetime(),
                    generation_mw=row.forecast_mw,
                    settlement_date=None if _missing(row.settlement_date) else row.settlement_date,
                    settlement_period=None if _missing(row.settlement_period) else int(row.settlement_period),
                    estimated_actual_range=EstimatedActualRange(lower_mw=rng[0], upper_mw=rng[1]) if rng else None,
                    estimated_actual_range_by_level=LevelEstimatedActualRange(
                        lower_mw=level_rng[0],
                        upper_mw=level_rng[1],
                        band_forecast_min_mw=band.forecast_min_mw,
                        band_forecast_max_mw=band.forecast_max_mw,
                        band_sample_count=band.error_range.sample_count,
                    )
                    if band and level_rng
                    else None,
                )
            )
        pubs = f.publish_times
        selection = "Latest available publication for each period"
        if f.as_of:
            selection += f", published at or before {f.as_of.isoformat()}"
        return cls(
            delivery_date=f.delivery_date,
            day_start=f.day_start,
            day_end=f.day_end,
            as_of=f.as_of,
            selection=selection,
            data_source=f.source,
            available=f.available,
            complete=f.complete,
            resolution_minutes=f.resolution_minutes,
            expected_periods=f.expected_periods,
            selected_publish_times=pubs,
            latest_publish_time=pubs[-1] if pubs else None,
            items=items,
            warnings=f.warnings,
        )


def _missing(value: object) -> bool:
    return value is None or (isinstance(value, float) and value != value)


class EvolutionPoint(BaseModel):
    publish_time: datetime
    forecast_mw: float


class EvolutionOut(BaseModel):
    source: str = SOURCE
    target_start_time: datetime
    publication_count: int
    series: list[EvolutionPoint] = Field(description="All stored publications for the period, by publish_time")
    message: str | None = None
