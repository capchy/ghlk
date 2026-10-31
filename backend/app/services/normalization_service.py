"""Converts validated Elexon rows into storage records (UTC, consistent units)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timezone

from app.clients.elexon_client import FuelHHRow, WindForRow


@dataclass(frozen=True)
class ActualRecord:
    dataset: str
    publish_time: datetime
    start_time: datetime
    settlement_date: date
    settlement_period: int
    fuel_type: str
    generation_mw: float


@dataclass(frozen=True)
class ForecastRecord:
    dataset: str
    publish_time: datetime
    start_time: datetime
    settlement_date: date | None
    settlement_period: int | None
    generation_mw: float


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc)


def normalise_actuals(rows: Iterable[FuelHHRow]) -> list[ActualRecord]:
    return [
        ActualRecord(
            dataset=r.dataset,
            publish_time=_utc(r.publishTime),
            start_time=_utc(r.startTime),
            settlement_date=r.settlementDate,
            settlement_period=r.settlementPeriod,
            fuel_type=r.fuelType,
            generation_mw=float(r.generation),
        )
        for r in rows
    ]


def normalise_forecasts(rows: Iterable[WindForRow]) -> list[ForecastRecord]:
    return [
        ForecastRecord(
            dataset=r.dataset,
            publish_time=_utc(r.publishTime),
            start_time=_utc(r.startTime),
            settlement_date=r.settlementDate,
            settlement_period=r.settlementPeriod,
            generation_mw=float(r.generation),
        )
        for r in rows
    ]
