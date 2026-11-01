"""Response building blocks shared across endpoints."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, time
from typing import Literal

from pydantic import BaseModel, Field

from app.core.timeutils import AnalysisWindow
from app.services.accuracy_service import (
    Coverage,
    DailyEnergyRatioRange,
    ErrorRange,
    LevelErrorRanges,
    Metrics,
    PeriodMetrics,
)
from app.services.temporal_alignment_service import AlignmentInfo

SOURCE = "Elexon Insights API (https://data.elexon.co.uk/bmrs/api/v1)"


class WindowOut(BaseModel):
    start: datetime = Field(description="Inclusive start (UTC)")
    end: datetime = Field(description="Exclusive end (UTC)")
    days: float
    mode: Literal["rolling", "custom"]

    @classmethod
    def of(cls, w: AnalysisWindow) -> WindowOut:
        return cls(start=w.start, end=w.end, days=round(w.days, 4), mode=w.mode)


class DecisionCutoffOut(BaseModel):
    time: str = Field(description="Wall-clock cutoff on the day before delivery, HH:MM")
    timezone: str
    rule: str

    @classmethod
    def of(cls, cutoff: time, tz: str) -> DecisionCutoffOut:
        hhmm = cutoff.strftime("%H:%M")
        return cls(
            time=hhmm,
            timezone=tz,
            rule=f"For delivery date D, the latest forecast published at or before {hhmm} {tz} on D-1 is used.",
        )


class AlignmentOut(BaseModel):
    status: Literal["direct", "inferred", "unresolved", "no_data"]
    forecast_resolution_minutes: int | None
    actual_resolution_minutes: int
    method: str
    description: str
    warnings: list[str]

    @classmethod
    def of(cls, a: AlignmentInfo) -> AlignmentOut:
        return cls(
            status=a.status,
            forecast_resolution_minutes=a.forecast_resolution_minutes,
            actual_resolution_minutes=a.actual_resolution_minutes,
            method=a.method,
            description=a.description,
            warnings=a.warnings,
        )


class UnmatchedPeriodOut(BaseModel):
    start_time: datetime
    reason: Literal["no_eligible_forecast", "incomplete_actuals", "awaiting_actuals"]


class CoverageOut(BaseModel):
    expected_actual_periods: int = Field(description="Half-hourly periods inside the window")
    actual_periods_available: int
    missing_actual_periods: int
    latest_actual_start_time: datetime | None
    actual_data_complete: bool
    target_periods: int = Field(description="Forecast-resolution periods fully inside the window")
    targets_with_eligible_forecast: int
    records_matched: int
    records_unmatched: int
    unmatched_by_reason: dict[str, int]
    unmatched_examples: list[UnmatchedPeriodOut] = Field(description="Up to 20 unmatched periods")
    effective_start: datetime | None = Field(description="First matched period start")
    effective_end: datetime | None = Field(description="End of the last matched period")

    @classmethod
    def of(cls, c: Coverage) -> CoverageOut:
        data = asdict(c)
        data["unmatched_examples"] = [UnmatchedPeriodOut(**u) for u in data["unmatched_examples"]]
        return cls(**data)


class MetricsOut(BaseModel):
    sample_count: int
    mae_mw: float | None = Field(description="Mean absolute error: mean(|forecast - actual|)")
    rmse_mw: float | None = Field(description="Root mean squared error")
    bias_mw: float | None = Field(description="mean(forecast - actual); positive = overforecast")
    wape_pct: float | None = Field(description="sum|forecast - actual| / sum(actual) x 100; null if sum(actual) = 0")

    @classmethod
    def of(cls, m: Metrics) -> MetricsOut:
        return cls(**asdict(m))


class ErrorRangeOut(BaseModel):
    label: str = "Empirical historical error range (not a guaranteed confidence interval)"
    available: bool
    sample_count: int
    min_samples: int
    p10_error_mw: float | None
    p50_error_mw: float | None
    p90_error_mw: float | None
    explanation: str

    @classmethod
    def of(cls, r: ErrorRange) -> ErrorRangeOut:
        return cls(**asdict(r))


class DailyEnergyRatioRangeOut(BaseModel):
    label: str = "Empirical historical range of whole-day actual / forecast energy (not a guaranteed confidence interval)"
    available: bool
    day_count: int = Field(description="Complete delivery days (every period matched, forecast > 0) used")
    min_days: int
    p10_ratio: float | None = Field(description="P10 of actual / forecast day energy")
    p50_ratio: float | None
    p90_ratio: float | None = Field(description="P90 of actual / forecast day energy")
    explanation: str

    @classmethod
    def of(cls, r: DailyEnergyRatioRange) -> DailyEnergyRatioRangeOut:
        return cls(**asdict(r))


class LevelBandOut(BaseModel):
    forecast_min_mw: float | None = Field(description="Inclusive lower forecast level; null = open-ended")
    forecast_max_mw: float | None = Field(description="Exclusive upper forecast level; null = open-ended")
    error_range: ErrorRangeOut


class LevelErrorRangesOut(BaseModel):
    label: str = "Empirical historical error ranges by forecast level (not guaranteed confidence intervals)"
    available: bool
    explanation: str
    bands: list[LevelBandOut]

    @classmethod
    def of(cls, r: LevelErrorRanges) -> LevelErrorRangesOut:
        return cls(
            available=r.available,
            explanation=r.explanation,
            bands=[
                LevelBandOut(
                    forecast_min_mw=b.forecast_min_mw,
                    forecast_max_mw=b.forecast_max_mw,
                    error_range=ErrorRangeOut.of(b.error_range),
                )
                for b in r.bands
            ],
        )


class PeriodMetricsOut(BaseModel):
    period_start: datetime
    period_end: datetime
    sample_count: int
    mae_mw: float | None
    rmse_mw: float | None
    bias_mw: float | None
    wape_pct: float | None

    @classmethod
    def of(cls, p: PeriodMetrics) -> PeriodMetricsOut:
        return cls(period_start=p.period_start, period_end=p.period_end, **asdict(p.metrics))
