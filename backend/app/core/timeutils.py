"""Time-window and timezone helpers.

All instants are timezone-aware and handled in UTC internally. The business
timezone (Europe/London) is used only to interpret dates: settlement dates,
delivery dates, custom date ranges and the decision cutoff.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime) -> datetime:
    """Convert to UTC; naive datetimes are rejected rather than guessed."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must include a timezone offset, e.g. 2026-10-31T00:00:00Z")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class AnalysisWindow:
    """Half-open UTC interval ``[start, end)``."""

    start: datetime
    end: datetime
    mode: Literal["rolling", "custom"]

    @property
    def days(self) -> float:
        return (self.end - self.start).total_seconds() / 86400


def rolling_window(days: int, end: datetime | None = None) -> AnalysisWindow:
    """Exactly ``days`` elapsed days ending at ``end`` (default: now)."""
    end_utc = ensure_utc(end) if end is not None else utc_now()
    return AnalysisWindow(start=end_utc - timedelta(days=days), end=end_utc, mode="rolling")


def custom_window(start_date: date, end_date: date, tz: ZoneInfo) -> AnalysisWindow:
    """Local dates ``start_date``..``end_date`` inclusive, as a UTC interval.

    Starts at local midnight of ``start_date`` and ends at local midnight after
    ``end_date``.
    """
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")
    start, _ = local_day_bounds(start_date, tz)
    _, end = local_day_bounds(end_date, tz)
    return AnalysisWindow(start=start, end=end, mode="custom")


def local_day_bounds(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """UTC bounds of a local calendar day (23 or 25 hours long on DST days)."""
    start = datetime.combine(day, time.min, tzinfo=tz).astimezone(timezone.utc)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz).astimezone(timezone.utc)
    return start, end


def expected_settlement_periods(day: date, tz: ZoneInfo) -> int:
    start, end = local_day_bounds(day, tz)
    return int((end - start) / timedelta(minutes=30))


def local_dates_covering(start: datetime, end: datetime, tz: ZoneInfo) -> tuple[date, date]:
    """First and last local dates intersecting ``[start, end)``."""
    first = ensure_utc(start).astimezone(tz).date()
    last = (ensure_utc(end) - timedelta(microseconds=1)).astimezone(tz).date()
    return first, last


def decision_cutoff(delivery_date: date, cutoff_time: time, cutoff_tz: ZoneInfo) -> datetime:
    """Latest usable publish time (UTC) for forecasts of ``delivery_date``:
    ``cutoff_time`` wall-clock in ``cutoff_tz`` on the previous day."""
    local = datetime.combine(delivery_date - timedelta(days=1), cutoff_time, tzinfo=cutoff_tz)
    return local.astimezone(timezone.utc)


def seven_day_buckets(window: AnalysisWindow) -> list[tuple[datetime, datetime]]:
    """Consecutive 7-day buckets from the window start.

    A 28-day window yields exactly four. A window that is not a multiple of
    seven days ends with a shorter final bucket, clipped to the window end.
    """
    buckets = []
    start = window.start
    while start < window.end:
        end = min(start + timedelta(days=7), window.end)
        buckets.append((start, end))
        start = end
    return buckets
