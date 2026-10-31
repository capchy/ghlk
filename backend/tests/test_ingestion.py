from datetime import date, timedelta

import pytest

from app.clients.elexon_client import ElexonClient
from app.core.config import Settings
from app.db.repositories import ActualRepository, ForecastRepository
from app.services.ingestion_service import IngestionService
from app.services.normalization_service import ActualRecord
from tests.conftest import FakeElexon, utc


@pytest.fixture
def service(settings: Settings, elexon_client: ElexonClient, repos) -> IngestionService:
    return IngestionService(settings, elexon_client, *repos)


async def test_persists_actuals_and_forecast_versions(service: IngestionService, repos) -> None:
    actuals, forecasts = repos
    report = await service.ingest(date(2026, 10, 10), date(2026, 10, 11))
    assert report.status == "success"
    fuel, windfor = report.datasets
    assert fuel.inserted == 96 and fuel.fetched == 96
    assert windfor.inserted > 0
    stored = actuals.load(utc(2026, 10, 9, 23), utc(2026, 10, 11, 23))
    assert len(stored) == 96 and stored["actual_mw"].notna().all()
    # Many publications for one target period are all kept.
    assert len(forecasts.evolution(utc(2026, 10, 11, 12))) >= 8


async def test_ingestion_is_idempotent(service: IngestionService, repos) -> None:
    first = await service.ingest(date(2026, 10, 10), date(2026, 10, 10))
    second = await service.ingest(date(2026, 10, 10), date(2026, 10, 10))
    for a, b in zip(first.datasets, second.datasets, strict=True):
        assert b.fetched == a.fetched and b.inserted == 0 and b.updated == 0 and b.skipped == a.fetched
    assert repos[0].stats()["count"] == 48


async def test_forecast_publication_lookback_range(
    service: IngestionService, fake_elexon: FakeElexon, settings: Settings
) -> None:
    report = await service.ingest(date(2026, 10, 10), date(2026, 10, 10), datasets=("WINDFOR",))
    windfor = report.datasets[0]
    # Publications from midnight (BST) on D-1-lookback = 2 Oct.
    assert settings.forecast_publication_lookback_days == 7
    assert windfor.requested_from == "2026-10-01T23:00:00+00:00"
    assert windfor.requested_to == "2026-10-10T23:00:00+00:00"
    assert fake_elexon.calls["/datasets/FUELHH/stream"] == 0


async def test_dst_day_stores_50_periods(service: IngestionService, repos) -> None:
    await service.ingest(date(2026, 10, 25), date(2026, 10, 25), datasets=("FUELHH",))
    assert repos[0].counts_by_settlement_date(date(2026, 10, 25), date(2026, 10, 25)) == {date(2026, 10, 25): 50}


async def test_upstream_failure_reported_per_dataset(service: IngestionService, fake_elexon: FakeElexon) -> None:
    import httpx

    def fail_fuelhh(request: httpx.Request) -> httpx.Response:
        if "FUELHH" in request.url.path:
            return httpx.Response(500)
        fake_elexon.respond = None
        response = fake_elexon.handler(request)
        fake_elexon.respond = fail_fuelhh
        return response

    fake_elexon.respond = fail_fuelhh
    report = await service.ingest(date(2026, 10, 10), date(2026, 10, 10))
    assert report.status == "partial"
    assert report.errors and report.errors[0].startswith("FUELHH")
    assert report.datasets[1].inserted > 0


async def test_empty_response_warns(service: IngestionService, fake_elexon: FakeElexon) -> None:
    import httpx

    fake_elexon.respond = lambda request: httpx.Response(200, json=[])
    report = await service.ingest(date(2026, 10, 10), date(2026, 10, 10))
    assert report.status == "success"
    assert any("no wind records" in w for w in report.warnings)


async def test_invalid_date_range(service: IngestionService) -> None:
    with pytest.raises(ValueError):
        await service.ingest(date(2026, 10, 10), date(2026, 10, 9))
    with pytest.raises(ValueError):
        await service.ingest(date(2026, 10, 10), None)


def _actual(publish_minute: int, mw: float) -> ActualRecord:
    return ActualRecord(
        "FUELHH", utc(2026, 10, 1, 1, publish_minute), utc(2026, 10, 1, 0), date(2026, 10, 1), 3, "WIND", mw
    )


def test_actual_revision_keeps_latest_publication(repos) -> None:
    actuals: ActualRepository = repos[0]
    assert actuals.upsert([_actual(0, 100)]).inserted == 1
    newer = actuals.upsert([_actual(30, 120)])
    assert newer.updated == 1
    older = actuals.upsert([_actual(15, 999)])  # stale revision must not win
    assert older.skipped == 1 and older.updated == 0
    stored = actuals.load(utc(2026, 10, 1), utc(2026, 10, 2))
    assert stored["actual_mw"].tolist() == [120]


def test_duplicate_records_in_one_batch(repos) -> None:
    actuals: ActualRepository = repos[0]
    counts = actuals.upsert([_actual(0, 100), _actual(30, 120), _actual(30, 120)])
    assert counts.inserted == 1 and counts.skipped == 2


def test_forecasts_are_never_overwritten(repos) -> None:
    from app.services.normalization_service import ForecastRecord

    forecasts: ForecastRepository = repos[1]
    original = ForecastRecord("WINDFOR", utc(2026, 10, 14, 9, 30), utc(2026, 10, 15, 10), None, None, 12193)
    revised_value = ForecastRecord("WINDFOR", utc(2026, 10, 14, 9, 30), utc(2026, 10, 15, 10), None, None, 1)
    later = ForecastRecord("WINDFOR", utc(2026, 10, 14, 11, 30), utc(2026, 10, 15, 10), None, None, 12362)
    assert forecasts.insert_new([original]).inserted == 1
    assert forecasts.insert_new([revised_value, later]).inserted == 1
    assert forecasts.evolution(utc(2026, 10, 15, 10)) == [
        (utc(2026, 10, 14, 9, 30), 12193),
        (utc(2026, 10, 14, 11, 30), 12362),
    ]


def test_loader_applies_half_open_window(repos) -> None:
    actuals: ActualRepository = repos[0]
    records = [
        ActualRecord("FUELHH", utc(2026, 10, 1, h, 30), utc(2026, 10, 1, h), date(2026, 10, 1), 2 * h + 3, "WIND", h)
        for h in range(4)
    ]
    actuals.upsert(records)
    loaded = actuals.load(utc(2026, 10, 1, 1), utc(2026, 10, 1, 3))
    assert [t.hour for t in loaded["start_time"]] == [1, 2]
    assert timedelta(0) == loaded["start_time"].iloc[0] - utc(2026, 10, 1, 1)
