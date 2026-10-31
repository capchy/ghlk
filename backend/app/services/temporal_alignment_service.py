"""Aligns half-hourly FUELHH actuals with the WINDFOR forecast resolution.

Findings (October 2026 data, see README): WINDFOR startTime values are on the
hour with 60-minute spacing and no settlementPeriod in the raw dataset; FUELHH
is half-hourly average MW. Elexon documents WINDFOR as values "for each hour"
without stating whether a value is the hourly mean or an on-the-hour figure, and
the data cannot distinguish the two (the candidate alignments differ by <2% MAE).
So the hourly rule is configurable and always reported as ``inferred``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

import numpy as np
import pandas as pd

from app.core.config import AlignmentMethod
from app.core.timeutils import AnalysisWindow

ACTUAL_RESOLUTION_MINUTES = 30
SUPPORTED_RESOLUTIONS = (30, 60)
# Share of consecutive gaps that must equal the modal gap to call the series regular.
_REGULARITY_THRESHOLD = 0.9

AlignmentStatus = Literal["direct", "inferred", "unresolved", "no_data"]


@dataclass(frozen=True)
class AlignmentInfo:
    forecast_resolution_minutes: int | None
    method: str
    status: AlignmentStatus
    description: str
    actual_resolution_minutes: int = ACTUAL_RESOLUTION_MINUTES
    warnings: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return self.status in ("direct", "inferred")


def detect_resolution(start_times: Iterable[datetime | pd.Timestamp]) -> tuple[int | None, list[str]]:
    """Infer the forecast target-period resolution from consecutive startTimes."""
    unique = pd.Series(sorted(set(pd.to_datetime(list(start_times), utc=True))))
    if len(unique) < 2:
        return None, ["Not enough forecast periods to determine the forecast resolution."]
    gaps = (unique.diff().dropna().dt.total_seconds() // 60).astype(int)
    modal = int(gaps.mode().iloc[0])
    share = float((gaps == modal).mean())
    if modal not in SUPPORTED_RESOLUTIONS:
        return None, [f"Unsupported forecast resolution: consecutive startTimes are {modal} minutes apart."]
    warnings = []
    if share < _REGULARITY_THRESHOLD:
        return None, [f"Irregular forecast startTimes: only {share:.0%} of gaps equal {modal} minutes."]
    if share < 1:
        warnings.append(
            f"{1 - share:.1%} of gaps between forecast periods differ from {modal} minutes (missing periods)."
        )
    return modal, warnings


def describe_alignment(start_times: Iterable[datetime | pd.Timestamp], method: AlignmentMethod) -> AlignmentInfo:
    times = list(start_times)
    if not times:
        return AlignmentInfo(None, "none", "no_data", "No forecast data available to determine alignment.")
    resolution, warnings = detect_resolution(times)
    if resolution is None:
        return AlignmentInfo(
            None,
            "none",
            "unresolved",
            "Forecast resolution could not be established; accuracy metrics are withheld.",
            warnings=warnings,
        )
    if resolution == ACTUAL_RESOLUTION_MINUTES:
        return AlignmentInfo(
            30,
            "direct_half_hour",
            "direct",
            "Half-hourly forecasts matched to actuals by UTC startTime.",
            warnings=warnings,
        )
    if method == "hourly_mean":
        description = (
            "Hourly forecast at T compared with the mean of the two half-hourly actuals starting at T and T+30min. "
            "Matched only when both half-hours exist."
        )
    else:
        description = "Hourly forecast at T compared with the half-hourly actual starting at T."
    warnings.append(
        "Elexon documents WINDFOR as hourly values but does not state whether each value is the mean over the hour "
        f"or an on-the-hour figure. The '{method}' rule is an inferred assumption (configurable via "
        "FORECAST_ALIGNMENT_METHOD); empirically the alternatives differ by under 2% in MAE."
    )
    return AlignmentInfo(60, method, "inferred", description, warnings=warnings)


def target_grid(window: AnalysisWindow, resolution_minutes: int) -> pd.DatetimeIndex:
    """Target periods fully inside the window: ``start <= T`` and ``T + resolution <= end``."""
    step = pd.Timedelta(minutes=resolution_minutes)
    first = pd.Timestamp(window.start).ceil(step)
    last = pd.Timestamp(window.end) - step
    if last < first:
        return pd.DatetimeIndex([], tz="UTC")
    return pd.date_range(first, last, freq=step)


def half_hour_grid(window: AnalysisWindow) -> pd.DatetimeIndex:
    return target_grid(window, ACTUAL_RESOLUTION_MINUTES)


def align_actuals(actuals: pd.DataFrame, targets: pd.DatetimeIndex, alignment: AlignmentInfo) -> pd.DataFrame:
    """Actual MW per target period.

    Returns ``start_time, actual_mw, actual_halves`` for every target. ``actual_mw``
    is NaN when the required half-hours are not all present - never zero.
    """
    by_time = actuals.set_index("start_time")["actual_mw"] if not actuals.empty else pd.Series(dtype=float)
    first = by_time.reindex(targets)
    if alignment.method == "hourly_mean":
        second = by_time.reindex(targets + timedelta(minutes=30))
        second.index = targets
        halves = first.notna().astype(int) + second.notna().astype(int)
        # MW is power: average the two half-hours, never sum them.
        actual = ((first + second) / 2).where(halves == 2)
    else:
        halves = first.notna().astype(int)
        actual = first
    return pd.DataFrame(
        {
            "start_time": targets,
            "actual_mw": actual.to_numpy(dtype=float),
            "actual_halves": halves.to_numpy(dtype=np.int64),
        }
    )
