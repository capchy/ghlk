from collections.abc import Iterator
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient

from app.clients.elexon_client import ElexonClient
from app.core.config import Settings
from app.main import create_app
from tests.conftest import FakeElexon, hourly_actual_mw

LONDON = ZoneInfo("Europe/London")
# A fixed historical range (all BST) so expectations are deterministic.
RANGE = {"start_date": "2026-09-14", "end_date": "2026-10-11"}  # 28 local days


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@pytest.fixture
def ingested(client: TestClient) -> TestClient:
    response = client.post("/api/v1/data/ingest", json=RANGE)
    assert response.status_code == 200 and response.json()["status"] == "success"
    return client


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "healthy"}


def test_works_when_lifespan_is_skipped(settings: Settings, elexon_client: ElexonClient) -> None:
    # Some serverless runtimes never send ASGI lifespan events; the container is built lazily.
    bare = TestClient(create_app(settings, elexon_client))  # no `with`: lifespan does not run
    assert bare.get("/api/v1/data/status").status_code == 200
    assert (
        bare.post("/api/v1/data/ingest", json={"start_date": "2026-10-01", "end_date": "2026-10-01"}).status_code == 200
    )
    assert (
        bare.get("/api/v1/accuracy/summary", params={"start_date": "2026-10-01", "end_date": "2026-10-01"}).status_code
        == 200
    )


def test_ingest_reports_counts_and_is_idempotent(client: TestClient) -> None:
    first = client.post("/api/v1/data/ingest", json={"start_date": "2026-10-01", "end_date": "2026-10-02"}).json()
    assert first["records_inserted"] == first["records_fetched"] > 0
    assert {d["dataset"] for d in first["datasets"]} == {"FUELHH", "WINDFOR"}
    second = client.post("/api/v1/data/ingest", json={"start_date": "2026-10-01", "end_date": "2026-10-02"}).json()
    assert second["records_inserted"] == 0 and second["records_skipped"] == first["records_fetched"]


def test_ingest_dataset_selection_and_default_range(client: TestClient, fake_elexon: FakeElexon) -> None:
    body = client.post("/api/v1/data/ingest", json={"datasets": ["FUELHH"]}).json()
    assert [d["dataset"] for d in body["datasets"]] == ["FUELHH"]
    assert fake_elexon.calls["/datasets/WINDFOR/stream"] == 0


def test_ingest_validation(client: TestClient) -> None:
    assert client.post("/api/v1/data/ingest", json={"start_date": "2026-10-01"}).status_code == 422
    assert (
        client.post("/api/v1/data/ingest", json={"start_date": "2026-10-02", "end_date": "2026-10-01"}).status_code
        == 422
    )
    assert client.post("/api/v1/data/ingest", json={"datasets": ["NOPE"]}).status_code == 422


def test_ingest_upstream_failure_returns_502(client: TestClient, fake_elexon: FakeElexon) -> None:
    fake_elexon.respond = lambda request: httpx.Response(503)
    response = client.post("/api/v1/data/ingest", json={"start_date": "2026-10-01", "end_date": "2026-10-01"})
    assert response.status_code == 502
    body = response.json()
    assert body["status"] == "failed" and len(body["errors"]) == 2


def test_status_on_empty_database(client: TestClient) -> None:
    body = client.get("/api/v1/data/status").json()
    assert body["actual"]["record_count"] == 0 and body["forecast"]["record_count"] == 0
    assert body["alignment"]["status"] == "no_data"
    assert body["default_window"]["ready"] is False
    assert any("data/ingest" in w for w in body["warnings"])


def test_status_after_ingest(ingested: TestClient) -> None:
    body = ingested.get("/api/v1/data/status").json()
    assert body["actual"]["record_count"] == 28 * 48
    assert body["actual"]["missing_periods"] == 0
    assert body["actual"]["latest_generation_period_end"] == "2026-10-11T23:00:00Z"
    assert body["forecast"]["publication_count"] > 0
    assert body["alignment"]["status"] == "inferred" and body["alignment"]["forecast_resolution_minutes"] == 60


def test_accuracy_summary_custom_range(ingested: TestClient) -> None:
    body = ingested.get("/api/v1/accuracy/summary", params=RANGE).json()
    assert body["analysis_window"] == {
        "start": "2026-09-13T23:00:00Z",
        "end": "2026-10-11T23:00:00Z",
        "days": 28.0,
        "mode": "custom",
    }
    assert body["decision_cutoff"]["time"] == "09:00"
    coverage = body["coverage"]
    assert coverage["target_periods"] == coverage["records_matched"] == 28 * 24
    assert coverage["actual_data_complete"] is True and coverage["records_unmatched"] == 0
    metrics = body["metrics"]
    # Synthetic forecasts overforecast by 10 MW per hour of lead time -> positive bias.
    assert metrics["bias_mw"] > 0 and metrics["mae_mw"] == metrics["bias_mw"]
    assert body["error_range"]["available"] is True
    levels = body["error_range_by_level"]
    assert levels["available"] and len(levels["bands"]) == 5
    assert sum(band["error_range"]["sample_count"] for band in levels["bands"]) == 28 * 24
    assert (
        body["error_range"]["p10_error_mw"]
        <= body["error_range"]["p50_error_mw"]
        <= body["error_range"]["p90_error_mw"]
    )
    weekly = body["breakdown"]
    assert body["granularity"] == "weekly" and len(weekly) == 4
    assert [w["sample_count"] for w in weekly] == [168] * 4
    assert weekly[0]["period_start"] == body["analysis_window"]["start"]
    assert weekly[-1]["period_end"] == body["analysis_window"]["end"]


def test_accuracy_granularity(ingested: TestClient) -> None:
    daily = ingested.get("/api/v1/accuracy/summary", params={**RANGE, "granularity": "daily"}).json()["breakdown"]
    assert len(daily) == 28 and all(d["sample_count"] == 24 for d in daily)
    overall = ingested.get("/api/v1/accuracy/summary", params={**RANGE, "granularity": "overall"}).json()
    assert overall["breakdown"] == []


def test_series_never_uses_post_cutoff_publications(ingested: TestClient) -> None:
    body = ingested.get("/api/v1/accuracy/series", params=RANGE).json()
    assert len(body["series"]) == 28 * 24
    for point in body["series"]:
        start = parse(point["timestamp"])
        delivery = start.astimezone(LONDON).date()
        cutoff = datetime.combine(delivery - timedelta(days=1), time(9), tzinfo=LONDON)
        published = parse(point["forecast_publish_time"])
        assert published <= cutoff
        assert cutoff - published < timedelta(hours=3)  # latest eligible of the 3-hourly synthetic schedule
        assert point["actual_mw"] == hourly_actual_mw(start)
        assert point["error_mw"] == point["forecast_mw"] - point["actual_mw"]
        assert point["absolute_error_mw"] == abs(point["error_mw"])


def test_decision_cutoff_parameter_changes_selection(ingested: TestClient) -> None:
    early = ingested.get("/api/v1/accuracy/summary", params={**RANGE, "decision_cutoff": "06:00"}).json()
    late = ingested.get("/api/v1/accuracy/summary", params={**RANGE, "decision_cutoff": "18:00"}).json()
    assert early["decision_cutoff"]["time"] == "06:00"
    # Later cutoff -> shorter lead time -> smaller synthetic error.
    assert late["metrics"]["mae_mw"] < early["metrics"]["mae_mw"]


def test_distribution_uses_same_matched_observations(ingested: TestClient) -> None:
    body = ingested.get("/api/v1/accuracy/distribution", params={**RANGE, "bins": 10}).json()
    assert body["sample_count"] == sum(b["count"] for b in body["bins"]) == 28 * 24
    assert body["zero_reference_mw"] == 0
    assert body["overforecast_count"] == 28 * 24


def test_incomplete_recent_actuals_are_flagged(client: TestClient, fake_elexon: FakeElexon) -> None:
    fake_elexon.missing_actuals = {parse("2026-10-05T10:30:00Z")}
    client.post("/api/v1/data/ingest", json=RANGE)
    body = client.get("/api/v1/accuracy/summary", params=RANGE).json()
    assert body["coverage"]["missing_actual_periods"] == 1
    assert body["coverage"]["unmatched_by_reason"]["incomplete_actuals"] == 1
    assert body["coverage"]["unmatched_examples"][0] == {
        "start_time": "2026-10-05T10:00:00Z",
        "reason": "incomplete_actuals",
    }
    assert any("lack complete half-hourly actuals" in w for w in body["warnings"])


def test_accuracy_on_empty_database(client: TestClient) -> None:
    body = client.get("/api/v1/accuracy/summary").json()
    assert body["metrics"]["sample_count"] == 0 and body["metrics"]["mae_mw"] is None
    assert body["error_range"]["available"] is False
    assert len(body["breakdown"]) == 4
    assert body["analysis_window"]["mode"] == "rolling" and body["analysis_window"]["days"] == 28


def test_query_validation(client: TestClient) -> None:
    for params in (
        {"window_days": 0},
        {"start_date": "2026-10-01"},
        {"start_date": "2026-10-05", "end_date": "2026-10-01"},
        {"granularity": "monthly"},
        {"decision_cutoff": "25:00"},
    ):
        assert client.get("/api/v1/accuracy/summary", params=params).status_code == 422, params
    assert client.get("/api/v1/accuracy/distribution", params={"bins": 1}).status_code == 422
    assert client.get("/api/v1/forecasts", params={"as_of": "2026-10-01T00:00:00"}).status_code == 422
    assert client.get("/api/v1/forecasts/evolution").status_code == 422


@pytest.fixture
def client_without_cutoff(tmp_path, elexon_client: ElexonClient) -> Iterator[TestClient]:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'nocutoff.db'}", decision_cutoff_time=None, _env_file=None)
    with TestClient(create_app(settings, elexon_client)) as c:
        yield c


def test_missing_decision_cutoff(client_without_cutoff: TestClient) -> None:
    for path in ("/api/v1/accuracy/summary", "/api/v1/accuracy/series", "/api/v1/accuracy/distribution"):
        response = client_without_cutoff.get(path)
        assert response.status_code == 422 and "DECISION_CUTOFF_TIME" in response.json()["detail"]
    assert client_without_cutoff.get("/api/v1/accuracy/summary", params={"decision_cutoff": "09:00"}).status_code == 200
    dashboard = client_without_cutoff.get("/api/v1/dashboard").json()
    assert dashboard["historical_accuracy"] is None
    assert any("decision cutoff" in w for w in dashboard["warnings"])
    assert dashboard["forecast"]["available"] is True


def test_settings_treat_blank_cutoff_as_unset() -> None:
    assert Settings(decision_cutoff_time="", _env_file=None).decision_cutoff_time is None
    assert Settings(decision_cutoff_time="10:30", _env_file=None).decision_cutoff_time == time(10, 30)
    assert Settings(decision_cutoff_time=" 9:00 ", _env_file=None).decision_cutoff_time == time(9, 0)


def test_forecast_latest_for_delivery_date(client: TestClient) -> None:
    body = client.get("/api/v1/forecasts", params={"delivery_date": "2026-11-05"}).json()
    assert body["available"] and body["complete"] and body["data_source"] == "elexon_latest_endpoint"
    assert body["day_start"] == "2026-11-05T00:00:00Z" and body["expected_periods"] == 24
    assert len(body["items"]) == 24 and len(body["selected_publish_times"]) == 1
    assert body["latest_publish_time"] == body["items"][0]["publish_time"]
    assert {"start_time", "publish_time", "generation_mw"} <= body["items"][0].keys()


def test_forecast_defaults_to_tomorrow(client: TestClient) -> None:
    tomorrow = datetime.now(LONDON).date() + timedelta(days=1)
    assert client.get("/api/v1/forecasts").json()["delivery_date"] == tomorrow.isoformat()


def test_forecast_as_of_uses_stored_publications(ingested: TestClient) -> None:
    body = ingested.get(
        "/api/v1/forecasts", params={"delivery_date": "2026-10-10", "as_of": "2026-10-09T06:00:00Z"}
    ).json()
    assert body["data_source"] == "stored_publications"
    assert body["available"] and len(body["items"]) == 24
    assert all(parse(i["publish_time"]) <= parse("2026-10-09T06:00:00Z") for i in body["items"])
    assert body["selected_publish_times"] == ["2026-10-09T05:30:00Z"]


def test_forecast_not_found(client: TestClient) -> None:
    body = client.get(
        "/api/v1/forecasts", params={"delivery_date": "2026-10-10", "as_of": "2026-10-09T06:00:00Z"}
    ).json()
    assert body["available"] is False and body["items"] == []
    assert any("No forecast" in w for w in body["warnings"])


def test_forecast_upstream_failure(client: TestClient, fake_elexon: FakeElexon) -> None:
    fake_elexon.respond = lambda request: httpx.Response(500)
    response = client.get("/api/v1/forecasts", params={"delivery_date": "2026-11-05"})
    assert response.status_code == 502 and "Elexon" in response.json()["detail"]


def test_forecast_falls_back_to_stored_when_elexon_down(ingested: TestClient, fake_elexon: FakeElexon) -> None:
    fake_elexon.respond = lambda request: httpx.Response(500)
    body = ingested.get("/api/v1/forecasts", params={"delivery_date": "2026-10-11"}).json()
    assert body["data_source"] == "stored_publications" and body["available"]
    assert any("unavailable" in w for w in body["warnings"])


def test_forecast_evolution(ingested: TestClient) -> None:
    body = ingested.get("/api/v1/forecasts/evolution", params={"target_start_time": "2026-10-05T10:00:00Z"}).json()
    times = [parse(p["publish_time"]) for p in body["series"]]
    assert body["publication_count"] == len(times) >= 8
    assert times == sorted(times)
    empty = ingested.get("/api/v1/forecasts/evolution", params={"target_start_time": "2026-10-05T10:15:00Z"}).json()
    assert empty["series"] == [] and empty["message"]


def test_dashboard(ingested: TestClient) -> None:
    body = ingested.get(
        "/api/v1/dashboard", params={**RANGE, "delivery_date": "2026-11-05", "decision_cutoff": "09:00"}
    ).json()
    hist = body["historical_accuracy"]
    assert hist["metrics"]["sample_count"] == 28 * 24 and len(hist["weekly"]) == 4
    assert hist["alignment"]["status"] == "inferred"
    item = body["forecast"]["items"][0]
    rng = item["estimated_actual_range"]
    assert rng["lower_mw"] <= item["generation_mw"] or rng["upper_mw"] <= item["generation_mw"]
    assert rng["lower_mw"] <= rng["upper_mw"]
    assert hist["error_range_by_level"]["available"]
    for item in body["forecast"]["items"]:
        level = item["estimated_actual_range_by_level"]
        assert level["lower_mw"] <= level["upper_mw"] and level["band_sample_count"] >= 30
        assert level["band_forecast_min_mw"] is None or item["generation_mw"] >= level["band_forecast_min_mw"]
        assert level["band_forecast_max_mw"] is None or item["generation_mw"] < level["band_forecast_max_mw"]
    assert "procurement" in body["scope_note"]


def test_dashboard_shows_forecast_available_at_decision_cutoff(ingested: TestClient) -> None:
    # Delivery 10 Oct: cutoff 09:00 BST on 9 Oct (08:00Z) has passed, so later publications are excluded.
    body = ingested.get("/api/v1/dashboard", params={**RANGE, "delivery_date": "2026-10-10"}).json()
    assert body["forecast_basis"] == "decision_cutoff"
    assert body["decision_at"] == "2026-10-09T08:00:00Z"
    forecast = body["forecast"]
    assert forecast["data_source"] == "stored_publications" and forecast["available"]
    assert forecast["selected_publish_times"] == ["2026-10-09T05:30:00Z"]


def test_dashboard_uses_latest_before_cutoff(ingested: TestClient) -> None:
    body = ingested.get("/api/v1/dashboard", params={**RANGE, "delivery_date": "2026-11-05"}).json()
    assert body["forecast_basis"] == "latest"
    assert body["forecast"]["data_source"] == "elexon_latest_endpoint"


def test_dashboard_survives_forecast_outage(ingested: TestClient, fake_elexon: FakeElexon) -> None:
    fake_elexon.respond = lambda request: httpx.Response(500)
    body = ingested.get("/api/v1/dashboard", params={**RANGE, "delivery_date": "2026-11-05"}).json()
    assert body["forecast"] is None and body["historical_accuracy"] is not None
    assert any("unavailable" in w for w in body["warnings"])
