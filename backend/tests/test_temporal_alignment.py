from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from app.core.timeutils import AnalysisWindow, custom_window, expected_settlement_periods, local_day_bounds
from app.services.temporal_alignment_service import (
    align_actuals,
    describe_alignment,
    detect_resolution,
    half_hour_grid,
    target_grid,
)
from tests.conftest import utc

LONDON = ZoneInfo("Europe/London")


def half_hours(start: datetime, n: int, step_minutes: int = 30) -> list[datetime]:
    return [start + timedelta(minutes=step_minutes * i) for i in range(n)]


def actual_frame(times: list[datetime], values: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"start_time": pd.to_datetime(times, utc=True), "actual_mw": values})


def test_detects_hourly_and_half_hourly_resolution() -> None:
    assert detect_resolution(half_hours(utc(2026, 10, 1), 48, 60))[0] == 60
    assert detect_resolution(half_hours(utc(2026, 10, 1), 48, 30))[0] == 30


def test_unsupported_or_irregular_resolution_is_unresolved() -> None:
    resolution, warnings = detect_resolution(half_hours(utc(2026, 10, 1), 10, 15))
    assert resolution is None and "Unsupported" in warnings[0]
    irregular = [
        utc(2026, 10, 1, 0),
        utc(2026, 10, 1, 1),
        utc(2026, 10, 1, 1, 30),
        utc(2026, 10, 1, 3),
        utc(2026, 10, 1, 3, 30),
    ]
    resolution, warnings = detect_resolution(irregular)
    assert resolution is None
    info = describe_alignment(irregular, "hourly_mean")
    assert info.status == "unresolved" and not info.usable


def test_alignment_status_reporting() -> None:
    assert describe_alignment([], "hourly_mean").status == "no_data"
    direct = describe_alignment(half_hours(utc(2026, 10, 1), 10, 30), "hourly_mean")
    assert direct.status == "direct" and direct.method == "direct_half_hour"
    hourly = describe_alignment(half_hours(utc(2026, 10, 1), 10, 60), "hourly_mean")
    assert hourly.status == "inferred" and hourly.warnings  # assumption is always surfaced


def test_hourly_mean_averages_two_half_hours() -> None:
    window = AnalysisWindow(utc(2026, 10, 1), utc(2026, 10, 1, 2), "custom")
    alignment = describe_alignment(half_hours(utc(2026, 10, 1), 2, 60), "hourly_mean")
    actuals = actual_frame(half_hours(utc(2026, 10, 1), 4), [100, 200, 300, 0])
    aligned = align_actuals(actuals, target_grid(window, 60), alignment)
    # Mean, not sum: MW is power. A zero actual is valid.
    assert aligned["actual_mw"].tolist() == [150, 150]


def test_hourly_mean_requires_both_half_hours() -> None:
    window = AnalysisWindow(utc(2026, 10, 1), utc(2026, 10, 1, 2), "custom")
    alignment = describe_alignment(half_hours(utc(2026, 10, 1), 2, 60), "hourly_mean")
    actuals = actual_frame([utc(2026, 10, 1, 0), utc(2026, 10, 1, 1), utc(2026, 10, 1, 1, 30)], [100, 300, 500])
    aligned = align_actuals(actuals, target_grid(window, 60), alignment)
    assert np.isnan(aligned["actual_mw"].iloc[0])  # missing, never zero
    assert aligned["actual_halves"].tolist() == [1, 2]
    assert aligned["actual_mw"].iloc[1] == 400


def test_start_period_method_uses_half_hour_starting_at_t() -> None:
    window = AnalysisWindow(utc(2026, 10, 1), utc(2026, 10, 1, 2), "custom")
    alignment = describe_alignment(half_hours(utc(2026, 10, 1), 2, 60), "start_period")
    actuals = actual_frame(half_hours(utc(2026, 10, 1), 4), [100, 200, 300, 400])
    assert align_actuals(actuals, target_grid(window, 60), alignment)["actual_mw"].tolist() == [100, 300]


def test_half_hourly_forecasts_match_directly() -> None:
    window = AnalysisWindow(utc(2026, 10, 1), utc(2026, 10, 1, 1), "custom")
    alignment = describe_alignment(half_hours(utc(2026, 10, 1), 4, 30), "hourly_mean")
    actuals = actual_frame(half_hours(utc(2026, 10, 1), 2), [100, 200])
    assert align_actuals(actuals, target_grid(window, 30), alignment)["actual_mw"].tolist() == [100, 200]


def test_target_grid_only_includes_whole_periods_inside_window() -> None:
    window = AnalysisWindow(utc(2026, 10, 1, 0, 20), utc(2026, 10, 1, 3, 40), "rolling")
    assert [t.hour for t in target_grid(window, 60)] == [1, 2]
    assert len(half_hour_grid(window)) == 6  # 00:30 .. 03:00


def test_matching_uses_utc_timestamps_across_dst() -> None:
    # 25 Oct 2026 (clocks go back) has 25 hourly targets and 50 half-hours.
    window = custom_window(date(2026, 10, 25), date(2026, 10, 25), LONDON)
    assert len(target_grid(window, 60)) == 25
    assert len(half_hour_grid(window)) == 50
    # 01:00-02:00 local happens twice; UTC keeps them distinct.
    alignment = describe_alignment(half_hours(window.start, 25, 60), "hourly_mean")
    times = list(half_hour_grid(window))
    aligned = align_actuals(actual_frame(times, list(range(50))), target_grid(window, 60), alignment)
    assert aligned["actual_mw"].notna().all() and aligned["start_time"].is_unique


@pytest.mark.parametrize(
    ("day", "periods"), [(date(2026, 10, 25), 50), (date(2026, 3, 29), 46), (date(2026, 6, 1), 48)]
)
def test_settlement_day_lengths(day: date, periods: int) -> None:
    assert expected_settlement_periods(day, LONDON) == periods


def test_local_day_bounds_in_summer() -> None:
    assert local_day_bounds(date(2026, 7, 1), LONDON) == (
        datetime(2026, 6, 30, 23, tzinfo=timezone.utc),
        datetime(2026, 7, 1, 23, tzinfo=timezone.utc),
    )
