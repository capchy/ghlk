"""Upcoming forecast display and forecast evolution.

Separate from historical accuracy: the upcoming forecast is the newest
available publication for each period, independent of the analysis window.

Source choice:
* no ``as_of`` -> Elexon's ``/forecast/generation/wind/latest/stream`` (always
  current, one small request). If Elexon is unavailable, stored publications
  are used and a warning is returned.
* ``as_of`` given -> stored WINDFOR publications, because the latest endpoint
  cannot answer "what was the newest forecast at time X".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

import pandas as pd

from app.clients.elexon_client import ElexonClient, ElexonError
from app.core.config import Settings
from app.core.timeutils import local_day_bounds, utc_now
from app.db.repositories import ForecastRepository
from app.services.forecast_selection_service import select_latest
from app.services.normalization_service import normalise_forecasts
from app.services.temporal_alignment_service import detect_resolution

logger = logging.getLogger(__name__)

ForecastSource = Literal["elexon_latest_endpoint", "stored_publications"]


@dataclass
class UpcomingForecast:
    delivery_date: date
    day_start: datetime
    day_end: datetime
    as_of: datetime | None
    source: ForecastSource
    items: pd.DataFrame  # start_time, publish_time, settlement_date, settlement_period, forecast_mw
    resolution_minutes: int | None
    expected_periods: int | None
    warnings: list[str] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return not self.items.empty

    @property
    def complete(self) -> bool:
        return self.expected_periods is not None and len(self.items) >= self.expected_periods

    @property
    def publish_times(self) -> list[datetime]:
        return sorted(t.to_pydatetime() for t in self.items["publish_time"].unique()) if self.available else []


class ForecastService:
    def __init__(self, settings: Settings, client: ElexonClient, forecasts: ForecastRepository) -> None:
        self._client = client
        self._forecasts = forecasts
        self._tz = ZoneInfo(settings.business_timezone)

    def default_delivery_date(self) -> date:
        return utc_now().astimezone(self._tz).date() + timedelta(days=1)

    async def upcoming(self, delivery_date: date, as_of: datetime | None = None) -> UpcomingForecast:
        day_start, day_end = local_day_bounds(delivery_date, self._tz)
        warnings: list[str] = []
        source: ForecastSource
        if as_of is None:
            try:
                result = await self._client.fetch_latest_forecast(day_start, day_end)
                items = select_latest(_frame(normalise_forecasts(result.rows)))
                source = "elexon_latest_endpoint"
                if result.invalid:
                    warnings.append(f"{result.invalid} invalid forecast record(s) from Elexon were skipped.")
            except ElexonError as exc:
                logger.warning("Latest forecast unavailable, falling back to stored publications: %s", exc)
                items = select_latest(self._forecasts.load_publications(day_start, day_end))
                if items.empty:
                    raise
                source = "stored_publications"
                warnings.append(f"Elexon is unavailable ({exc}); showing the latest stored publication instead.")
        else:
            items = select_latest(self._forecasts.load_publications(day_start, day_end), as_of)
            source = "stored_publications"

        resolution, res_warnings = detect_resolution(items["start_time"]) if len(items) >= 2 else (None, [])
        warnings.extend(res_warnings)
        expected = int((day_end - day_start) / timedelta(minutes=resolution)) if resolution else None
        if items.empty:
            when = f" as of {as_of.isoformat()}" if as_of else ""
            warnings.append(f"No forecast{when} was found for delivery date {delivery_date.isoformat()}.")
        elif expected is not None and len(items) < expected:
            warnings.append(f"Forecast covers {len(items)} of {expected} periods of {delivery_date.isoformat()}.")
        if not items.empty and items["publish_time"].nunique() > 1:
            warnings.append("Periods on this date come from different publications; see each item's publish_time.")
        return UpcomingForecast(delivery_date, day_start, day_end, as_of, source, items, resolution, expected, warnings)

    def evolution(self, target_start_time: datetime) -> list[tuple[datetime, float]]:
        return self._forecasts.evolution(target_start_time)


def _frame(records: list) -> pd.DataFrame:
    df = pd.DataFrame(
        [(r.publish_time, r.start_time, r.settlement_date, r.settlement_period, r.generation_mw) for r in records],
        columns=["publish_time", "start_time", "settlement_date", "settlement_period", "forecast_mw"],
    )
    df["publish_time"] = pd.to_datetime(df["publish_time"], utc=True)
    df["start_time"] = pd.to_datetime(df["start_time"], utc=True)
    return df
