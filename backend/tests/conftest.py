"""Shared fixtures: a deterministic fake Elexon served through httpx.MockTransport."""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Callable, Iterator
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient

from app.clients.elexon_client import ElexonClient
from app.core.config import Settings
from app.db.database import init_db, make_engine, make_session_factory
from app.db.models import Base
from app.db.repositories import ActualRepository, ForecastRepository
from app.main import create_app

LONDON = ZoneInfo("Europe/London")
BASE_URL = "https://data.elexon.co.uk/bmrs/api/v1"
# Synthetic schedule; the backend must work it out from the data.
PUBLISH_HOURS_UTC = (2, 5, 8, 11, 14, 17, 20, 23)


def z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def actual_mw(start: datetime) -> int:
    """Deterministic half-hourly 'true' outturn."""
    return 5000 + start.hour * 100 + (30 if start.minute else 0)


def hourly_actual_mw(start: datetime) -> float:
    return (actual_mw(start) + actual_mw(start + timedelta(minutes=30))) / 2


def forecast_mw(start: datetime, publish: datetime) -> float:
    """Overforecasts by 10 MW per hour of lead time, so vintages are distinguishable."""
    lead_hours = int((start - publish).total_seconds() // 3600)
    return hourly_actual_mw(start) + 10 * lead_hours


class FakeElexon:
    """Generates Elexon-shaped data for any requested range; records calls."""

    def __init__(self) -> None:
        self.calls: Counter[str] = Counter()
        self.requests: list[httpx.Request] = []
        # Optional override: return this response (or call it) instead of data.
        self.respond: Callable[[httpx.Request], httpx.Response] | None = None
        self.missing_actuals: set[datetime] = set()
        self.envelope = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.removeprefix("/bmrs/api/v1")
        self.calls[path] += 1
        if self.respond is not None:
            return self.respond(request)
        params = request.url.params
        if path == "/datasets/FUELHH/stream":
            data = self._fuelhh(params)
        elif path == "/datasets/WINDFOR/stream":
            data = self._windfor(params)
        elif path == "/forecast/generation/wind/latest/stream":
            data = self._latest(params)
        else:
            return httpx.Response(404, text="not found")
        return httpx.Response(200, json={"data": data} if self.envelope else data)

    @staticmethod
    def _parse(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    def _fuelhh(self, params: httpx.QueryParams) -> list[dict]:
        assert "publishDateTimeFrom" not in params and "publishDateTimeTo" not in params
        assert params["fuelType"] == "WIND"
        day, last = date.fromisoformat(params["settlementDateFrom"]), date.fromisoformat(params["settlementDateTo"])
        rows = []
        while day <= last:
            t = datetime.combine(day, time.min, tzinfo=LONDON).astimezone(timezone.utc)
            end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=LONDON).astimezone(timezone.utc)
            period = 1
            while t < end:
                if t not in self.missing_actuals:
                    rows.append(
                        {
                            "dataset": "FUELHH",
                            "publishTime": z(t + timedelta(minutes=30)),
                            "startTime": z(t),
                            "settlementDate": day.isoformat(),
                            "settlementPeriod": period,
                            "fuelType": "WIND",
                            "generation": actual_mw(t),
                        }
                    )
                t += timedelta(minutes=30)
                period += 1
            day += timedelta(days=1)
        return rows

    @staticmethod
    def publications(start: datetime, end: datetime) -> list[datetime]:
        """Publish times in ``[start, end]`` (inclusive, like Elexon)."""
        t = start.replace(minute=30, second=0, microsecond=0) - timedelta(hours=1)
        out = []
        while t <= end:
            if t >= start and t.hour in PUBLISH_HOURS_UTC:
                out.append(t)
            t += timedelta(hours=1)
        return out

    @staticmethod
    def horizon(publish: datetime) -> list[datetime]:
        first = publish.replace(minute=0) - timedelta(hours=23)
        return [first + timedelta(hours=h) for h in range(72)]

    def _windfor(self, params: httpx.QueryParams) -> list[dict]:
        pubs = self.publications(self._parse(params["publishDateTimeFrom"]), self._parse(params["publishDateTimeTo"]))
        return [
            {"dataset": "WINDFOR", "publishTime": z(p), "startTime": z(s), "generation": forecast_mw(s, p)}
            for p in pubs
            for s in self.horizon(p)
        ]

    def _latest(self, params: httpx.QueryParams) -> list[dict]:
        start, end = self._parse(params["from"]), self._parse(params["to"])
        publish = self.publications(start - timedelta(days=1), start - timedelta(hours=12))[-1]
        return [
            {
                "publishTime": z(publish),
                "startTime": z(s),
                "settlementDate": s.astimezone(LONDON).date().isoformat(),
                "settlementPeriod": s.astimezone(LONDON).hour * 2 + 1,
                "generation": forecast_mw(s, publish),
            }
            for s in self.horizon(publish)
            if start <= s <= end
        ]


class SleepRecorder:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


@pytest.fixture
def fake_elexon() -> FakeElexon:
    return FakeElexon()


@pytest.fixture
def sleeps() -> SleepRecorder:
    return SleepRecorder()


@pytest.fixture
def elexon_client(fake_elexon: FakeElexon, sleeps: SleepRecorder) -> ElexonClient:
    return ElexonClient(BASE_URL, max_retries=2, transport=httpx.MockTransport(fake_elexon.handler), sleep=sleeps)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    # TEST_DATABASE_URL runs the suite against a real database (e.g. Postgres,
    # as used on Vercel); tables are dropped first so every test starts empty.
    url = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'test.db'}"
    if os.environ.get("TEST_DATABASE_URL"):
        engine = make_engine(url)
        Base.metadata.drop_all(engine)
        engine.dispose()
    return Settings(database_url=url, decision_cutoff_time=time(9, 0), _env_file=None)


@pytest.fixture
def repos(settings: Settings) -> tuple[ActualRepository, ForecastRepository]:
    engine = make_engine(settings.database_url)
    init_db(engine)
    sf = make_session_factory(engine)
    return ActualRepository(sf), ForecastRepository(sf)


@pytest.fixture
def client(settings: Settings, elexon_client: ElexonClient) -> Iterator[TestClient]:
    with TestClient(create_app(settings, elexon_client)) as test_client:
        yield test_client
