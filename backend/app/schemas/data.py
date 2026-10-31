from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.common import SOURCE, AlignmentOut, WindowOut
from app.services.ingestion_service import IngestionReport
from app.services.status_service import DataStatus, latest_generation_period


class IngestRequest(BaseModel):
    start_date: date | None = Field(default=None, description="First local settlement/delivery date")
    end_date: date | None = Field(default=None, description="Last local settlement/delivery date (inclusive)")
    datasets: list[Literal["FUELHH", "WINDFOR"]] = Field(default=["FUELHH", "WINDFOR"], min_length=1)

    @model_validator(mode="after")
    def _check_dates(self) -> IngestRequest:
        if (self.start_date is None) != (self.end_date is None):
            raise ValueError("start_date and end_date must be supplied together")
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        if self.start_date and self.end_date and (self.end_date - self.start_date).days > 366:
            raise ValueError("date range may not exceed 366 days")
        return self


class DatasetIngestOut(BaseModel):
    dataset: str
    requested_from: str
    requested_to: str
    requests: int
    fetched: int
    inserted: int
    updated: int
    skipped: int
    invalid: int
    error: str | None


class IngestOut(BaseModel):
    source: str = SOURCE
    status: Literal["success", "partial", "failed"]
    records_fetched: int
    records_inserted: int
    records_updated: int
    records_skipped: int
    records_invalid: int
    datasets: list[DatasetIngestOut]
    errors: list[str]
    warnings: list[str]

    @classmethod
    def of(cls, r: IngestionReport) -> IngestOut:
        ds = [DatasetIngestOut(**vars(d)) for d in r.datasets]
        return cls(
            status=r.status,
            records_fetched=sum(d.fetched for d in ds),
            records_inserted=sum(d.inserted for d in ds),
            records_updated=sum(d.updated for d in ds),
            records_skipped=sum(d.skipped for d in ds),
            records_invalid=sum(d.invalid for d in ds),
            datasets=ds,
            errors=r.errors,
            warnings=r.warnings,
        )


class IncompleteDateOut(BaseModel):
    settlement_date: date
    expected_periods: int
    stored_periods: int


class ActualStatusOut(BaseModel):
    record_count: int
    first_start_time: datetime | None
    last_start_time: datetime | None
    first_settlement_date: date | None
    last_settlement_date: date | None
    latest_publish_time: datetime | None
    latest_generation_period_start: datetime | None
    latest_generation_period_end: datetime | None
    missing_periods: int = Field(description="Missing half-hours on past settlement dates within stored coverage")
    incomplete_dates: list[IncompleteDateOut] = Field(description="Up to 31 past dates with missing periods")


class ForecastStatusOut(BaseModel):
    record_count: int
    publication_count: int
    first_publish_time: datetime | None
    latest_publish_time: datetime | None
    first_target_time: datetime | None
    last_target_time: datetime | None


class DefaultWindowStatusOut(BaseModel):
    analysis_window: WindowOut
    actual_periods_available: int
    expected_actual_periods: int
    decision_cutoff_configured: bool
    ready: bool


class DataStatusOut(BaseModel):
    source: str = SOURCE
    actual: ActualStatusOut
    forecast: ForecastStatusOut
    alignment: AlignmentOut
    default_window: DefaultWindowStatusOut
    warnings: list[str]

    @classmethod
    def of(cls, s: DataStatus) -> DataStatusOut:
        period = latest_generation_period(s.actual["last_start_time"])
        return cls(
            actual=ActualStatusOut(
                record_count=s.actual["count"],
                first_start_time=s.actual["first_start_time"],
                last_start_time=s.actual["last_start_time"],
                first_settlement_date=s.actual["first_settlement_date"],
                last_settlement_date=s.actual["last_settlement_date"],
                latest_publish_time=s.actual["latest_publish_time"],
                latest_generation_period_start=period[0] if period else None,
                latest_generation_period_end=period[1] if period else None,
                missing_periods=s.missing_actual_periods,
                incomplete_dates=[IncompleteDateOut(**vars(d)) for d in s.incomplete_dates],
            ),
            forecast=ForecastStatusOut(
                record_count=s.forecast["count"],
                publication_count=s.forecast["publication_count"],
                first_publish_time=s.forecast["first_publish_time"],
                latest_publish_time=s.forecast["last_publish_time"],
                first_target_time=s.forecast["first_target_time"],
                last_target_time=s.forecast["last_target_time"],
            ),
            alignment=AlignmentOut.of(s.alignment),
            default_window=DefaultWindowStatusOut(
                analysis_window=WindowOut.of(s.default_window),
                actual_periods_available=s.default_window_actual_periods,
                expected_actual_periods=s.default_window_expected_periods,
                decision_cutoff_configured=s.decision_cutoff_configured,
                ready=s.ready_for_default_analysis,
            ),
            warnings=s.warnings,
        )
