"""Engine and session factory (SQLite locally, Postgres in hosted deployments)."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Base

logger = logging.getLogger(__name__)

SERVERLESS_FALLBACK_URL = "sqlite:////tmp/app.db"


def normalise_database_url(database_url: str) -> str:
    """Use the psycopg 3 driver for Postgres URLs as hosts provide them (postgres://, postgresql://)."""
    for prefix in ("postgres://", "postgresql://"):
        if database_url.startswith(prefix):
            return "postgresql+psycopg://" + database_url.removeprefix(prefix)
    return database_url


def resolve_database_url(database_url: str) -> str:
    """On Vercel only /tmp is writable. Without a configured database, fall back to a
    throwaway SQLite file there so the app still starts (data does not persist)."""
    if os.environ.get("VERCEL") and database_url.startswith("sqlite:///./"):
        logger.warning("DATABASE_URL is not set on Vercel; using %s (not persistent)", SERVERLESS_FALLBACK_URL)
        return SERVERLESS_FALLBACK_URL
    return normalise_database_url(database_url)


def make_engine(database_url: str) -> Engine:
    url = resolve_database_url(database_url)
    if url.startswith("sqlite"):
        if url.startswith("sqlite:///") and ":memory:" not in url:
            Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        return create_engine(url, connect_args={"check_same_thread": False})
    # Serverless instances are frozen between requests, so pooled connections can
    # go stale: check them before use and recycle them regularly.
    return create_engine(url, pool_pre_ping=True, pool_recycle=300, pool_size=2, max_overflow=3)


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
