from typing import Annotated, Literal

from fastapi import APIRouter, Query

from app.api.deps import AnalysisDep, ContainerDep
from app.schemas.accuracy import (
    AccuracySeriesOut,
    AccuracySummaryOut,
    AnalysisMeta,
    DistributionOut,
    HistogramBinOut,
    SeriesPoint,
)
from app.schemas.common import ErrorRangeOut, LevelErrorRangesOut, MetricsOut, PeriodMetricsOut
from app.services.accuracy_service import (
    bucket_metrics,
    compute_metrics,
    daily_metrics,
    error_histogram,
    error_range,
    level_error_ranges,
)

router = APIRouter(prefix="/accuracy", tags=["accuracy"])


@router.get("/summary", response_model=AccuracySummaryOut)
async def accuracy_summary(
    query: AnalysisDep,
    container: ContainerDep,
    granularity: Annotated[Literal["overall", "weekly", "daily"], Query()] = "weekly",
) -> AccuracySummaryOut:
    """MAE, RMSE, bias, WAPE, empirical error range and weekly/daily breakdown."""
    analysis = container.accuracy.analyse(query.window, query.required_cutoff())
    matched = analysis.matched
    settings = container.settings
    if granularity == "weekly":
        breakdown = bucket_metrics(matched, analysis.window)
    elif granularity == "daily":
        breakdown = daily_metrics(matched, analysis.window, container.accuracy.business_tz)
    else:
        breakdown = []
    return AccuracySummaryOut(
        **AnalysisMeta.of(analysis),
        granularity=granularity,
        metrics=MetricsOut.of(compute_metrics(matched)),
        error_range=ErrorRangeOut.of(error_range(matched, settings.min_samples_for_error_range)),
        error_range_by_level=LevelErrorRangesOut.of(
            level_error_ranges(matched, settings.error_range_level_bands, settings.min_samples_for_error_range)
        ),
        breakdown=[PeriodMetricsOut.of(p) for p in breakdown],
    )


@router.get("/series", response_model=AccuracySeriesOut)
async def accuracy_series(query: AnalysisDep, container: ContainerDep) -> AccuracySeriesOut:
    """Matched forecast-vs-actual observations (forecast vs actual, and error-over-time graphs)."""
    analysis = container.accuracy.analyse(query.window, query.required_cutoff())
    series = [
        SeriesPoint(
            timestamp=row.start_time.to_pydatetime(),
            forecast_mw=row.forecast_mw,
            actual_mw=row.actual_mw,
            error_mw=round(row.error_mw, 3),
            absolute_error_mw=round(row.absolute_error_mw, 3),
            forecast_publish_time=row.forecast_publish_time.to_pydatetime(),
        )
        for row in analysis.matched.itertuples(index=False)
    ]
    return AccuracySeriesOut(**AnalysisMeta.of(analysis), series=series)


@router.get("/distribution", response_model=DistributionOut)
async def accuracy_distribution(
    query: AnalysisDep,
    container: ContainerDep,
    bins: Annotated[int | None, Query(ge=2, le=200, description="Target number of histogram bins")] = None,
) -> DistributionOut:
    """Histogram of forecast errors (forecast - actual) over the same matched observations."""
    analysis = container.accuracy.analyse(query.window, query.required_cutoff())
    hist = error_histogram(analysis.matched, bins or container.settings.default_histogram_bins)
    return DistributionOut(
        **AnalysisMeta.of(analysis),
        bins=[HistogramBinOut(bin_start_mw=a, bin_end_mw=b, count=c) for a, b, c in hist.bins],
        bin_width_mw=hist.bin_width_mw,
        sample_count=hist.sample_count,
        overforecast_count=hist.overforecast_count,
        underforecast_count=hist.underforecast_count,
        exact_count=hist.exact_count,
    )
