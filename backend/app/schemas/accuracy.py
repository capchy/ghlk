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
from app.services.accuracy_service import AnalysisResult


class AnalysisMeta(BaseModel):
    source: str = SOURCE
    analysis_window: WindowOut
    decision_cutoff: DecisionCutoffOut
    alignment: AlignmentOut
    coverage: CoverageOut
    warnings: list[str]

    @classmethod
    def of(cls, a: AnalysisResult) -> dict:
        """Fields to splat into a response model that extends AnalysisMeta."""
        return {
            "analysis_window": WindowOut.of(a.window),
            "decision_cutoff": DecisionCutoffOut.of(a.cutoff_time, a.cutoff_timezone),
            "alignment": AlignmentOut.of(a.alignment),
            "coverage": CoverageOut.of(a.coverage),
            "warnings": a.warnings,
        }


class AccuracySummaryOut(AnalysisMeta):
    granularity: Literal["overall", "weekly", "daily"]
    metrics: MetricsOut
    error_range: ErrorRangeOut
    error_range_by_level: LevelErrorRangesOut
    breakdown: list[PeriodMetricsOut] = Field(
        description="Weekly: consecutive 7-day buckets from the window start. Daily: local delivery dates. "
        "Empty for granularity=overall."
    )


class SeriesPoint(BaseModel):
    timestamp: datetime = Field(description="Target period start (UTC)")
    forecast_mw: float
    actual_mw: float
    error_mw: float = Field(description="forecast - actual")
    absolute_error_mw: float
    forecast_publish_time: datetime


class AccuracySeriesOut(AnalysisMeta):
    series: list[SeriesPoint]


class HistogramBinOut(BaseModel):
    bin_start_mw: float
    bin_end_mw: float
    count: int


class DistributionOut(AnalysisMeta):
    bins: list[HistogramBinOut]
    bin_width_mw: float | None
    sample_count: int
    zero_reference_mw: float = Field(
        default=0.0, description="Errors above 0 are overforecasts, below 0 underforecasts"
    )
    overforecast_count: int
    underforecast_count: int
    exact_count: int
