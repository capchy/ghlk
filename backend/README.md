# GB Wind Forecast Accuracy API

A FastAPI backend that measures how accurate Elexon's national wind generation forecasts have been
over a rolling four-week window, and serves the latest forecast for upcoming delivery dates. It
returns graph-ready JSON for a separately built React (Recharts) dashboard.

> **Scope.** This backend analyses **wind generation** forecasts and their historical accuracy for
> Great Britain. It does **not** calculate how much electricity to buy. That would need consumer
> demand data, which is out of scope. Empirical error ranges describe past forecast performance;
> they are not confidence guarantees or procurement buffers.

## Contents

1. [Business problem](#1-business-problem)
2. [Architecture](#2-architecture)
3. [Data sources: FUELHH and WINDFOR](#3-data-sources-fuelhh-and-windfor)
4. [publishTime vs startTime](#4-publishtime-vs-starttime)
5. [Matching forecasts to actuals](#5-matching-forecasts-to-actuals)
6. [Temporal resolution: findings and approach](#6-temporal-resolution-findings-and-approach)
7. [Day-ahead decision cutoff](#7-day-ahead-decision-cutoff)
8. [Rolling 28-day window](#8-rolling-28-day-window)
9. [Weekly buckets](#9-weekly-buckets)
10. [Metric definitions](#10-metric-definitions)
11. [Empirical historical error range](#11-empirical-historical-error-range)
12. [API endpoints](#12-api-endpoints)
13. [Example requests and responses](#13-example-requests-and-responses)
14. [Run locally](#14-run-locally)
15. [Run with Docker Compose](#15-run-with-docker-compose)
16. [Tests](#16-tests)
17. [Limitations and assumptions](#17-limitations-and-assumptions)
18. [Data attribution and references](#18-data-attribution-and-references)
19. [AI tools used](#19-ai-tools-used)

---

## 1. Business problem

A UK utility buys electricity on the day-ahead market. Part of the supply it expects comes from wind,
which is hard to predict. Buyers need answers to three questions:

1. **How much wind is expected** for an upcoming delivery date? → `GET /api/v1/forecasts`
2. **How accurate have the forecasts been?** → `GET /api/v1/accuracy/*`
3. **How much uncertainty should they allow for?** → the empirical error range in the summary and the dashboard

## 2. Architecture

```
backend/
├── app/
│   ├── main.py                       app factory, lifespan wiring, CORS, 502 handler
│   ├── api/
│   │   ├── deps.py                   service container + shared query parameters
│   │   └── v1/router.py, endpoints/  thin route handlers (health, ingestion, forecasts, accuracy, dashboard)
│   ├── core/                         config (env vars), logging, timeutils (UTC windows, Europe/London dates)
│   ├── clients/elexon_client.py      async HTTPX client: retries, Retry-After, chunking, validation
│   ├── db/                           SQLAlchemy models, engine, repositories (upsert / insert-only)
│   ├── schemas/                      Pydantic response models
│   └── services/
│       ├── ingestion_service.py           explicit, idempotent ingestion
│       ├── normalization_service.py       Elexon rows → storage records (UTC)
│       ├── temporal_alignment_service.py  resolution detection + actual aggregation
│       ├── forecast_selection_service.py  as-of / latest publication selection
│       ├── accuracy_service.py            matching, metrics, buckets, histogram, error range
│       ├── forecast_service.py            upcoming forecast + evolution
│       ├── dashboard_service.py           combines forecast + accuracy
│       └── status_service.py              data coverage report
├── tests/                                 91 tests, offline (mocked Elexon)
├── Dockerfile, docker-compose.yml, .env.example, requirements*.txt
```

**Data flow:**
1. `POST /data/ingest` → Elexon client → normalisation → SQLite.
2. The accuracy endpoints read SQLite and run **one** pipeline, `AccuracyService.analyse`:
   alignment → as-of selection → matching → metrics.

The summary, weekly buckets, series and histogram therefore always come from the same matched
observations. Dashboard requests never trigger bulk ingestion. The only live Elexon call made while
serving a request is one small request for the newest upcoming forecast.

## 3. Data sources: FUELHH and WINDFOR

Both endpoints were checked against the [OpenAPI spec](https://data.elexon.co.uk/swagger/v1/swagger.json)
and against live responses.

| Purpose | Endpoint | Parameters used |
|---|---|---|
| Actual wind outturn | `GET /datasets/FUELHH/stream` | `settlementDateFrom`, `settlementDateTo` (`yyyy-MM-dd`), `fuelType=WIND` |
| Historical forecast publications | `GET /datasets/WINDFOR/stream` | `publishDateTimeFrom`, `publishDateTimeTo` |
| Latest upcoming forecast | `GET /forecast/generation/wind/latest/stream` | `from`, `to` (both required; filter on `startTime`) |

- **FUELHH** gives half-hourly average MW by fuel type. Only `WIND` is used. Elexon rejects
  settlement-date filters combined with publish-date filters, so the client never sends publish
  filters to FUELHH. Whole settlement dates are fetched; exact UTC window bounds are applied in the backend.
- **WINDFOR (raw dataset)** returns **every** publication in a publish-time range. This is what
  backtesting needs: the *latest* endpoint keeps only the newest publication per period, which would
  leak hindsight into historical accuracy.
- **Latest endpoint** is used only for the upcoming display when no `as_of` is given. With `as_of`,
  stored WINDFOR publications are used instead, because the latest endpoint cannot answer "what was
  newest at time X?".

Verified behaviour that shaped the client:
- Elexon treats both ends of `publishDateTime*` and `from`/`to` as **inclusive**. The client splits
  ranges into half-open chunks so a publication on a boundary is not fetched twice, and trims the
  inclusive `to` bound.
- Responses may be a bare array or a `{"data": [...]}` envelope. Both are accepted; anything else is an error.
- Each record is validated on its own. A record with no generation value or with naive timestamps is
  counted as invalid and reported. It is never stored as 0. An empty response produces a warning,
  not "zero generation".

## 4. publishTime vs startTime

- **`publishTime`** is when a forecast became available.
- **`startTime`** is the delivery period the forecast is for.

One `startTime` has many publications over the preceding days. In the October 2026 data each
WINDFOR publication covers about −1 to +2 days, and a single target hour has ~20 publications.
The backend keeps all of them (`forecast_generation` is unique on `(publish_time, start_time)` and
never overwritten). The publication schedule is never hard-coded; it is read from the data.

## 5. Matching forecasts to actuals

For each target period `T` fully inside the analysis window:

1. **Align actuals** to the forecast resolution (section 6).
2. **Select the forecast**: the latest publication with `publishTime ≤ cutoff(T)` (section 7).
3. **Match** on the UTC `startTime`, never on settlement period alone. A matched record looks like:

```json
{ "timestamp": "2026-10-15T10:00:00Z", "forecast_publish_time": "2026-10-14T07:30:00Z",
  "forecast_mw": 12362, "actual_mw": 12100, "error_mw": 262, "absolute_error_mw": 262 }
```

**`error_mw = forecast_mw − actual_mw`**: positive means the forecast was too high, negative means it
was too low.

Periods that cannot be matched are left out of the metrics and reported in `coverage`:
- **`no_eligible_forecast`**: nothing was published by the cutoff.
- **`incomplete_actuals`**: a half-hour is missing in the past.
- **`awaiting_actuals`**: the period is after the latest actual received; the data has not arrived yet.

`coverage` shows the counts by reason and up to 20 example periods. An actual value of 0 MW is valid data.

## 6. Temporal resolution: findings and approach

These are the Phase 1 findings from live data for 3–31 October 2026:

| Check | Result |
|---|---|
| Unique WINDFOR `startTime` spacing | 60 minutes in 100% of gaps; all on `:00` |
| WINDFOR `settlementPeriod` | Absent from the raw dataset. The latest endpoint labels hour `T` with the odd settlement period starting at `T` (e.g. 00:00 → period 1). |
| FUELHH | 30-minute periods of average MW; 2026-10-25 has **50** periods (clocks go back) |
| Official definition | Elexon calls WINDFOR a forecast for all Power Park Modules, with values "for each hour". It does **not** say whether a value is the hourly mean or an on-the-hour figure. |
| Empirical test | Compared with the hourly mean, the half-hour at `T`, the half-hour before `T` and `T+30`, MAE differs by **< 2%** and correlation ranges 0.919–0.925. The data cannot tell them apart. |

**Approach:**
- **Forecast resolution is detected** from the stored data on every analysis. 30-minute forecasts
  match directly. Irregular or unsupported spacing → `alignment.status = "unresolved"` and **no
  metrics are produced**.
- **Hourly forecasts** use `FORECAST_ALIGNMENT_METHOD`:
  - `hourly_mean` (default): actual = mean of the two half-hours starting at `T` and `T+30`. MW is
    power, so the two values are averaged, never summed. A match is created only when **both**
    half-hours exist.
  - `start_period`: actual = the half-hour starting at `T` (Elexon's own labelling).
- Because the hourly semantics are not documented, every accuracy response returns
  `alignment.status = "inferred"` with a warning that says so. Nothing is aligned silently.

## 7. Day-ahead decision cutoff

For delivery date **D** (a Europe/London calendar date), only forecasts with
`publishTime ≤ DECISION_CUTOFF_TIME on D-1 in DECISION_CUTOFF_TIMEZONE` are eligible. The latest
eligible one is used. Later publications are **never** used, because they would leak future
information into the backtest.

- **No default.** The market deadline is not invented. If `DECISION_CUTOFF_TIME` is unset and the
  request has no `decision_cutoff=HH:MM`, the accuracy endpoints return **422**. The dashboard still
  returns the forecast, with `historical_accuracy: null` and a warning.
- **DST-safe.** Cutoffs are computed with `zoneinfo` for each delivery date. A test checks every hour
  across both 2026 clock changes.
- **Configurable lookback.** `FORECAST_PUBLICATION_LOOKBACK_DAYS` (default 7) controls how far before
  D-1 publications are ingested, so the forecast that was current at the cutoff is always stored.

## 8. Rolling 28-day window

```python
end = datetime.now(timezone.utc); start = end - timedelta(days=28)   # [start, end)
```

The window is recomputed on every request, so it moves forward automatically with no
month-specific code. Override it with `window_days`, or with `start_date`/`end_date`. Those are
Europe/London dates, inclusive, from local midnight to local midnight.

FUELHH lags real time. The response therefore separates the **requested** window (`analysis_window`)
from the data it actually has (`coverage.effective_start/end`, `latest_actual_start_time`,
`actual_data_complete`), and warns when the last hours have no actuals yet.

## 9. Weekly buckets

The window is split into consecutive 7-day buckets starting at the window start (`[start + 7k days,
start + 7(k+1) days)`). A 28-day window always produces **exactly four**, with no ISO calendar-week
fragments. Each bucket reports `period_start`, `period_end`, MAE, RMSE, bias, WAPE and
`sample_count`. `granularity=daily` gives one bucket per local delivery date instead; DST days
contain 23 or 25 hourly samples.

## 10. Metric definitions

With `e = forecast − actual` over matched observations:

| Metric | Formula | Meaning |
|---|---|---|
| MAE | `mean(abs(e))` | Typical size of the error in MW |
| RMSE | `sqrt(mean(e²))` | Like MAE, but penalises large misses more |
| Bias | `mean(e)` | Positive = wind has been overforecast, negative = underforecast |
| WAPE | `sum(abs(e)) / sum(actual) × 100` | Percentage error weighted by volume. `null` when `sum(actual) = 0`. Used instead of MAPE, which breaks down near zero output. |

## 11. Empirical historical error range

P10, P50 and P90 of `e` are calculated over the matched observations. For a new forecast `F`:

```
estimated_actual_lower = F − P90(e)
estimated_actual_upper = F − P10(e)        (both floored at 0 MW)
```

This needs at least `MIN_SAMPLES_FOR_ERROR_RANGE` matched observations (default 30). Below that,
the values are `null` and an explanation is returned. The range is labelled as an *empirical
historical error range*, not a confidence interval. MAE is not presented as a safety margin, and no
buffer is added to forecasts.

### Ranges by forecast level

Errors grow strongly with the forecast level (section 13), so a single range is too wide for
low-wind hours and too narrow for high-wind ones. The backend therefore also returns
`error_range_by_level`:

1. Matched observations are split into `ERROR_RANGE_LEVEL_BANDS` (default 5) bands of roughly equal
   size, using quantiles of the **forecast** value. The band edges are returned as
   `forecast_min_mw` (inclusive) and `forecast_max_mw` (exclusive); `null` means open-ended.
2. P10, P50 and P90 are computed within each band, with the same minimum-sample rule. If the data
   cannot fill at least two bands of `MIN_SAMPLES_FOR_ERROR_RANGE`, the band count shrinks, and
   below two bands it is reported as unavailable with an explanation.
3. On the dashboard, each forecast item gets `estimated_actual_range_by_level`: `F − P90` to `F − P10`
   of the band that `F` falls in, together with the band used and its sample count.

The overall range stays as the spec defines it (`error_range`, `estimated_actual_range`). The
level-based range is an addition, not a replacement.

Example from 31 October 2026, day-ahead forecasts at a 09:00 cutoff:

| Forecast band (MW) | n | P10 | P50 | P90 |
|---|---:|---:|---:|---:|
| < 7,987 | 134 | 78 | 1,021 | 2,381 |
| 7,987 – 10,706 | 134 | −575 | 483 | 3,511 |
| 10,706 – 14,211 | 134 | 177 | 2,087 | 4,852 |
| 14,211 – 18,421 | 134 | 754 | 3,190 | 5,322 |
| ≥ 18,421 | 135 | 3,170 | 4,916 | 7,830 |
| *All levels* | 671 | 27 | 2,384 | 5,946 |

For the 3,200 MW forecast at 00:00 on 1 November, the overall range is 0–3,173 MW, with the lower
bound clipped at zero. The level-based range is 819–3,122 MW.

## 12. API endpoints

Interactive docs are at `/docs`. All timestamps are ISO 8601 UTC (`Z`).

| Endpoint | Purpose |
|---|---|
| `GET /health` | `{"status":"healthy"}` |
| `POST /api/v1/data/ingest` | Fetch and store data. Optional JSON body: `start_date`, `end_date`, `datasets` (`["FUELHH","WINDFOR"]`). With no dates it covers the rolling window plus the lookback. Idempotent. Returns `status`, `records_fetched/inserted/updated/skipped/invalid`, per-dataset detail, `errors` and `warnings`. Returns 502 if every dataset failed. |
| `GET /api/v1/data/status` | Coverage of stored actuals and forecasts, latest publication and period, missing periods by date, alignment status, readiness for the default window, warnings |
| `GET /api/v1/dashboard` | Forecast for `delivery_date` (default tomorrow) with overall and level-based estimated ranges, plus overall metrics, error ranges (overall and by level), 4 weekly buckets, coverage and warnings. Parameters: `delivery_date`, `as_of`, `window_days`, `start_date`, `end_date`, `decision_cutoff`. With `as_of`, the rolling window also ends at `as_of`. Without `as_of`, once the delivery date's decision cutoff has passed, the forecast is taken as published by the cutoff (`forecast_basis: "decision_cutoff"`, `decision_at`); before that, the live latest forecast is used (`"latest"`). |
| `GET /api/v1/forecasts` | Latest eligible forecast per period for `delivery_date` (default tomorrow); optional `as_of`. Each item has `start_time`, `publish_time`, `generation_mw`; the response lists `selected_publish_times`, completeness and a warning when nothing is found. |
| `GET /api/v1/forecasts/evolution` | Every stored publication for `target_start_time`, sorted by `publish_time` (Graph 5) |
| `GET /api/v1/accuracy/summary` | Metrics, P10/P50/P90 overall and by forecast level, coverage, unmatched counts, alignment, window. `granularity=overall\|weekly\|daily` (default `weekly`); Graph 3 |
| `GET /api/v1/accuracy/series` | Matched observations: `timestamp`, `forecast_mw`, `actual_mw`, `error_mw`, `absolute_error_mw` (Graphs 1 and 2) |
| `GET /api/v1/accuracy/distribution` | Histogram bins (`bins` = target count, default 20, with "nice" widths aligned so 0 is an edge), `sample_count`, `zero_reference_mw`, over/under counts (Graph 4) |

All accuracy endpoints take `window_days` (default 28), `start_date` + `end_date`, and `decision_cutoff`.

Every response also carries metadata:
- `source`, `analysis_window`, `decision_cutoff`, `alignment`, `coverage`, `warnings`.

Errors:
- **422** for invalid parameters, naive timestamps or a missing cutoff.
- **502** when Elexon is unavailable (`/forecasts` falls back to stored data first, with a warning).

**Using it from React (Recharts):**
- Feed `series` straight into `<LineChart data={series}>` with `dataKey="forecast_mw"`/`"actual_mw"`.
- For the error graph, use `<BarChart>` on `error_mw` with `<ReferenceLine y={0}/>`.
- For the weekly graph, use `breakdown` with `mae_mw`/`rmse_mw` bars.
- For the histogram, plot `bins` with `<ReferenceLine x={0}/>`.
- For forecast evolution, plot `series` with `publish_time` on the X axis.
- Convert UTC to UK local time for display.

## 13. Example requests and responses

These are real responses (trimmed) from 31 October 2026 with `DECISION_CUTOFF_TIME=09:00`.

```bash
curl -X POST localhost:8000/api/v1/data/ingest
```
```json
{"status":"success","records_fetched":22785,"records_inserted":22785,"records_updated":0,"records_skipped":0,
 "datasets":[{"dataset":"FUELHH","requested_from":"2026-10-03","requested_to":"2026-10-31","fetched":1372,...},
             {"dataset":"WINDFOR","requested_from":"2026-09-24T23:00:00+00:00",...,"fetched":21413,...}],
 "errors":[],"warnings":[]}
```
Repeating the same request gives `"records_inserted":0,"records_skipped":22785`.

```bash
curl "localhost:8000/api/v1/accuracy/summary"
```
```json
{"analysis_window":{"start":"2026-10-03T12:55:04Z","end":"2026-10-31T12:55:04Z","days":28.0,"mode":"rolling"},
 "decision_cutoff":{"time":"09:00","timezone":"Europe/London","rule":"For delivery date D, the latest forecast published at or before 09:00 Europe/London on D-1 is used."},
 "alignment":{"status":"inferred","forecast_resolution_minutes":60,"method":"hourly_mean",...},
 "coverage":{"expected_actual_periods":1343,"actual_periods_available":1343,"records_matched":671,"records_unmatched":0,
             "latest_actual_start_time":"2026-10-31T12:00:00Z","actual_data_complete":true,...},
 "metrics":{"sample_count":671,"mae_mw":2739.7,"rmse_mw":3465.5,"bias_mw":2596.5,"wape_pct":27.3},
 "error_range":{"available":true,"p10_error_mw":27.0,"p50_error_mw":2384.0,"p90_error_mw":5946.0,...},
 "granularity":"weekly",
 "breakdown":[{"period_start":"2026-10-03T12:55:04Z","period_end":"2026-10-10T12:55:04Z","sample_count":168,"mae_mw":2541.3,...}, ...3 more],
 "warnings":["Elexon documents WINDFOR as hourly values but does not state ...","Actual generation is available up to ..."]}
```

```bash
curl "localhost:8000/api/v1/forecasts?delivery_date=2026-11-01"
```
```json
{"delivery_date":"2026-11-01","day_start":"2026-11-01T00:00:00Z","day_end":"2026-11-02T00:00:00Z",
 "data_source":"elexon_latest_endpoint","available":true,"complete":true,"resolution_minutes":60,"expected_periods":24,
 "selected_publish_times":["2026-10-31T11:30:00Z"],
 "items":[{"start_time":"2026-11-01T00:00:00Z","publish_time":"2026-10-31T11:30:00Z","generation_mw":3200.0,
           "settlement_date":"2026-11-01","settlement_period":1}, ...]}
```

```bash
curl "localhost:8000/api/v1/forecasts/evolution?target_start_time=2026-10-31T12:00:00Z"
```
```json
{"target_start_time":"2026-10-31T12:00:00Z","publication_count":21,
 "series":[{"publish_time":"2026-10-29T02:30:00Z","forecast_mw":2790.0},{"publish_time":"2026-10-29T04:30:00Z","forecast_mw":2907.0}, ...]}
```

Other examples:
- `GET /api/v1/accuracy/distribution?bins=12` → 1,000 MW bins from −3,000 to 9,000 MW; 606 overforecasts and 65 underforecasts.
- `GET /api/v1/accuracy/summary?start_date=2026-10-24&end_date=2026-10-26&granularity=daily` → daily sample counts of 24, 25 and 24 (25 October is the 25-hour day).

### Finding worth surfacing to users

Over this window the day-ahead forecast was on average **~2.6 GW above** metered FUELHH wind (WAPE
≈ 27%, correlation ≈ 0.92). The gap is still there 1–4 hours ahead and grows with wind level: the
median day-ahead error is about +1.0 GW for forecasts under 8 GW and about +4.9 GW above 18.4 GW.
That looks structural, not forecasting skill:
- The likely cause is curtailment and constraint actions, since FUELHH is metered output after curtailment.
- The two datasets may also cover slightly different sets of wind farms.

Read the metrics as "how far WINDFOR sits from metered wind output". Because the error depends on
level, the single P10–P90 range is too wide for low forecasts (the lower bound often floors at 0).
That is why the level-based ranges exist (section 11).

## 14. Run locally

Requires Python 3.12.

```bash
cd backend
pip install -r requirements-dev.txt
cp .env.example .env          # then set DECISION_CUTOFF_TIME, e.g. 09:00
uvicorn app.main:app --reload
curl -X POST localhost:8000/api/v1/data/ingest     # first-time data load (~10-15 s)
```

## 15. Run with Docker Compose

```bash
cd backend
cp .env.example .env          # set DECISION_CUTOFF_TIME
docker compose up --build
```

- API: http://localhost:8000. Swagger docs: http://localhost:8000/docs
- The container runs as a non-root user and has a `/health` healthcheck.
- SQLite lives at `/app/data/app.db` on the named volume `wind-data`, so it survives
  `docker compose down`/`up` and restarts. `docker compose down -v` deletes it.
- `env_file` with `required: false` needs Docker Compose v2.24 or later. On older versions, create `.env` first.

## 16. Tests

```bash
cd backend && pytest                               # locally
docker compose --profile test run --rm tests       # in Docker
```

There are 91 tests, all offline. A synthetic Elexon served through `httpx.MockTransport` encodes the
lead time in each forecast value, so the tests can tell which publication was selected.

| File | Covers |
|---|---|
| `test_elexon_client.py` | Endpoint paths and parameters (no publish filters on FUELHH); array/envelope normalisation; unexpected shapes; empty responses; invalid records (missing generation is not 0, naive timestamps); 429 with Retry-After (and its cap); 5xx exponential backoff; timeouts; no retry on 4xx; half-open chunks |
| `test_ingestion.py` | Actual and forecast persistence; idempotency; duplicate prevention; actual revisions (newer wins, stale ignored); forecasts never overwritten; lookback range; DST day with 50 periods; partial and failed upstream; window filtering |
| `test_temporal_alignment.py` | Resolution detection (30/60/unsupported/irregular); hourly mean (not sum); missing half-hours; start-period mode; direct half-hourly matching; DST days with 46/48/50 periods; UTC uniqueness across the repeated hour |
| `test_forecast_selection.py` | Latest eligible before cutoff; post-cutoff excluded; publication exactly at cutoff; no eligible forecast; inputs preserved; DST cutoffs checked hour by hour; separate cutoff timezone; `as_of` selection |
| `test_accuracy.py` | Level-based ranges (conditioning, open-ended bands, lookup consistent with binning, sparse data); hand-calculated MAE/RMSE/bias/WAPE (`[100,200,300]` vs `[110,180,300]`); zero actuals; empty and single samples; WAPE with zero denominator; minimum samples; quantiles and actual range; histogram zero edge; exact 28-day window and rolling; four buckets; DST daily buckets; late and incomplete data; empty database |
| `test_api.py` | Every endpoint and its response model; status codes; invalid parameters; missing cutoff (422, and the dashboard degrades); empty database; incomplete data; Elexon failure (502, fallback, dashboard keeps working) |

The suite was also checked against the live Elexon API: ingestion, idempotent re-ingestion, every
endpoint, and data persisting across a process restart.

## 17. Limitations and assumptions

- **Hourly alignment is inferred** (section 6): `hourly_mean` is the default; the alternative is configurable.
- **Level-based ranges condition only on forecast level**, not on lead time, season or weather
  regime. With 28 days and 5 bands, each band holds about 134 hourly samples drawn from a few weather
  episodes, and consecutive hours are correlated, so treat them as indicative. Band edges move as
  the window rolls.
- **The cutoff is a fixed wall-clock time on D-1**, the same for every day in a window. It is not a
  market rule; the user configures it.
- **The upcoming forecast** comes from the live latest endpoint and falls back to stored
  publications. Data can only be as fresh as the last ingestion when `as_of` is used.
- **No scheduler.** Run `POST /api/v1/data/ingest` from cron or a CI job to keep data fresh. Dashboard
  requests deliberately do not trigger ingestion.
- **Databases.** SQLite by default; set `DATABASE_URL` to a Postgres URL (as the Vercel deployment does) and the same code runs there. Upserts pick the right SQL dialect, and the test suite passes on both (`TEST_DATABASE_URL=postgresql://... pytest`). See the root README for deployment.
- **Wind only, GB national level.** No embedded or regional split, no demand data, no procurement volumes.
- **Docker was not run on the development machine** (Docker isn't installed there). The image and
  Compose files follow standard practice, but `docker compose up --build` should be smoke-tested once.

## 18. Data attribution and references

The data comes from **Elexon Insights Solution (BMRS)**, which is publicly available and needs no API key.
- Developer portal: https://developer.data.elexon.co.uk/
- API documentation: https://bmrs.elexon.co.uk/api-documentation
- OpenAPI specification: https://data.elexon.co.uk/swagger/v1/swagger.json
- FUELHH: https://bmrs.elexon.co.uk/api-documentation/endpoint/datasets/FUELHH
- WINDFOR: https://bmrs.elexon.co.uk/api-documentation/endpoint/datasets/WINDFOR
- Latest wind forecast: https://bmrs.elexon.co.uk/api-documentation/endpoint/forecast/generation/wind/latest/stream
- WINDFOR glossary definition: https://www.elexon.co.uk/bsc/glossary/wind-generation-forecast/

Check Elexon's terms of use before redistributing the data.

## 19. AI tools used

This backend was designed, implemented, tested and documented with **Claude Code** (Anthropic's
agentic coding tool, model Claude Opus 5.5), working from the requirements specification. Claude:
- inspected the OpenAPI spec and live Elexon responses,
- ran the alignment experiments in section 6,
- wrote the code and tests and fixed the failures it found, including two DST date-arithmetic bugs
  (decision cutoffs and daily buckets).

A human engineer should review the code before production use.
