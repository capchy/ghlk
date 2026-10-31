"""Async client for the Elexon Insights API.

Endpoints (verified against https://data.elexon.co.uk/swagger/v1/swagger.json
and live responses):

* ``GET /datasets/FUELHH/stream`` - ``settlementDateFrom``/``settlementDateTo``
  (``yyyy-MM-dd``) and ``fuelType``. Elexon rejects settlement-date filters
  combined with publish-date filters, so publish filters are never sent.
* ``GET /datasets/WINDFOR/stream`` - ``publishDateTimeFrom``/``publishDateTimeTo``.
  Returns every forecast publication (vintage) in the range.
* ``GET /forecast/generation/wind/latest/stream`` - required ``from``/``to``,
  filtering on ``startTime``. Returns only the newest publication per period.

Elexon treats both ends of these ranges as inclusive; callers get half-open
semantics from this client.
"""

from __future__ import annotations

import asyncio
import email.utils
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Generic, TypeVar

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError

logger = logging.getLogger(__name__)


class ElexonError(RuntimeError):
    """Elexon could not be reached or returned an unusable response."""


class _Row(BaseModel):
    model_config = ConfigDict(extra="ignore")


class FuelHHRow(_Row):
    dataset: str = "FUELHH"
    publishTime: AwareDatetime
    startTime: AwareDatetime
    settlementDate: date
    settlementPeriod: int
    fuelType: str
    # Required: a missing generation value is invalid, never zero.
    generation: float


class WindForRow(_Row):
    dataset: str = "WINDFOR"
    publishTime: AwareDatetime
    startTime: AwareDatetime
    settlementDate: date | None = None
    settlementPeriod: int | None = None
    generation: float


RowT = TypeVar("RowT", bound=_Row)


@dataclass
class FetchResult(Generic[RowT]):
    rows: list[RowT] = field(default_factory=list)
    invalid: int = 0
    invalid_examples: list[str] = field(default_factory=list)
    requests: int = 0


def normalise_payload(payload: Any, path: str) -> list[Any]:
    """Accept a bare JSON array or an envelope ``{"data": [...]}``."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return payload["data"]
    raise ElexonError(f"Unexpected response format from {path}: expected a JSON array or an object with 'data'")


def _iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _retry_after_seconds(response: httpx.Response) -> float | None:
    header = response.headers.get("Retry-After")
    if not header:
        return None
    try:
        return max(0.0, float(header))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(header)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


class ElexonClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 30.0,
        max_retries: int = 3,
        backoff_base: float = 0.5,
        max_retry_after: float = 60.0,
        fuelhh_chunk_days: int = 14,
        windfor_chunk_days: int = 7,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        # A client is opened per request rather than held for the process lifetime:
        # on serverless hosts each invocation may run on a different event loop.
        self._client_kwargs: dict[str, Any] = {
            "base_url": base_url.rstrip("/"),
            "timeout": timeout,
            "transport": transport,
            "headers": {"Accept": "application/json"},
        }
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._max_retry_after = max_retry_after
        self._fuelhh_chunk_days = fuelhh_chunk_days
        self._windfor_chunk_days = windfor_chunk_days
        self._sleep = sleep

    async def aclose(self) -> None:
        """Kept for symmetry with the app lifespan; there is no long-lived connection."""

    async def _get_rows(self, path: str, params: dict[str, Any]) -> list[Any]:
        last_error = "unknown error"
        for attempt in range(self._max_retries + 1):
            delay = self._backoff_base * 2**attempt
            try:
                async with httpx.AsyncClient(**self._client_kwargs) as client:
                    response = await client.get(path, params=params)
            except httpx.TimeoutException:
                last_error = "request timed out"
            except httpx.TransportError as exc:
                last_error = f"network error: {exc.__class__.__name__}"
            else:
                status = response.status_code
                if status == 429 or status >= 500:
                    last_error = f"HTTP {status}"
                    retry_after = _retry_after_seconds(response)
                    if retry_after is not None:
                        delay = min(retry_after, self._max_retry_after)
                elif status >= 400:
                    raise ElexonError(f"Elexon rejected {path} with HTTP {status}: {response.text[:300]}")
                else:
                    try:
                        payload = response.json()
                    except ValueError as exc:
                        raise ElexonError(f"Elexon returned invalid JSON for {path}") from exc
                    return normalise_payload(payload, path)

            if attempt < self._max_retries:
                logger.warning(
                    "Elexon %s failed (%s), attempt %d/%d; retrying in %.1fs",
                    path,
                    last_error,
                    attempt + 1,
                    self._max_retries + 1,
                    delay,
                )
                await self._sleep(delay)
        logger.error(
            "Elexon %s failed after %d attempts: %s (params=%s)", path, self._max_retries + 1, last_error, params
        )
        raise ElexonError(f"Elexon request to {path} failed after {self._max_retries + 1} attempts: {last_error}")

    @staticmethod
    def _validate(raw: list[Any], model: type[RowT], result: FetchResult[RowT]) -> None:
        for item in raw:
            try:
                result.rows.append(model.model_validate(item))
            except ValidationError as exc:
                result.invalid += 1
                if len(result.invalid_examples) < 5:
                    result.invalid_examples.append(f"{model.__name__}: {exc.errors()[0]['msg']} in {str(item)[:120]}")

    async def fetch_actual_wind(self, date_from: date, date_to: date) -> FetchResult[FuelHHRow]:
        """FUELHH wind rows for settlement dates ``date_from..date_to`` inclusive."""
        result: FetchResult[FuelHHRow] = FetchResult()
        chunk_start = date_from
        while chunk_start <= date_to:
            chunk_end = min(chunk_start + timedelta(days=self._fuelhh_chunk_days - 1), date_to)
            raw = await self._get_rows(
                "/datasets/FUELHH/stream",
                {
                    "settlementDateFrom": chunk_start.isoformat(),
                    "settlementDateTo": chunk_end.isoformat(),
                    "fuelType": "WIND",
                },
            )
            result.requests += 1
            self._validate(raw, FuelHHRow, result)
            chunk_start = chunk_end + timedelta(days=1)
        # Defensive: the fuelType filter should already restrict to WIND.
        result.rows = [r for r in result.rows if r.fuelType == "WIND"]
        return result

    async def fetch_forecast_publications(
        self, publish_from: datetime, publish_to: datetime
    ) -> FetchResult[WindForRow]:
        """All WINDFOR forecasts published within ``[publish_from, publish_to)``."""
        result: FetchResult[WindForRow] = FetchResult()
        step = timedelta(days=self._windfor_chunk_days)
        chunk_start = publish_from
        while chunk_start < publish_to:
            chunk_end = min(chunk_start + step, publish_to)
            raw = await self._get_rows(
                "/datasets/WINDFOR/stream",
                {"publishDateTimeFrom": _iso_z(chunk_start), "publishDateTimeTo": _iso_z(chunk_end)},
            )
            result.requests += 1
            chunk: FetchResult[WindForRow] = FetchResult()
            self._validate(raw, WindForRow, chunk)
            # Elexon bounds are inclusive; keep chunks half-open so a
            # publication on a boundary is not fetched twice.
            result.rows.extend(r for r in chunk.rows if chunk_start <= r.publishTime < chunk_end)
            result.invalid += chunk.invalid
            result.invalid_examples.extend(chunk.invalid_examples[: 5 - len(result.invalid_examples)])
            chunk_start = chunk_end
        return result

    async def fetch_latest_forecast(self, start_from: datetime, start_to: datetime) -> FetchResult[WindForRow]:
        """Newest forecast for each period with ``start_from <= startTime < start_to``."""
        raw = await self._get_rows(
            "/forecast/generation/wind/latest/stream",
            {"from": _iso_z(start_from), "to": _iso_z(start_to)},
        )
        result: FetchResult[WindForRow] = FetchResult(requests=1)
        self._validate(raw, WindForRow, result)
        result.rows = [r for r in result.rows if start_from <= r.startTime < start_to]
        return result
