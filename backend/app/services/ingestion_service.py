"""Explicit, idempotent ingestion of FUELHH actuals and WINDFOR publications."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from app.clients.elexon_client import ElexonClient, ElexonError
from app.core.config import Settings
from app.core.timeutils import local_dates_covering, local_day_bounds, rolling_window, utc_now
from app.db.repositories import ActualRepository, ForecastRepository
from app.services.normalization_service import normalise_actuals, normalise_forecasts

logger = logging.getLogger(__name__)

Dataset = Literal["FUELHH", "WINDFOR"]
ALL_DATASETS: tuple[Dataset, ...] = ("FUELHH", "WINDFOR")


@dataclass
class DatasetReport:
    dataset: Dataset
    requested_from: str
    requested_to: str
    fetched: int = 0
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    invalid: int = 0
    requests: int = 0
    error: str | None = None


@dataclass
class IngestionReport:
    status: Literal["success", "partial", "failed"]
    datasets: list[DatasetReport]
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class IngestionService:
    def __init__(
        self,
        settings: Settings,
        client: ElexonClient,
        actuals: ActualRepository,
        forecasts: ForecastRepository,
    ) -> None:
        self._settings = settings
        self._client = client
        self._actuals = actuals
        self._forecasts = forecasts
        self._tz = ZoneInfo(settings.business_timezone)
        # One lock per event loop: an asyncio.Lock must not be shared across loops,
        # which can happen between invocations on serverless hosts.
        self._locks: dict[int, asyncio.Lock] = {}

    def _lock(self) -> asyncio.Lock:
        return self._locks.setdefault(id(asyncio.get_running_loop()), asyncio.Lock())

    def default_date_range(self) -> tuple[date, date]:
        """Local dates covered by the default rolling analysis window."""
        window = rolling_window(self._settings.default_analysis_window_days)
        return local_dates_covering(window.start, window.end, self._tz)

    async def ingest(
        self,
        start_date: date | None = None,
        end_date: date | None = None,
        datasets: tuple[Dataset, ...] = ALL_DATASETS,
    ) -> IngestionReport:
        if (start_date is None) != (end_date is None):
            raise ValueError("start_date and end_date must be supplied together")
        if start_date is None or end_date is None:
            start_date, end_date = self.default_date_range()
        if end_date < start_date:
            raise ValueError("end_date must be on or after start_date")

        today = utc_now().astimezone(self._tz).date()
        warnings: list[str] = []
        if end_date > today:
            warnings.append(f"end_date {end_date} is in the future; actuals requested up to {today} only")

        async with self._lock():
            reports: list[DatasetReport] = []
            if "FUELHH" in datasets:
                reports.append(await self._ingest_actuals(start_date, min(end_date, today), warnings))
            if "WINDFOR" in datasets:
                reports.append(await self._ingest_forecasts(start_date, end_date, warnings))

        errors = [f"{r.dataset}: {r.error}" for r in reports if r.error]
        if not errors:
            status: Literal["success", "partial", "failed"] = "success"
        elif len(errors) < len(reports):
            status = "partial"
        else:
            status = "failed"
        return IngestionReport(status=status, datasets=reports, errors=errors, warnings=warnings)

    async def _ingest_actuals(self, first: date, last: date, warnings: list[str]) -> DatasetReport:
        report = DatasetReport("FUELHH", first.isoformat(), last.isoformat())
        if last < first:
            report.error = "no settlement dates to request (range is entirely in the future)"
            return report
        try:
            result = await self._client.fetch_actual_wind(first, last)
        except ElexonError as exc:
            report.error = str(exc)
            return report
        report.requests, report.fetched, report.invalid = result.requests, len(result.rows), result.invalid
        if result.invalid:
            warnings.append(f"FUELHH: {result.invalid} invalid record(s) skipped, e.g. {result.invalid_examples[:2]}")
        if not result.rows:
            # An empty response is "no data yet", never "zero generation".
            warnings.append(f"FUELHH: Elexon returned no wind records for {first}..{last}")
        counts = self._actuals.upsert(normalise_actuals(result.rows))
        report.inserted, report.updated, report.skipped = counts.inserted, counts.updated, counts.skipped
        logger.info("FUELHH %s..%s: %s", first, last, counts)
        return report

    async def _ingest_forecasts(self, first: date, last: date, warnings: list[str]) -> DatasetReport:
        # The forecast for delivery day D is chosen at a cutoff on D-1, so fetch
        # publications from `lookback` days before D-1 of the first date.
        publish_from, _ = local_day_bounds(
            first - timedelta(days=1 + self._settings.forecast_publication_lookback_days), self._tz
        )
        _, last_end = local_day_bounds(last, self._tz)
        publish_to = min(last_end, utc_now())
        report = DatasetReport("WINDFOR", publish_from.isoformat(), publish_to.isoformat())
        try:
            result = await self._client.fetch_forecast_publications(publish_from, publish_to)
        except ElexonError as exc:
            report.error = str(exc)
            return report
        report.requests, report.fetched, report.invalid = result.requests, len(result.rows), result.invalid
        if result.invalid:
            warnings.append(f"WINDFOR: {result.invalid} invalid record(s) skipped, e.g. {result.invalid_examples[:2]}")
        if not result.rows:
            warnings.append(f"WINDFOR: Elexon returned no publications between {publish_from} and {publish_to}")
        counts = self._forecasts.insert_new(normalise_forecasts(result.rows))
        report.inserted, report.skipped = counts.inserted, counts.skipped
        logger.info("WINDFOR published %s..%s: %s", publish_from, publish_to, counts)
        return report
