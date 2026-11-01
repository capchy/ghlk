import math
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from app.core.timeutils import AnalysisWindow, custom_window, ensure_utc, rolling_window, seven_day_buckets
from app.services.accuracy_service import (
    AccuracyService,
    bucket_metrics,
    compute_metrics,
    daily_energy_ratio_range,
    daily_energy_errors,
    daily_metrics,
    error_histogram,
    error_range,
    level_error_ranges,
)
from app.services.normalization_service import ActualRecord, ForecastRecord
from tests.conftest import utc

LONDON = ZoneInfo("Europe/London")


def matched(forecast: list[float], actual: list[float], start: datetime | None = None) -> pd.DataFrame:
    start = start or utc(2026, 10, 1)
    df = pd.DataFrame(
        {
            "start_time": pd.to_datetime([start + timedelta(hours=i) for i in range(len(forecast))], utc=True),
            "forecast_mw": [float(f) for f in forecast],
            "actual_mw": [float(a) for a in actual],
        }
    )
    df["error_mw"] = df["forecast_mw"] - df["actual_mw"]
    df["absolute_error_mw"] = df["error_mw"].abs()
    return df


def test_hand_calculated_metrics() -> None:
    df = matched([100, 200, 300], [110, 180, 300])
    assert df["error_mw"].tolist() == [-10, 20, 0]
    assert df["absolute_error_mw"].tolist() == [10, 20, 0]
    m = compute_metrics(df)
    assert m.sample_count == 3
    assert m.mae_mw == 10
    assert m.bias_mw == pytest.approx(10 / 3, abs=1e-3)
    assert m.rmse_mw == pytest.approx(math.sqrt((100 + 400 + 0) / 3), abs=1e-3)
    assert m.wape_pct == pytest.approx(30 / 590 * 100, abs=1e-3)


def test_daily_energy_errors_hand_calculated() -> None:
    # Local midnight on 1 Oct 2026 (BST) is 30 Sep 23:00Z; two whole 24-hour days.
    df = matched([100] * 24 + [200] * 24, [90] * 24 + [250] * 24, start=utc(2026, 9, 30, 23))
    days = daily_energy_errors(df, LONDON, 60)
    assert [str(d) for d in days["delivery_date"]] == ["2026-10-01", "2026-10-02"]
    assert days["forecast_mwh"].tolist() == [2400, 4800]
    assert days["error_mwh"].tolist() == [240, -1200]
    # Half-hourly resolution halves the energy of each value.
    half_hourly = matched([100] * 48, [100] * 48)
    half_hourly["start_time"] = pd.date_range(utc(2026, 9, 30, 23), periods=48, freq="30min")
    assert daily_energy_errors(half_hourly, LONDON, 30)["forecast_mwh"].tolist() == [2400]


def test_daily_energy_drops_incomplete_days_and_counts_dst_day() -> None:
    df = matched([100] * 48, [100] * 48, start=utc(2026, 9, 30, 23)).drop(index=30)
    assert [str(d) for d in daily_energy_errors(df, LONDON, 60)["delivery_date"]] == ["2026-10-01"]
    # 25 Oct 2026 has 25 local hours (clocks go back); local midnight is 24 Oct 23:00Z.
    dst = daily_energy_errors(matched([10] * 25, [0] * 25, start=utc(2026, 10, 24, 23)), LONDON, 60)
    assert len(dst) == 1 and dst["forecast_mwh"].iloc[0] == 250
    assert daily_energy_errors(matched([10] * 24, [0] * 24, start=utc(2026, 10, 24, 23)), LONDON, 60).empty


def test_daily_energy_ratio_range_quantiles_and_minimum() -> None:
    # Ten whole days with forecast 100 MW and actual 50, 55, ... 95 MW: ratios 0.50 .. 0.95.
    forecast = [100.0] * 240
    actual = [50.0 + 5 * d for d in range(10) for _ in range(24)]
    df = matched(forecast, actual, start=utc(2026, 9, 30, 23))
    rng = daily_energy_ratio_range(df, LONDON, 60, min_days=10)
    assert rng.available and rng.day_count == 10
    assert rng.p10_ratio == pytest.approx(0.545) and rng.p90_ratio == pytest.approx(0.905)
    assert rng.p50_ratio == pytest.approx(0.725)
    # The range scales with the day's forecast: calm and windy days keep the same proportions.
    assert rng.actual_range_for(1000) == (pytest.approx(545), pytest.approx(905))
    assert rng.actual_range_for(100) == (pytest.approx(54.5), pytest.approx(90.5))
    assert "55%" in rng.explanation and "90%" in rng.explanation
    short = daily_energy_ratio_range(df, LONDON, 60, min_days=11)
    assert not short.available and short.p10_ratio is None and "10 complete" in short.explanation
    assert short.actual_range_for(1000) is None
    assert not daily_energy_ratio_range(matched([], []), LONDON, 60, min_days=1).available
    assert not daily_energy_ratio_range(df, LONDON, None, min_days=1).available


def test_daily_energy_ratio_skips_zero_forecast_days() -> None:
    df = matched([0.0] * 24 + [100.0] * 24, [10.0] * 24 + [80.0] * 24, start=utc(2026, 9, 30, 23))
    rng = daily_energy_ratio_range(df, LONDON, 60, min_days=1)
    assert rng.day_count == 1 and rng.p10_ratio == pytest.approx(0.8)


def test_zero_actuals_are_valid_and_wape_guarded() -> None:
    m = compute_metrics(matched([50, 0], [0, 0]))
    assert m.sample_count == 2 and m.mae_mw == 25 and m.bias_mw == 25
    assert m.wape_pct is None  # denominator is zero


def test_empty_and_single_observation() -> None:
    empty = compute_metrics(matched([], []))
    assert empty.sample_count == 0 and empty.mae_mw is None and empty.rmse_mw is None
    single = compute_metrics(matched([120], [100]))
    assert (single.mae_mw, single.rmse_mw, single.bias_mw) == (20, 20, 20)


def test_error_range_requires_minimum_samples() -> None:
    small = error_range(matched([1, 2], [0, 0]), min_samples=30)
    assert not small.available and small.p10_error_mw is None and "at least 30" in small.explanation
    assert small.actual_range_for(1000) is None


def test_error_range_quantiles_and_actual_range() -> None:
    # errors -50..50 in steps of 1
    df = matched([50] * 101, list(range(101)))
    r = error_range(df, min_samples=30)
    assert r.available and r.sample_count == 101
    assert (r.p10_error_mw, r.p50_error_mw, r.p90_error_mw) == (-40, 0, 40)
    # F - P90 .. F - P10
    assert r.actual_range_for(1000) == (960, 1040)
    assert r.actual_range_for(10) == (0, 50)  # floored at 0 MW


def test_histogram_bins_include_zero_edge_and_count_all() -> None:
    df = matched([0] * 7, [730, 420, 10, 0, -260, -505, -990])  # errors -730..990
    h = error_histogram(df, target_bins=8)
    edges = [b[0] for b in h.bins] + [h.bins[-1][1]]
    assert 0 in edges
    assert sum(c for _, _, c in h.bins) == h.sample_count == 7
    assert (h.overforecast_count, h.underforecast_count, h.exact_count) == (3, 3, 1)
    assert h.bin_width_mw in (200, 250, 500)
    assert error_histogram(matched([], []), 10).bins == []


def test_histogram_constant_errors() -> None:
    h = error_histogram(matched([5, 5], [0, 0]), 10)
    assert sum(c for _, _, c in h.bins) == 2


def test_rolling_window_is_exactly_28_days_and_rolls() -> None:
    end = utc(2026, 10, 31, 12)
    w = rolling_window(28, end)
    assert (w.start, w.end, w.days, w.mode) == (utc(2026, 10, 3, 12), end, 28, "rolling")
    assert rolling_window(28, end + timedelta(days=1)).start == utc(2026, 10, 4, 12)
    # Works across year boundaries without code changes.
    assert rolling_window(28, utc(2027, 1, 10)).start == utc(2026, 12, 13)
    now = rolling_window(28)
    assert abs((datetime.now(timezone.utc) - now.end).total_seconds()) < 5


def test_naive_datetimes_rejected() -> None:
    with pytest.raises(ValueError):
        ensure_utc(datetime(2026, 1, 1))


def test_custom_window_uses_london_dates() -> None:
    w = custom_window(date(2026, 7, 1), date(2026, 7, 7), LONDON)
    assert (w.start, w.end) == (utc(2026, 6, 30, 23), utc(2026, 7, 7, 23))
    with pytest.raises(ValueError):
        custom_window(date(2026, 7, 2), date(2026, 7, 1), LONDON)


def test_four_consecutive_seven_day_buckets() -> None:
    w = rolling_window(28, utc(2026, 10, 31, 12))
    buckets = seven_day_buckets(w)
    assert len(buckets) == 4
    assert buckets[0][0] == w.start and buckets[-1][1] == w.end
    assert all(e - s == timedelta(days=7) for s, e in buckets)
    assert all(buckets[i][1] == buckets[i + 1][0] for i in range(3))


def test_bucket_metrics_assign_each_observation_once() -> None:
    w = AnalysisWindow(utc(2026, 10, 1), utc(2026, 10, 29), "rolling")
    df = matched([10] * (28 * 24), [0] * (28 * 24), start=w.start)
    out = bucket_metrics(df, w)
    assert [p.metrics.sample_count for p in out] == [168, 168, 168, 168]
    assert all(p.metrics.mae_mw == 10 for p in out)


def test_daily_metrics_handle_dst_day() -> None:
    w = custom_window(date(2026, 10, 24), date(2026, 10, 26), LONDON)
    df = matched([1] * 73, [0] * 73, start=w.start)  # 24 + 25 + 24 hours
    out = daily_metrics(df, w, LONDON)
    assert [p.metrics.sample_count for p in out] == [24, 25, 24]
    assert out[1].period_end - out[1].period_start == timedelta(hours=25)


# --- End-to-end analysis over stored rows -------------------------------------------


def _store(repos, actual_hours: int, start: datetime, skip: set[datetime] = frozenset()) -> None:
    actuals, forecasts = repos
    actuals.upsert(
        [
            ActualRecord("FUELHH", t + timedelta(minutes=30), t, t.date(), 1, "WIND", 1000.0)
            for t in (start + timedelta(minutes=30 * i) for i in range(actual_hours * 2))
            if t not in skip
        ]
    )
    # Two publications: one before every cutoff, one after (must never be used).
    early, late = start - timedelta(days=2), start + timedelta(days=30)
    forecasts.insert_new(
        [ForecastRecord("WINDFOR", early, start + timedelta(hours=h), None, None, 1100.0) for h in range(48)]
        + [ForecastRecord("WINDFOR", late, start + timedelta(hours=h), None, None, 1.0) for h in range(48)]
    )


def test_analysis_matches_and_reports_unmatched(settings, repos) -> None:
    start = utc(2026, 9, 30, 23)  # local midnight 1 Oct (BST)
    _store(repos, actual_hours=40, start=start, skip={start + timedelta(hours=5, minutes=30)})
    service = AccuracyService(settings, *repos)
    window = AnalysisWindow(start, start + timedelta(days=2), "custom")
    result = service.analyse(window, time(9, 0))
    c = result.coverage
    assert c.target_periods == 48 and c.targets_with_eligible_forecast == 48
    assert c.records_matched == 39  # 40 hours of actuals, one with a missing half-hour
    assert c.unmatched_by_reason == {"no_eligible_forecast": 0, "incomplete_actuals": 1, "awaiting_actuals": 8}
    assert c.missing_actual_periods == 96 - 79
    assert not c.actual_data_complete
    assert set(result.matched["forecast_mw"]) == {1100.0}  # the post-cutoff publication is never used
    assert set(result.matched["error_mw"]) == {100.0}
    assert result.alignment.status == "inferred"
    assert any("no actuals yet" in w for w in result.warnings)


def test_analysis_on_empty_database(settings, repos) -> None:
    service = AccuracyService(settings, *repos)
    result = service.analyse(rolling_window(28), time(9, 0))
    assert result.matched.empty and result.coverage.records_matched == 0
    assert result.alignment.status == "no_data"
    assert any("data/ingest" in w for w in result.warnings)


def test_level_error_ranges_condition_on_forecast_level() -> None:
    # Low forecasts are accurate; high forecasts overshoot by 1000 MW.
    df = pd.concat([matched([1000] * 50, [1000] * 50), matched([10000] * 50, [9000] * 50)], ignore_index=True)
    levels = level_error_ranges(df, target_bins=2, min_samples=10)
    assert levels.available and len(levels.bands) == 2
    assert levels.actual_range_for(1000) == (1000, 1000)
    assert levels.actual_range_for(10000) == (9000, 9000)
    # The overall range mixes both regimes and is wider for each.
    overall = error_range(df, 10).actual_range_for(10000)
    assert overall[1] - overall[0] > 0


def test_level_bands_are_open_ended_and_consistent_with_binning() -> None:
    df = matched(list(range(0, 3000, 10)), [0] * 300)
    levels = level_error_ranges(df, target_bins=5, min_samples=30)
    assert len(levels.bands) == 5
    assert levels.bands[0].forecast_min_mw is None and levels.bands[-1].forecast_max_mw is None
    assert levels.band_for(-1e9) is levels.bands[0] and levels.band_for(1e9) is levels.bands[-1]
    assert sum(b.error_range.sample_count for b in levels.bands) == 300
    assert all(b.error_range.sample_count >= 30 for b in levels.bands)
    # Looking each observation up by value reproduces the band counts exactly.
    by_lookup = [sum(levels.band_for(f) is b for f in df["forecast_mw"]) for b in levels.bands]
    assert by_lookup == [b.error_range.sample_count for b in levels.bands]


def test_level_bands_shrink_or_disable_when_sparse() -> None:
    assert len(level_error_ranges(matched([1] * 90, [0] * 90), 5, 30).bands) <= 3
    sparse = level_error_ranges(matched([1] * 50, [0] * 50), 5, 30)
    assert not sparse.available and sparse.bands == [] and "at least 60" in sparse.explanation
    assert sparse.actual_range_for(1) is None
