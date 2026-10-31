"""Data access for actuals and forecasts."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session, sessionmaker

from app.core.timeutils import utc_now
from app.db.models import ActualGeneration, ForecastGeneration
from app.services.normalization_service import ActualRecord, ForecastRecord


@dataclass
class WriteCounts:
    inserted: int = 0
    updated: int = 0
    skipped: int = 0


def _insert(session_factory: sessionmaker[Session], model: type):
    """Dialect-specific INSERT supporting ON CONFLICT (SQLite locally, Postgres when hosted)."""
    dialect = session_factory.kw["bind"].dialect.name
    return (postgresql.insert if dialect == "postgresql" else sqlite.insert)(model)


def _frame(rows: Sequence, columns: list[str], time_columns: list[str]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=columns)
    for col in time_columns:
        df[col] = pd.to_datetime(df[col], utc=True)
    return df


class ActualRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sf = session_factory

    def upsert(self, records: Sequence[ActualRecord]) -> WriteCounts:
        """Insert new periods; replace a stored period only with a strictly newer publication.

        Revision rule: for one (start_time, fuel_type) the row with the latest
        ``publish_time`` wins. Re-ingesting the same publication is a no-op.
        """
        counts = WriteCounts()
        if not records:
            return counts
        latest: dict[tuple[datetime, str], ActualRecord] = {}
        for r in records:
            key = (r.start_time, r.fuel_type)
            if key not in latest or r.publish_time > latest[key].publish_time:
                latest[key] = r
        counts.skipped += len(records) - len(latest)

        starts = [k[0] for k in latest]
        with self._sf() as session:
            existing = {
                (st, ft): pt
                for st, ft, pt in session.execute(
                    select(
                        ActualGeneration.start_time, ActualGeneration.fuel_type, ActualGeneration.publish_time
                    ).where(ActualGeneration.start_time.between(min(starts), max(starts)))
                )
            }

        now = utc_now()
        to_write = []
        for key, r in latest.items():
            stored = existing.get(key)
            if stored is None:
                counts.inserted += 1
            elif r.publish_time > stored:
                counts.updated += 1
            else:
                counts.skipped += 1
                continue
            to_write.append({**asdict(r), "created_at": now, "updated_at": now})

        if not to_write:
            return counts
        stmt = _insert(self._sf, ActualGeneration)
        stmt = stmt.on_conflict_do_update(
            index_elements=["start_time", "fuel_type"],
            set_={
                c: stmt.excluded[c]
                for c in (
                    "dataset",
                    "publish_time",
                    "settlement_date",
                    "settlement_period",
                    "generation_mw",
                    "updated_at",
                )
            },
            # Guards against a concurrent writer storing a newer revision first.
            where=stmt.excluded.publish_time > ActualGeneration.publish_time,
        )
        with self._sf.begin() as session:
            # Core executemany: far faster than a compiled multi-row VALUES statement.
            session.connection().execute(stmt, to_write)
        return counts

    def load(self, start: datetime, end: datetime, fuel_type: str = "WIND") -> pd.DataFrame:
        """Actuals with ``start <= start_time < end``."""
        with self._sf() as session:
            rows = session.execute(
                select(
                    ActualGeneration.start_time,
                    ActualGeneration.publish_time,
                    ActualGeneration.settlement_date,
                    ActualGeneration.settlement_period,
                    ActualGeneration.generation_mw,
                )
                .where(
                    ActualGeneration.fuel_type == fuel_type,
                    ActualGeneration.start_time >= start,
                    ActualGeneration.start_time < end,
                )
                .order_by(ActualGeneration.start_time)
            ).all()
        return _frame(
            rows,
            ["start_time", "publish_time", "settlement_date", "settlement_period", "actual_mw"],
            ["start_time", "publish_time"],
        )

    def stats(self, fuel_type: str = "WIND") -> dict:
        with self._sf() as session:
            count, first, last, latest_pub, first_date, last_date = session.execute(
                select(
                    func.count(),
                    func.min(ActualGeneration.start_time),
                    func.max(ActualGeneration.start_time),
                    func.max(ActualGeneration.publish_time),
                    func.min(ActualGeneration.settlement_date),
                    func.max(ActualGeneration.settlement_date),
                ).where(ActualGeneration.fuel_type == fuel_type)
            ).one()
        # Aggregates bypass the column type, so normalise to aware UTC here.
        return {
            "count": count,
            "first_start_time": _aware(first),
            "last_start_time": _aware(last),
            "latest_publish_time": _aware(latest_pub),
            "first_settlement_date": _date(first_date),
            "last_settlement_date": _date(last_date),
        }

    def counts_by_settlement_date(self, first: date, last: date, fuel_type: str = "WIND") -> dict[date, int]:
        with self._sf() as session:
            result = session.execute(
                select(ActualGeneration.settlement_date, func.count())
                .where(ActualGeneration.fuel_type == fuel_type, ActualGeneration.settlement_date.between(first, last))
                .group_by(ActualGeneration.settlement_date)
            )
            return {_date(d): n for d, n in result}


class ForecastRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sf = session_factory

    def insert_new(self, records: Sequence[ForecastRecord]) -> WriteCounts:
        """Insert publications not yet stored. Existing rows are never modified."""
        counts = WriteCounts()
        if not records:
            return counts
        unique: dict[tuple[datetime, datetime], ForecastRecord] = {}
        for r in records:
            unique.setdefault((r.publish_time, r.start_time), r)
        counts.skipped += len(records) - len(unique)

        pubs = [k[0] for k in unique]
        with self._sf() as session:
            existing = set(
                session.execute(
                    select(ForecastGeneration.publish_time, ForecastGeneration.start_time).where(
                        ForecastGeneration.publish_time.between(min(pubs), max(pubs))
                    )
                ).tuples()
            )
        now = utc_now()
        new = [{**asdict(r), "created_at": now} for k, r in unique.items() if k not in existing]
        counts.inserted = len(new)
        counts.skipped += len(unique) - len(new)
        if new:
            with self._sf.begin() as session:
                session.connection().execute(_insert(self._sf, ForecastGeneration).on_conflict_do_nothing(), new)
        return counts

    def load_publications(
        self, start_from: datetime, start_to: datetime, published_at_or_before: datetime | None = None
    ) -> pd.DataFrame:
        """All stored publications for targets in ``[start_from, start_to)``."""
        conditions = [ForecastGeneration.start_time >= start_from, ForecastGeneration.start_time < start_to]
        if published_at_or_before is not None:
            conditions.append(ForecastGeneration.publish_time <= published_at_or_before)
        with self._sf() as session:
            rows = session.execute(
                select(
                    ForecastGeneration.publish_time,
                    ForecastGeneration.start_time,
                    ForecastGeneration.settlement_date,
                    ForecastGeneration.settlement_period,
                    ForecastGeneration.generation_mw,
                ).where(*conditions)
            ).all()
        return _frame(
            rows,
            ["publish_time", "start_time", "settlement_date", "settlement_period", "forecast_mw"],
            ["publish_time", "start_time"],
        )

    def evolution(self, target_start_time: datetime) -> list[tuple[datetime, float]]:
        with self._sf() as session:
            return list(
                session.execute(
                    select(ForecastGeneration.publish_time, ForecastGeneration.generation_mw)
                    .where(ForecastGeneration.start_time == target_start_time)
                    .order_by(ForecastGeneration.publish_time)
                ).tuples()
            )

    def recent_target_times(self, limit: int = 500) -> list[datetime]:
        """Most recent distinct target start times (for resolution checks)."""
        with self._sf() as session:
            rows = session.execute(
                select(ForecastGeneration.start_time)
                .distinct()
                .order_by(ForecastGeneration.start_time.desc())
                .limit(limit)
            ).scalars()
            return sorted(rows)

    def stats(self) -> dict:
        with self._sf() as session:
            count, first_pub, last_pub, n_pubs, first_target, last_target = session.execute(
                select(
                    func.count(),
                    func.min(ForecastGeneration.publish_time),
                    func.max(ForecastGeneration.publish_time),
                    func.count(func.distinct(ForecastGeneration.publish_time)),
                    func.min(ForecastGeneration.start_time),
                    func.max(ForecastGeneration.start_time),
                )
            ).one()
        return {
            "count": count,
            "first_publish_time": _aware(first_pub),
            "last_publish_time": _aware(last_pub),
            "publication_count": n_pubs,
            "first_target_time": _aware(first_target),
            "last_target_time": _aware(last_target),
        }


def _aware(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    ts = pd.Timestamp(value)
    return (ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")).to_pydatetime()


def _date(value: date | str | None) -> date | None:
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(value)
