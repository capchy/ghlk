from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import (
    SOURCE,
    AlignmentOut,
    CoverageOut,
    DailyEnergyRatioRangeOut,
    DecisionCutoffOut,
    ErrorRangeOut,
    LevelErrorRangesOut,
    MetricsOut,
    PeriodMetricsOut,
    WindowOut,
)
from app.schemas.forecast import ForecastOut
from app.services.accuracy_service import DailyEnergyRatioRange
from app.services.dashboard_service import Dashboard
from app.services.forecast_service import UpcomingForecast


class HistoricalAccuracyOut(BaseModel):
    analysis_window: WindowOut
    decision_cutoff: DecisionCutoffOut
    alignment: AlignmentOut
    coverage: CoverageOut
    metrics: MetricsOut
    error_range: ErrorRangeOut
    error_range_by_level: LevelErrorRangesOut | None
    weekly: list[PeriodMetricsOut]
    daily_energy_ratio_range: DailyEnergyRatioRangeOut | None


class EstimatedDayEnergyRange(BaseModel):
    lower_mwh: float = Field(description="day forecast x P10(actual / forecast)")
    upper_mwh: float = Field(description="day forecast x P90(actual / forecast)")
    day_count: int = Field(description="Complete historical delivery days the range comes from")


class ForecastDayEnergyOut(BaseModel):
    forecast_mwh: float = Field(description="Sum of the day's forecast values x period length")
    complete: bool = Field(description="False when some periods of the day have no forecast yet")
    estimated_actual_range: EstimatedDayEnergyRange | None = Field(
        description="Null when the day is incomplete or too few complete historical days exist"
    )
    explanation: str | None = Field(description="Why the range is missing, when it is")

    @classmethod
    def of(cls, f: UpcomingForecast, daily: DailyEnergyRatioRange | None) -> ForecastDayEnergyOut | None:
        if not f.available or not f.resolution_minutes:
            return None
        total = round(float(f.items["forecast_mw"].sum()) * f.resolution_minutes / 60, 3)
        rng = daily.actual_range_for(total) if daily and f.complete else None
        if rng:
            explanation = None
        elif not f.complete:
            explanation = "The day's forecast is incomplete, so it is not compared with whole-day errors."
        else:
            explanation = daily.explanation if daily else "Historical accuracy needs a decision cutoff."
        return cls(
            forecast_mwh=total,
            complete=f.complete,
            estimated_actual_range=EstimatedDayEnergyRange(lower_mwh=rng[0], upper_mwh=rng[1], day_count=daily.day_count)
            if rng and daily
            else None,
            explanation=explanation,
        )


class DashboardOut(BaseModel):
    source: str = SOURCE
    scope_note: str = (
        "Wind generation forecast and historical accuracy only. No demand data or procurement "
        "volumes are calculated; estimated actual ranges are illustrative, not recommendations."
    )
    forecast: ForecastOut | None = Field(description="Forecast for the delivery date (see forecast_basis)")
    forecast_basis: Literal["decision_cutoff", "latest", "as_of"] = Field(
        description="decision_cutoff: as published by the D-1 cutoff, which has passed; "
        "latest: cutoff not yet reached (or not configured); as_of: caller-supplied as_of"
    )
    decision_at: datetime | None = Field(description="Decision cutoff instant for the delivery date (UTC)")
    forecast_day_energy: ForecastDayEnergyOut | None = Field(
        description="Delivery-day forecast total with an empirical range from past whole-day errors"
    )
    historical_accuracy: HistoricalAccuracyOut | None = Field(description="Null when no decision cutoff is configured")
    warnings: list[str]

    @classmethod
    def of(cls, d: Dashboard) -> DashboardOut:
        hist = None
        if d.analysis is not None and d.overall is not None and d.error_range is not None:
            a = d.analysis
            hist = HistoricalAccuracyOut(
                analysis_window=WindowOut.of(a.window),
                decision_cutoff=DecisionCutoffOut.of(a.cutoff_time, a.cutoff_timezone),
                alignment=AlignmentOut.of(a.alignment),
                coverage=CoverageOut.of(a.coverage),
                metrics=MetricsOut.of(d.overall),
                error_range=ErrorRangeOut.of(d.error_range),
                error_range_by_level=LevelErrorRangesOut.of(d.level_ranges) if d.level_ranges else None,
                weekly=[PeriodMetricsOut.of(p) for p in d.weekly],
                daily_energy_ratio_range=DailyEnergyRatioRangeOut.of(d.daily_energy_range)
                if d.daily_energy_range
                else None,
            )
        return cls(
            forecast=ForecastOut.of(d.forecast, d.error_range, d.level_ranges) if d.forecast else None,
            historical_accuracy=hist,
            forecast_day_energy=ForecastDayEnergyOut.of(d.forecast, d.daily_energy_range) if d.forecast else None,
            forecast_basis=d.forecast_basis,
            decision_at=d.decision_at,
            warnings=d.warnings,
        )
