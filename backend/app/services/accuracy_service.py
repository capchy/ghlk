"""Historical forecast accuracy: matching, metrics, buckets, distribution.

Sign convention: ``error_mw = forecast_mw - actual_mw``. Positive = wind was
overforecast, negative = underforecast.

Every accuracy endpoint goes through :meth:`AccuracyService.analyse`, so the
summary, weekly buckets, series and histogram always share one matched dataset.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from app.core.config import Settings
from app.core.timeutils import AnalysisWindow, local_day_bounds, seven_day_buckets
from app.db.repositories import ActualRepository, ForecastRepository
from app.services import forecast_selection_service as selection
from app.services.temporal_alignment_service import (
    AlignmentInfo,
    align_actuals,
    describe_alignment,
    half_hour_grid,
    target_grid,
)

MAX_UNMATCHED_EXAMPLES = 20


# --- Pure calculations --------------------------------------------------------


@dataclass(frozen=True)
class Metrics:
    sample_count: int
    mae_mw: float | None
    rmse_mw: float | None
    bias_mw: float | None
    wape_pct: float | None


def _r(value: float) -> float:
    return round(float(value), 3)


def compute_metrics(matched: pd.DataFrame) -> Metrics:
    """MAE, RMSE, bias and WAPE over matched rows (``forecast_mw``, ``actual_mw``)."""
    n = len(matched)
    if n == 0:
        return Metrics(0, None, None, None, None)
    forecast = matched["forecast_mw"].to_numpy(dtype=float)
    actual = matched["actual_mw"].to_numpy(dtype=float)
    err = forecast - actual
    abs_err = np.abs(err)
    total_actual = actual.sum()
    return Metrics(
        sample_count=n,
        mae_mw=_r(abs_err.mean()),
        rmse_mw=_r(math.sqrt((err**2).mean())),
        bias_mw=_r(err.mean()),
        # Undefined when total actual generation is zero (zero actuals are valid data).
        wape_pct=_r(abs_err.sum() / total_actual * 100) if total_actual > 0 else None,
    )


@dataclass(frozen=True)
class ErrorRange:
    available: bool
    sample_count: int
    min_samples: int
    p10_error_mw: float | None
    p50_error_mw: float | None
    p90_error_mw: float | None
    explanation: str

    def actual_range_for(self, forecast_mw: float) -> tuple[float, float] | None:
        """Empirical historical actual-generation range for a forecast ``F``:
        ``[F - P90, F - P10]``, floored at 0 MW (generation cannot be negative)."""
        if not self.available or self.p10_error_mw is None or self.p90_error_mw is None:
            return None
        return max(0.0, _r(forecast_mw - self.p90_error_mw)), max(0.0, _r(forecast_mw - self.p10_error_mw))


def error_range(matched: pd.DataFrame, min_samples: int) -> ErrorRange:
    n = len(matched)
    if n < min_samples:
        return ErrorRange(
            False,
            n,
            min_samples,
            None,
            None,
            None,
            f"Only {n} matched observations; at least {min_samples} are required for an empirical error range.",
        )
    p10, p50, p90 = np.quantile(matched["error_mw"].to_numpy(dtype=float), [0.1, 0.5, 0.9])
    return ErrorRange(
        True,
        n,
        min_samples,
        _r(p10),
        _r(p50),
        _r(p90),
        "Empirical historical error range from matched day-ahead forecast errors (forecast - actual). "
        "It describes past performance and is not a guaranteed confidence interval or procurement buffer.",
    )


@dataclass(frozen=True)
class LevelBand:
    """Error range for historical forecasts in ``[forecast_min_mw, forecast_max_mw)``; ``None`` = open-ended."""

    forecast_min_mw: float | None
    forecast_max_mw: float | None
    error_range: ErrorRange

    def contains(self, forecast_mw: float) -> bool:
        return (self.forecast_min_mw is None or forecast_mw >= self.forecast_min_mw) and (
            self.forecast_max_mw is None or forecast_mw < self.forecast_max_mw
        )


@dataclass(frozen=True)
class LevelErrorRanges:
    available: bool
    bands: list[LevelBand]
    explanation: str

    def band_for(self, forecast_mw: float) -> LevelBand | None:
        return next((b for b in self.bands if b.contains(forecast_mw)), None)

    def actual_range_for(self, forecast_mw: float) -> tuple[float, float] | None:
        band = self.band_for(forecast_mw)
        return band.error_range.actual_range_for(forecast_mw) if band else None


def level_error_ranges(matched: pd.DataFrame, target_bins: int, min_samples: int) -> LevelErrorRanges:
    """Empirical error ranges conditioned on forecast level.

    Errors grow with the forecast level (high-wind hours show much larger
    overforecasts), so one range for all levels is too wide at low wind and too
    narrow at high wind. Bands are forecast-level quantiles; the band count
    shrinks so each band keeps at least ``min_samples`` observations.
    """
    n = len(matched)
    bins = min(target_bins, n // min_samples)
    if bins < 2:
        return LevelErrorRanges(
            False,
            [],
            f"Only {n} matched observations; at least {2 * min_samples} are needed for level-conditioned "
            "ranges (two bands of the minimum sample size). Use the overall error range instead.",
        )
    forecast = matched["forecast_mw"].to_numpy(dtype=float)
    # Rounded edges are used for both binning and reporting, so they agree exactly.
    edges = np.unique(np.round(np.quantile(forecast, np.linspace(0, 1, bins + 1))[1:-1], 1))
    band_idx = np.searchsorted(edges, forecast, side="right")
    bounds: list[float | None] = [None, *(float(e) for e in edges), None]
    bands = [
        LevelBand(bounds[i], bounds[i + 1], error_range(matched[band_idx == i], min_samples))
        for i in range(len(bounds) - 1)
    ]
    return LevelErrorRanges(
        True,
        bands,
        f"Historical errors grouped into {len(bands)} forecast-level bands of roughly equal size; a new forecast "
        "uses the band its value falls in. Empirical and historical, not a guaranteed confidence interval.",
    )


@dataclass(frozen=True)
class DailyEnergyRatioRange:
    """Empirical range of whole-day ``actual / forecast`` energy ratios over complete delivery days.

    Ratios, not MWh differences: daily misses grow with the size of the day
    (windy days miss by far more GWh than calm ones), while the ratio stays
    comparable across calm and windy days.
    """

    available: bool
    day_count: int
    min_days: int
    p10_ratio: float | None
    p50_ratio: float | None
    p90_ratio: float | None
    explanation: str

    def actual_range_for(self, forecast_mwh: float) -> tuple[float, float] | None:
        """``[E x P10, E x P90]`` for a day's forecast energy ``E``."""
        if not self.available or self.p10_ratio is None or self.p90_ratio is None:
            return None
        return _r(forecast_mwh * self.p10_ratio), _r(forecast_mwh * self.p90_ratio)


DAILY_ENERGY_COLUMNS = ["delivery_date", "forecast_mwh", "actual_mwh", "error_mwh", "ratio"]


def daily_energy_errors(matched: pd.DataFrame, tz: ZoneInfo, resolution_minutes: int) -> pd.DataFrame:
    """Forecast and actual energy per local delivery date, for days whose every period matched.

    Partial days (window edges, missing data) are dropped: their totals are not
    comparable with a whole day's forecast. DST days count with 23 or 25 hours.
    """
    if matched.empty:
        return pd.DataFrame(columns=DAILY_ENERGY_COLUMNS)
    hours = resolution_minutes / 60
    period = timedelta(minutes=resolution_minutes)
    local_date = pd.to_datetime(matched["start_time"], utc=True).dt.tz_convert(tz).dt.date
    grouped = matched.groupby(local_date).agg(
        periods=("forecast_mw", "size"), forecast=("forecast_mw", "sum"), actual=("actual_mw", "sum")
    )
    expected = []
    for day in grouped.index:
        start, end = local_day_bounds(day, tz)
        expected.append(int((end - start) / period))
    complete = grouped[grouped["periods"] == expected]
    out = pd.DataFrame(
        {
            "delivery_date": complete.index,
            "forecast_mwh": complete["forecast"].to_numpy(dtype=float) * hours,
            "actual_mwh": complete["actual"].to_numpy(dtype=float) * hours,
        }
    )
    out["error_mwh"] = out["forecast_mwh"] - out["actual_mwh"]
    # Undefined for a zero-forecast day; such days are left out of the ratio range.
    out["ratio"] = (out["actual_mwh"] / out["forecast_mwh"]).where(out["forecast_mwh"] > 0)
    return out


def daily_energy_ratio_range(
    matched: pd.DataFrame, tz: ZoneInfo, resolution_minutes: int | None, min_days: int
) -> DailyEnergyRatioRange:
    """P10/P50/P90 of whole-day ``actual / forecast`` energy ratios.

    Daily totals are compared directly instead of summing hourly percentiles,
    which would misstate the width because neighbouring hours' errors are correlated.
    """
    days = daily_energy_errors(matched, tz, resolution_minutes) if resolution_minutes else pd.DataFrame()
    ratios = days["ratio"].dropna().to_numpy(dtype=float) if len(days) else np.array([])
    n = len(ratios)
    if n < min_days:
        return DailyEnergyRatioRange(
            False,
            n,
            min_days,
            None,
            None,
            None,
            f"Only {n} complete delivery days matched; at least {min_days} are required for a day-total range.",
        )
    p10, p50, p90 = np.quantile(ratios, [0.1, 0.5, 0.9])
    return DailyEnergyRatioRange(
        True,
        n,
        min_days,
        round(float(p10), 4),
        round(float(p50), 4),
        round(float(p90), 4),
        f"On 80% of the last {n} complete delivery days, actual wind energy was between {p10:.0%} and "
        f"{p90:.0%} of the day-ahead forecast. Empirical and historical; not a guarantee or procurement "
        "recommendation.",
    )


@dataclass(frozen=True)
class Histogram:
    bins: list[tuple[float, float, int]]
    bin_width_mw: float | None
    sample_count: int
    overforecast_count: int
    underforecast_count: int
    exact_count: int


def _nice_width(raw: float) -> float:
    magnitude = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if m * magnitude >= raw:
            return m * magnitude
    return 10 * magnitude


def error_histogram(matched: pd.DataFrame, target_bins: int) -> Histogram:
    """Histogram with 'nice' bin widths whose edges are multiples of the width,
    so 0 MW is always a bin edge when it falls inside the data range."""
    if matched.empty:
        return Histogram([], None, 0, 0, 0, 0)
    err = matched["error_mw"].to_numpy(dtype=float)
    span = max(err.max() - err.min(), 1.0)
    width = _nice_width(span / target_bins)
    lo = math.floor(err.min() / width) * width
    hi = math.ceil(err.max() / width) * width
    if hi <= lo:
        hi = lo + width
    edges = np.arange(lo, hi + width / 2, width)
    counts, edges = np.histogram(err, bins=edges)
    return Histogram(
        bins=[(_r(edges[i]), _r(edges[i + 1]), int(c)) for i, c in enumerate(counts)],
        bin_width_mw=_r(width),
        sample_count=len(err),
        overforecast_count=int((err > 0).sum()),
        underforecast_count=int((err < 0).sum()),
        exact_count=int((err == 0).sum()),
    )


@dataclass(frozen=True)
class PeriodMetrics:
    period_start: datetime
    period_end: datetime
    metrics: Metrics


def _between(matched: pd.DataFrame, start: datetime, end: datetime) -> pd.DataFrame:
    return matched[(matched["start_time"] >= start) & (matched["start_time"] < end)]


def bucket_metrics(matched: pd.DataFrame, window: AnalysisWindow) -> list[PeriodMetrics]:
    """Metrics for consecutive 7-day buckets (exactly four for a 28-day window)."""
    return [PeriodMetrics(s, e, compute_metrics(_between(matched, s, e))) for s, e in seven_day_buckets(window)]


def daily_metrics(matched: pd.DataFrame, window: AnalysisWindow, tz: ZoneInfo) -> list[PeriodMetrics]:
    """Metrics per local delivery date, clipped to the window."""
    out = []
    day = pd.Timestamp(window.start).tz_convert(tz).normalize()
    end_local = pd.Timestamp(window.end).tz_convert(tz)
    while day < end_local:
        # Add the calendar day in wall-clock time; +24h would drift on DST days.
        nxt = (day.tz_localize(None) + pd.Timedelta(days=1)).tz_localize(tz)
        start = max(day.tz_convert("UTC"), pd.Timestamp(window.start))
        end = min(nxt.tz_convert("UTC"), pd.Timestamp(window.end))
        out.append(
            PeriodMetrics(start.to_pydatetime(), end.to_pydatetime(), compute_metrics(_between(matched, start, end)))
        )
        day = nxt
    return out


# --- Analysis over stored data -------------------------------------------------------


@dataclass(frozen=True)
class UnmatchedPeriod:
    start_time: datetime
    reason: str


@dataclass
class Coverage:
    expected_actual_periods: int
    actual_periods_available: int
    missing_actual_periods: int
    latest_actual_start_time: datetime | None
    actual_data_complete: bool
    target_periods: int
    targets_with_eligible_forecast: int
    records_matched: int
    records_unmatched: int
    unmatched_by_reason: dict[str, int]
    unmatched_examples: list[UnmatchedPeriod]
    effective_start: datetime | None
    effective_end: datetime | None


@dataclass
class AnalysisResult:
    window: AnalysisWindow
    cutoff_time: time
    cutoff_timezone: str
    alignment: AlignmentInfo
    matched: pd.DataFrame
    coverage: Coverage
    warnings: list[str] = field(default_factory=list)


MATCHED_COLUMNS = ["start_time", "forecast_publish_time", "forecast_mw", "actual_mw", "error_mw", "absolute_error_mw"]


class AccuracyService:
    def __init__(self, settings: Settings, actuals: ActualRepository, forecasts: ForecastRepository) -> None:
        self._settings = settings
        self._actuals = actuals
        self._forecasts = forecasts
        self._business_tz = ZoneInfo(settings.business_timezone)
        self._cutoff_tz = ZoneInfo(settings.decision_cutoff_timezone)

    @property
    def business_tz(self) -> ZoneInfo:
        return self._business_tz

    def analyse(self, window: AnalysisWindow, cutoff_time: time) -> AnalysisResult:
        warnings: list[str] = []
        actuals = self._actuals.load(window.start, window.end)
        # A forecast published after the window end can never pass a cutoff inside it.
        publications = self._forecasts.load_publications(window.start, window.end, published_at_or_before=window.end)
        alignment = describe_alignment(publications["start_time"], self._settings.forecast_alignment_method)
        warnings.extend(alignment.warnings)

        expected_half_hours = half_hour_grid(window)
        latest_actual = actuals["start_time"].max() if not actuals.empty else None
        if actuals.empty:
            warnings.append("No actual generation is stored for this window. Run POST /api/v1/data/ingest.")
        if publications.empty:
            warnings.append("No forecast publications are stored for this window. Run POST /api/v1/data/ingest.")

        matched = pd.DataFrame(columns=MATCHED_COLUMNS)
        unmatched: list[UnmatchedPeriod] = []
        reasons: dict[str, int] = {"no_eligible_forecast": 0, "incomplete_actuals": 0, "awaiting_actuals": 0}
        targets_with_forecast = 0
        targets = pd.DatetimeIndex([], tz="UTC")

        if alignment.usable:
            assert alignment.forecast_resolution_minutes is not None
            targets = target_grid(window, alignment.forecast_resolution_minutes)
            aligned = align_actuals(actuals, targets, alignment)
            selected = selection.select_as_of_cutoff(publications, self._business_tz, cutoff_time, self._cutoff_tz)
            joined = aligned.merge(selected[["start_time", "publish_time", "forecast_mw"]], on="start_time", how="left")
            has_forecast = joined["forecast_mw"].notna()
            has_actual = joined["actual_mw"].notna()
            targets_with_forecast = int(has_forecast.sum())

            ok = joined[has_forecast & has_actual]
            matched = pd.DataFrame(
                {
                    "start_time": ok["start_time"],
                    "forecast_publish_time": ok["publish_time"],
                    "forecast_mw": ok["forecast_mw"].astype(float),
                    "actual_mw": ok["actual_mw"].astype(float),
                }
            ).reset_index(drop=True)
            matched["error_mw"] = matched["forecast_mw"] - matched["actual_mw"]
            matched["absolute_error_mw"] = matched["error_mw"].abs()

            for row in joined[~(has_forecast & has_actual)].itertuples(index=False):
                if pd.isna(row.forecast_mw):
                    reason = "no_eligible_forecast"
                elif latest_actual is None or row.start_time > latest_actual:
                    reason = "awaiting_actuals"
                else:
                    reason = "incomplete_actuals"
                reasons[reason] += 1
                if len(unmatched) < MAX_UNMATCHED_EXAMPLES:
                    unmatched.append(UnmatchedPeriod(row.start_time.to_pydatetime(), reason))
        else:
            warnings.append("Accuracy metrics are withheld because forecast-to-actual alignment is not established.")

        missing_actual = len(expected_half_hours) - len(actuals)
        if latest_actual is not None and latest_actual + timedelta(minutes=30) < window.end:
            gap_hours = (window.end - (latest_actual + timedelta(minutes=30))).total_seconds() / 3600
            warnings.append(
                f"Actual generation is available up to {(latest_actual + timedelta(minutes=30)).isoformat()}; "
                f"the last {gap_hours:.1f}h of the window has no actuals yet. Metrics use matched periods only."
            )
        if reasons["incomplete_actuals"]:
            warnings.append(f"{reasons['incomplete_actuals']} target period(s) lack complete half-hourly actuals.")
        if reasons["no_eligible_forecast"] and not publications.empty:
            warnings.append(
                f"{reasons['no_eligible_forecast']} target period(s) have no forecast published by the decision cutoff "
                "(check ingestion coverage or FORECAST_PUBLICATION_LOOKBACK_DAYS)."
            )

        resolution = timedelta(minutes=alignment.forecast_resolution_minutes or 30)
        coverage = Coverage(
            expected_actual_periods=len(expected_half_hours),
            actual_periods_available=len(actuals),
            missing_actual_periods=max(missing_actual, 0),
            latest_actual_start_time=latest_actual.to_pydatetime() if latest_actual is not None else None,
            actual_data_complete=missing_actual <= 0,
            target_periods=len(targets),
            targets_with_eligible_forecast=targets_with_forecast,
            records_matched=len(matched),
            records_unmatched=sum(reasons.values()),
            unmatched_by_reason=reasons,
            unmatched_examples=unmatched,
            effective_start=matched["start_time"].min().to_pydatetime() if len(matched) else None,
            effective_end=(matched["start_time"].max() + resolution).to_pydatetime() if len(matched) else None,
        )
        return AnalysisResult(
            window=window,
            cutoff_time=cutoff_time,
            cutoff_timezone=self._settings.decision_cutoff_timezone,
            alignment=alignment,
            matched=matched,
            coverage=coverage,
            warnings=warnings,
        )
