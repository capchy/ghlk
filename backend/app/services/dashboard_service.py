"""Combines the upcoming forecast with historical accuracy for the dashboard."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Literal
from zoneinfo import ZoneInfo

from app.clients.elexon_client import ElexonError
from app.core.config import Settings
from app.core.timeutils import AnalysisWindow, decision_cutoff, utc_now
from app.services.accuracy_service import (
    AccuracyService,
    AnalysisResult,
    DailyEnergyRatioRange,
    ErrorRange,
    LevelErrorRanges,
    Metrics,
    PeriodMetrics,
    bucket_metrics,
    compute_metrics,
    daily_energy_ratio_range,
    error_range,
    level_error_ranges,
)
from app.services.forecast_service import ForecastService, UpcomingForecast


@dataclass
class Dashboard:
    forecast: UpcomingForecast | None
    analysis: AnalysisResult | None
    overall: Metrics | None
    error_range: ErrorRange | None
    level_ranges: LevelErrorRanges | None
    weekly: list[PeriodMetrics]
    warnings: list[str] = field(default_factory=list)
    # "decision_cutoff": forecast as it stood at the D-1 cutoff (already passed);
    # "latest": cutoff still ahead (or unknown), so the newest forecast is what a buyer would use.
    forecast_basis: Literal["decision_cutoff", "latest", "as_of"] = "latest"
    decision_at: datetime | None = None
    daily_energy_range: DailyEnergyRatioRange | None = None


class DashboardService:
    def __init__(self, settings: Settings, forecasts: ForecastService, accuracy: AccuracyService) -> None:
        self._settings = settings
        self._forecasts = forecasts
        self._accuracy = accuracy

    async def build(
        self,
        delivery_date: date,
        as_of: datetime | None,
        window: AnalysisWindow,
        cutoff_time: time | None,
    ) -> Dashboard:
        warnings: list[str] = []
        # Purpose: show the forecast available when the purchase decision is made.
        # Once the D-1 cutoff has passed, later publications were not available to
        # the buyer, so the forecast is taken as of the cutoff.
        decision_at = (
            decision_cutoff(delivery_date, cutoff_time, ZoneInfo(self._settings.decision_cutoff_timezone))
            if cutoff_time is not None
            else None
        )
        basis: Literal["decision_cutoff", "latest", "as_of"] = "latest"
        forecast_as_of = as_of
        if as_of is not None:
            basis = "as_of"
        elif decision_at is not None and decision_at <= utc_now():
            basis, forecast_as_of = "decision_cutoff", decision_at
        forecast: UpcomingForecast | None
        try:
            forecast = await self._forecasts.upcoming(delivery_date, forecast_as_of)
            warnings.extend(forecast.warnings)
        except ElexonError as exc:
            # Historical accuracy is still useful when the live forecast is not.
            forecast = None
            warnings.append(f"Upcoming forecast unavailable: {exc}")

        if cutoff_time is None:
            warnings.append(
                "Historical accuracy is not calculated: no decision cutoff is configured. "
                "Set DECISION_CUTOFF_TIME or pass decision_cutoff=HH:MM."
            )
            return Dashboard(forecast, None, None, None, None, [], warnings, basis, decision_at)

        analysis = self._accuracy.analyse(window, cutoff_time)
        warnings.extend(analysis.warnings)
        return Dashboard(
            forecast=forecast,
            analysis=analysis,
            overall=compute_metrics(analysis.matched),
            error_range=error_range(analysis.matched, self._settings.min_samples_for_error_range),
            level_ranges=level_error_ranges(
                analysis.matched, self._settings.error_range_level_bands, self._settings.min_samples_for_error_range
            ),
            weekly=bucket_metrics(analysis.matched, window),
            warnings=warnings,
            forecast_basis=basis,
            decision_at=decision_at,
            daily_energy_range=daily_energy_ratio_range(
                analysis.matched,
                self._accuracy.business_tz,
                analysis.alignment.forecast_resolution_minutes,
                self._settings.min_days_for_daily_energy_range,
            ),
        )
