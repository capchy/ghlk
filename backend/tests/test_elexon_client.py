from datetime import date

import httpx
import pytest

from app.clients.elexon_client import ElexonClient, ElexonError, normalise_payload
from tests.conftest import BASE_URL, FakeElexon, SleepRecorder, utc


async def test_fuelhh_request_parameters_and_chunking(fake_elexon: FakeElexon) -> None:
    client = ElexonClient(BASE_URL, fuelhh_chunk_days=3, transport=httpx.MockTransport(fake_elexon.handler))
    result = await client.fetch_actual_wind(date(2026, 10, 1), date(2026, 10, 7))
    assert result.requests == 3  # 3 + 3 + 1 days
    assert len(result.rows) == 7 * 48
    first = fake_elexon.requests[0].url
    assert first.path == "/bmrs/api/v1/datasets/FUELHH/stream"
    assert dict(first.params) == {
        "settlementDateFrom": "2026-10-01",
        "settlementDateTo": "2026-10-03",
        "fuelType": "WIND",
    }
    await client.aclose()


async def test_windfor_parameters_and_half_open_chunks(fake_elexon: FakeElexon) -> None:
    client = ElexonClient(BASE_URL, windfor_chunk_days=1, transport=httpx.MockTransport(fake_elexon.handler))
    start, end = utc(2026, 10, 1, 2, 30), utc(2026, 10, 3, 2, 30)
    result = await client.fetch_forecast_publications(start, end)
    params = fake_elexon.requests[0].url.params
    assert params["publishDateTimeFrom"] == "2026-10-01T02:30:00Z"
    assert params["publishDateTimeTo"] == "2026-10-02T02:30:00Z"
    pubs = {r.publishTime for r in result.rows}
    assert len(pubs) == 16 and start in pubs and end not in pubs  # boundary publication counted once
    assert len(result.rows) == len({(r.publishTime, r.startTime) for r in result.rows})
    await client.aclose()


async def test_latest_endpoint_parameters(fake_elexon: FakeElexon, elexon_client: ElexonClient) -> None:
    result = await elexon_client.fetch_latest_forecast(utc(2026, 11, 1), utc(2026, 11, 2))
    params = fake_elexon.requests[0].url.params
    assert fake_elexon.requests[0].url.path.endswith("/forecast/generation/wind/latest/stream")
    assert params["from"] == "2026-11-01T00:00:00Z" and params["to"] == "2026-11-02T00:00:00Z"
    # The inclusive `to` bound is trimmed: exactly the 24 hours of the day.
    assert len(result.rows) == 24 and max(r.startTime for r in result.rows) == utc(2026, 11, 1, 23)


def test_normalise_payload_accepts_array_or_envelope() -> None:
    assert normalise_payload([1], "/x") == [1]
    assert normalise_payload({"data": [1], "metadata": {}}, "/x") == [1]
    with pytest.raises(ElexonError, match="Unexpected response format"):
        normalise_payload({"rows": []}, "/x")
    with pytest.raises(ElexonError):
        normalise_payload("text", "/x")


async def test_envelope_response_is_normalised(fake_elexon: FakeElexon, elexon_client: ElexonClient) -> None:
    fake_elexon.envelope = True
    result = await elexon_client.fetch_actual_wind(date(2026, 10, 1), date(2026, 10, 1))
    assert len(result.rows) == 48


async def test_empty_response_is_empty_not_zero(fake_elexon: FakeElexon, elexon_client: ElexonClient) -> None:
    fake_elexon.respond = lambda request: httpx.Response(200, json=[])
    result = await elexon_client.fetch_actual_wind(date(2026, 10, 1), date(2026, 10, 1))
    assert result.rows == [] and result.invalid == 0


async def test_invalid_records_are_counted_not_zero_filled(
    fake_elexon: FakeElexon, elexon_client: ElexonClient
) -> None:
    good = {
        "publishTime": "2026-10-01T00:30:00Z",
        "startTime": "2026-10-01T00:00:00Z",
        "settlementDate": "2026-10-01",
        "settlementPeriod": 1,
        "fuelType": "WIND",
        "generation": 0,
    }
    missing_generation = {k: v for k, v in good.items() if k != "generation"}
    naive_time = {**good, "startTime": "2026-10-01T00:30:00"}
    fake_elexon.respond = lambda request: httpx.Response(200, json=[good, missing_generation, naive_time])
    result = await elexon_client.fetch_actual_wind(date(2026, 10, 1), date(2026, 10, 1))
    assert [r.generation for r in result.rows] == [0]  # zero is valid data
    assert result.invalid == 2 and result.invalid_examples


async def test_invalid_json_raises(fake_elexon: FakeElexon, elexon_client: ElexonClient) -> None:
    fake_elexon.respond = lambda request: httpx.Response(200, text="<html>")
    with pytest.raises(ElexonError, match="invalid JSON"):
        await elexon_client.fetch_actual_wind(date(2026, 10, 1), date(2026, 10, 1))


async def test_5xx_is_retried_with_exponential_backoff(
    fake_elexon: FakeElexon, elexon_client: ElexonClient, sleeps: SleepRecorder
) -> None:
    fake_elexon.respond = lambda request: httpx.Response(503)
    with pytest.raises(ElexonError, match="HTTP 503"):
        await elexon_client.fetch_actual_wind(date(2026, 10, 1), date(2026, 10, 1))
    assert fake_elexon.calls["/datasets/FUELHH/stream"] == 3  # 1 + max_retries(2)
    assert sleeps.delays == [0.5, 1.0]


async def test_5xx_then_success(fake_elexon: FakeElexon, elexon_client: ElexonClient) -> None:
    responses = iter([httpx.Response(502), httpx.Response(200, json=[])])
    fake_elexon.respond = lambda request: next(responses)
    result = await elexon_client.fetch_actual_wind(date(2026, 10, 1), date(2026, 10, 1))
    assert result.rows == [] and fake_elexon.calls["/datasets/FUELHH/stream"] == 2


async def test_429_respects_retry_after(
    fake_elexon: FakeElexon, elexon_client: ElexonClient, sleeps: SleepRecorder
) -> None:
    responses = iter([httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200, json=[])])
    fake_elexon.respond = lambda request: next(responses)
    await elexon_client.fetch_actual_wind(date(2026, 10, 1), date(2026, 10, 1))
    assert sleeps.delays == [7.0]


async def test_retry_after_is_capped(fake_elexon: FakeElexon, sleeps: SleepRecorder) -> None:
    client = ElexonClient(
        BASE_URL, max_retries=1, max_retry_after=5, transport=httpx.MockTransport(fake_elexon.handler), sleep=sleeps
    )
    fake_elexon.respond = lambda request: httpx.Response(429, headers={"Retry-After": "3600"})
    with pytest.raises(ElexonError, match="HTTP 429"):
        await client.fetch_actual_wind(date(2026, 10, 1), date(2026, 10, 1))
    assert sleeps.delays == [5]


async def test_timeout_is_retried_then_reported(fake_elexon: FakeElexon, elexon_client: ElexonClient) -> None:
    def raise_timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    fake_elexon.respond = raise_timeout
    with pytest.raises(ElexonError, match="timed out"):
        await elexon_client.fetch_actual_wind(date(2026, 10, 1), date(2026, 10, 1))
    assert fake_elexon.calls["/datasets/FUELHH/stream"] == 3


async def test_4xx_is_not_retried(fake_elexon: FakeElexon, elexon_client: ElexonClient) -> None:
    fake_elexon.respond = lambda request: httpx.Response(400, text="bad parameters")
    with pytest.raises(ElexonError, match="rejected"):
        await elexon_client.fetch_actual_wind(date(2026, 10, 1), date(2026, 10, 1))
    assert fake_elexon.calls["/datasets/FUELHH/stream"] == 1
