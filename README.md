# GB Wind Forecast Accuracy

A web app for a UK energy buyer who purchases power on the day-ahead market. It shows:

1. **The wind forecast available when the purchase decision is made.** That is the Elexon WINDFOR
   forecast for the delivery day, as published by the decision cutoff on the day before, with the
   range actual generation has typically landed in.
2. **How accurate those forecasts have been,** measured against Elexon FUELHH actual wind generation.
   By default this covers purchase decisions made in January 2026 (deliveries 2 Jan – 1 Feb 2026).

It covers national GB wind only, using Elexon data only. It does not calculate procurement volumes,
because that needs demand data, which is out of scope.

| Part | Stack | Docs |
|---|---|---|
| `backend/` | Python 3.12, FastAPI, SQLAlchemy (SQLite locally, Postgres when hosted), pandas | [backend/README.md](backend/README.md) |
| `frontend/` | React, TypeScript, Vite, Recharts | [frontend/README.md](frontend/README.md) |
| `api/index.py`, `vercel.json` | Vercel deployment: FastAPI as a Python function | below |
| `design-system/` | Original design rules from the UI UX Pro Max skill; colours and type now follow the REint AI site (see frontend README) | |

## Architecture overview

```
Browser (React + Recharts)
   │  GET /api/v1/...   (same domain; JSON, timestamps in UTC)
   ▼
FastAPI  ──  routes (api/v1/endpoints)    thin: parse query params, return Pydantic models
   │
   ├── services                           all logic and orchestration
   │     ingestion_service                fetch from Elexon and store
   │     forecast_selection_service       latest publication at or before the cutoff
   │     temporal_alignment_service       hourly forecast vs half-hourly actuals
   │     accuracy_service                 match, errors, metrics, ranges (single analyse() path)
   │     dashboard_service                assembles the delivery-day view
   │
   ├── repositories (db/)  ── SQLAlchemy ──  SQLite locally / Neon Postgres on Vercel
   └── elexon_client       ── httpx ──────  Elexon Insights API (public, no key)
```

**Layers.** Routes only read the request and shape the response. Services hold the logic and are
plain Python, so they can be tested without HTTP. Repositories are the only code that touches the
database, and `clients/elexon_client.py` is the only code that calls Elexon. Everything is wired in
one `Container` (`app/main.py`, `app/api/deps.py`), built on first use so it also works on
serverless platforms that skip startup events.

**Reads and writes are separate.** Bulk data loading happens only on `POST /api/v1/data/ingest`
(the **Fetch** button). Read endpoints work from the database; the only live Elexon call while
reading is one small request for the newest upcoming forecast.

### Data flow

1. **Ingest.** `elexon_client` pulls FUELHH (actual half-hourly wind output) and the raw WINDFOR
   publication history, in chunks, retrying 429/5xx with backoff. Invalid records are counted and
   reported, never stored as 0. All times are stored in UTC.
2. **Store.** Forecasts are insert-only and unique on (publish time, target hour), so every
   publication is kept. Actuals are upserted so newer revisions win. Re-running an ingest changes nothing.
3. **Analyse** (`AccuracyService.analyse`, used by every accuracy endpoint and the dashboard):
   - **Select:** for each delivery day D, take the latest forecast published at or before the
     decision cutoff on D-1 (Europe/London, DST-safe). Later revisions are never used, so there is no hindsight.
   - **Align:** an hourly forecast at T is compared with the mean of the half-hours at T and T+30
     (MW is power, so it is averaged, not summed).
   - **Measure:** error = forecast − actual. MAE, RMSE, bias and WAPE, plus empirical P10/P50/P90
     error ranges, split by forecast level because errors grow with wind. The day total uses
     P10/P90 of the actual ÷ forecast day-energy ratio.
4. **Serve.** Pydantic schemas (`app/schemas/`) define every response. Each response also carries
   the analysis window, cutoff, alignment method, coverage and warnings, so the caller can see how
   the numbers were produced.

### Key design decisions

| Decision | Why |
|---|---|
| Raw WINDFOR history, not the "latest" endpoint | "Latest" keeps only the final revision and would leak hindsight into the backtest |
| Configurable decision cutoff with no default | The buy time is a business choice; accuracy endpoints return 422 until one is set |
| Empirical percentiles, not a fitted distribution | Errors are skewed (structural overforecast), so quantiles of real misses are more honest |
| Ranges conditioned on forecast level | One global range is too wide on calm days and too narrow on windy ones |
| Ratio-based day-total range | Daily misses scale with the size of the day; MWh differences gave ranges like 0–111 GWh |
| SQLite locally, Postgres hosted, same code | Zero setup for development; serverless functions have no lasting disk |
| No task queue, ML model or frontend state library | Ingest is explicit and idempotent, the brief is to measure the forecast rather than replace it, and filters live in the URL |

### Frontend

A single page (`frontend/src/App.tsx`). `api/client.ts` is the only code that calls the backend,
using relative `/api/...` URLs (proxied to port 8000 by Vite locally, same domain on Vercel).
`hooks/useUrlState.ts` keeps every filter in the URL so views can be shared, and
`components/ChartCard.tsx` gives every chart the same loading, error, empty and table states.
Libraries: React 19, TypeScript, Vite, Recharts, Inter and Phosphor icons. There is no UI framework;
styling uses CSS design tokens (`styles/tokens.css`).

### Testing

98 backend tests (pytest) run offline against a fake Elexon built on `httpx.MockTransport`. The fake
encodes each forecast's lead time in its value, so tests can assert exactly which publication was
selected. They cover the client (retries, chunking, bad records), ingestion and idempotency, hourly
alignment on 23/24/25-hour days, cutoff selection across clock changes, hand-calculated metrics and
ranges, and every API endpoint. The frontend is checked with `npm run lint` and `npm run build`
(type-check and bundle); it has no automated tests yet.

## Run locally

- **Backend:** see `backend/README.md`. Or with Docker: `cd backend && docker compose up --build`.
- **Frontend:** `cd frontend && npm install && npm run dev`, then open http://localhost:5173.

## Deploy to Vercel (free)

Everything runs as one Vercel project:
- The React build is served as static files.
- The FastAPI backend runs as a **Python function** (`api/index.py`). `vercel.json` routes
  `/api/*` and `/health` to it, so the browser talks to a single domain and needs no CORS setup.
- Data is stored in a free **Neon Postgres** database. Serverless functions keep nothing on disk
  between requests, so SQLite cannot be used there.

### 1. Put the code on GitHub

```bash
git init
git add .
git commit -m "Wind forecast accuracy app"
git branch -M main
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```

`.gitignore` already excludes local-only files (`backend/.deps`, `node_modules`, databases, `.env`).

### 2. Create the Vercel project

1. Go to https://vercel.com/new and import the repository.
2. **Root Directory:** leave it as the repository root (`./`). `vercel.json` provides the build
   command (`npm --prefix frontend run build`) and the output folder (`frontend/dist`).
3. Don't deploy yet; first add the database and settings below. If it has already deployed, just
   redeploy after step 4.

### 3. Add a free Postgres database

1. In the project, open **Storage → Create Database → Neon (Serverless Postgres)** and choose the free plan.
2. Connect it to the project. This adds connection variables to the project.
3. Under **Settings → Environment Variables**, check that **`DATABASE_URL`** exists. If you only see
   `POSTGRES_URL`, add `DATABASE_URL` with the same value. The backend accepts `postgres://` and
   `postgresql://` URLs as given.

Tables are created automatically on first use.

### 4. Set the decision cutoff

Under **Settings → Environment Variables**, add:

| Name | Value | Notes |
|---|---|---|
| `DECISION_CUTOFF_TIME` | `09:00` | Wall-clock time on D-1 (UK) by which forecasts must be published. A business choice, not a market rule |

Optional: `DECISION_CUTOFF_TIMEZONE` (default `Europe/London`), `LOG_LEVEL` (default `INFO`).

### 5. Deploy and load data

1. Deploy (or redeploy).
2. Open `https://<project>.vercel.app/health`. It should return `{"status":"healthy"}`.
3. Open `https://<project>.vercel.app` and click **Fetch January 2026 from Elexon**. This takes
   about 15–30 seconds; it took 13 seconds locally against Postgres. The data then stays in Neon.

### Two Vercel gotchas (already handled in this repo)

- **`"framework": null` in `vercel.json` is required.** Without it, Vercel sees Python and treats the
  project as a "Python backend app". The npm `installCommand` then replaces the Python install
  (`No module named 'fastapi'`) and every route, including `/`, goes to the function.
- **The root `requirements.txt` lists packages explicitly.** Vercel's parser cannot follow
  `-r backend/requirements.txt`, so keep both lists in sync.

### Deploying from the terminal

```bash
npx vercel login
npx vercel deploy --prod --yes
```

Run these from the repository root. `npx vercel integration add neon` adds the database from the CLI.

### Notes and limits

- **Cold starts:** the first request after a quiet period takes a few seconds while the function
  starts and loads pandas.
- **Time limit:** functions are capped at 60 seconds (`maxDuration` in `vercel.json`). One
  January fetch fits easily. Very long custom ranges should be fetched in parts.
- **Without a database:** the app still starts, using a temporary SQLite file in `/tmp`, but data
  disappears between requests. Attach Neon before relying on it.
- **Free plans:** Vercel Hobby and the Neon free tier are enough for this app; a month of data is
  only about 25,000 rows. Check the providers' current free-tier limits, which change over time.
- **Docker:** used for local runs (`backend/Dockerfile`, `docker-compose.yml`). The Vercel deployment
  does not use it, because Vercel runs Python functions natively.

### How the deployment was tested

- The full backend suite (98 tests) passes on SQLite **and on a real Postgres** server
  (`TEST_DATABASE_URL=postgresql://... pytest`).
- `api/index.py` was served locally against Postgres: `/health` and `/api/v1/*` respond, and a live
  January 2026 fetch took 12.9 seconds with metrics matching SQLite.
- The backend works even when the platform skips ASGI startup events (it builds its services on
  first use), and it opens HTTP clients per request so nothing outlives an event loop.
- Deployed to https://wind-gamma-seven.vercel.app: `/health`, the dashboard and the API respond. A live January 2026 fetch into Neon took 7.3 seconds, with metrics identical to the local run (744/744 hours, MAE 2,039 MW).

## AI tools
I used **Claude Code** (Anthropic’s agentic coding tool, powered by Haiku) during the development of this project. The frontend design follows the **UI UX Pro Max**.
