"""Data availability and quality report for ``GET /api/v1/data/status``."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.core.config import Settings
from app.core.timeutils import AnalysisWindow, expected_settlement_periods, rolling_window, utc_now
from app.db.repositories import ActualRepository, ForecastRepository
from app.services.temporal_alignment_service import AlignmentInfo, describe_alignment, half_hour_grid

MAX_LISTED_DATES = 31


@dataclass(frozen=True)
class IncompleteDate:
    settlement_date: date
    expected_periods: int
    stored_periods: int


@dataclass
class DataStatus:
    actual: dict
    forecast: dict
    incomplete_dates: list[IncompleteDate]
    missing_actual_periods: int
    alignment: AlignmentInfo
    default_window: AnalysisWindow
    default_window_actual_periods: int
    default_window_expected_periods: int
    decision_cutoff_configured: bool
    decision_cutoff_time: time | None
    ready_for_default_analysis: bool
    warnings: list[str] = field(default_factory=list)


class StatusService:
    def __init__(self, settings: Settings, actuals: ActualRepository, forecasts: ForecastRepository) -> None:
        self._settings = settings
        self._actuals = actuals
        self._forecasts = forecasts
        self._tz = ZoneInfo(settings.business_timezone)

    def status(self) -> DataStatus:
        warnings: list[str] = []
        actual = self._actuals.stats()
        forecast = self._forecasts.stats()
        today = utc_now().astimezone(self._tz).date()

        incomplete: list[IncompleteDate] = []
        missing = 0
        if actual["count"]:
            first, last = actual["first_settlement_date"], actual["last_settlement_date"]
            counts = self._actuals.counts_by_settlement_date(first, last)
            day = first
            while day <= last:
                expected = expected_settlement_periods(day, self._tz)
                stored = counts.get(day, 0)
                # Today is still in progress, so a shortfall there is expected.
                if stored < expected and day < today:
                    missing += expected - stored
                    if len(incomplete) < MAX_LISTED_DATES:
                        incomplete.append(IncompleteDate(day, expected, stored))
                day += timedelta(days=1)
            if incomplete:
                warnings.append(
                    f"{len(incomplete)} past settlement date(s) are missing {missing} half-hourly actual period(s)."
                )
        else:
            warnings.append("No actual generation stored. Run POST /api/v1/data/ingest.")
        if not forecast["count"]:
            warnings.append("No forecast publications stored. Run POST /api/v1/data/ingest.")

        alignment = describe_alignment(self._forecasts.recent_target_times(), self._settings.forecast_alignment_method)
        warnings.extend(alignment.warnings)

        window = rolling_window(self._settings.default_analysis_window_days)
        in_window = len(self._actuals.load(window.start, window.end))
        expected_in_window = len(half_hour_grid(window))
        cutoff_ok = self._settings.decision_cutoff_time is not None
        if not cutoff_ok:
            warnings.append("DECISION_CUTOFF_TIME is not configured; accuracy endpoints require decision_cutoff=HH:MM.")
        needed_from = window.start - timedelta(days=self._settings.forecast_publication_lookback_days)
        forecasts_cover = forecast["first_publish_time"] is not None and forecast[
            "first_publish_time"
        ] <= needed_from + timedelta(days=1)
        if forecast["count"] and not forecasts_cover:
            warnings.append("Stored forecast publications do not reach back far enough for the default window.")
        ready = cutoff_ok and in_window > 0 and forecasts_cover and alignment.usable

        return DataStatus(
            actual=actual,
            forecast=forecast,
            incomplete_dates=incomplete,
            missing_actual_periods=missing,
            alignment=alignment,
            default_window=window,
            default_window_actual_periods=in_window,
            default_window_expected_periods=expected_in_window,
            decision_cutoff_configured=cutoff_ok,
            decision_cutoff_time=self._settings.decision_cutoff_time,
            ready_for_default_analysis=ready,
            warnings=warnings,
        )


def latest_generation_period(last_start: datetime | None) -> tuple[datetime, datetime] | None:
    return (last_start, last_start + timedelta(minutes=30)) if last_start else None
