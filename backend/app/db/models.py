"""ORM models."""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, Float, Index, Integer, String, TypeDecorator, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class UTCDateTime(TypeDecorator[datetime]):
    """Stored as naive UTC (SQLite has no timezone type); always read back as aware UTC."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime passed to a UTCDateTime column")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: object) -> datetime | None:
        return value.replace(tzinfo=timezone.utc) if value is not None else None


class Base(DeclarativeBase):
    pass


class ActualGeneration(Base):
    """FUELHH outturn. One row per (start_time, fuel_type); a newer publication
    of the same period replaces an older one (see repositories)."""

    __tablename__ = "actual_generation"
    __table_args__ = (
        UniqueConstraint("start_time", "fuel_type", name="uq_actual_period_fuel"),
        Index("ix_actual_start_time", "start_time"),
        Index("ix_actual_settlement_date", "settlement_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dataset: Mapped[str] = mapped_column(String(16))
    publish_time: Mapped[datetime] = mapped_column(UTCDateTime)
    start_time: Mapped[datetime] = mapped_column(UTCDateTime)
    settlement_date: Mapped[date] = mapped_column(Date)
    settlement_period: Mapped[int] = mapped_column(Integer)
    fuel_type: Mapped[str] = mapped_column(String(16))
    generation_mw: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class ForecastGeneration(Base):
    """WINDFOR forecasts. Every publication is kept; rows are never overwritten."""

    __tablename__ = "forecast_generation"
    __table_args__ = (
        UniqueConstraint("publish_time", "start_time", name="uq_forecast_publication_period"),
        Index("ix_forecast_start_time", "start_time"),
        Index("ix_forecast_publish_time", "publish_time"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dataset: Mapped[str] = mapped_column(String(16))
    publish_time: Mapped[datetime] = mapped_column(UTCDateTime)
    start_time: Mapped[datetime] = mapped_column(UTCDateTime)
    settlement_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    settlement_period: Mapped[int | None] = mapped_column(Integer, nullable=True)
    generation_mw: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
