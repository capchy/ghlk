from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import (
    SOURCE,
    AlignmentOut,
    CoverageOut,
    DecisionCutoffOut,
    ErrorRangeOut,
    LevelErrorRangesOut,
    MetricsOut,
    PeriodMetricsOut,
    WindowOut,
)
from app.schemas.forecast import ForecastOut
from app.services.dashboard_service import Dashboard


class HistoricalAccuracyOut(BaseModel):
    analysis_window: WindowOut
    decision_cutoff: DecisionCutoffOut
    alignment: AlignmentOut
    coverage: CoverageOut
    metrics: MetricsOut
    error_range: ErrorRangeOut
    error_range_by_level: LevelErrorRangesOut | None
    weekly: list[PeriodMetricsOut]


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
            )
        return cls(
            forecast=ForecastOut.of(d.forecast, d.error_range, d.level_ranges) if d.forecast else None,
            historical_accuracy=hist,
            forecast_basis=d.forecast_basis,
            decision_at=d.decision_at,
            warnings=d.warnings,
        )
