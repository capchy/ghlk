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
| `design-system/` | Design rules generated with the UI UX Pro Max skill | |

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

- The full backend suite (94 tests) passes on SQLite **and on a real Postgres** server
  (`TEST_DATABASE_URL=postgresql://... pytest`).
- `api/index.py` was served locally against Postgres: `/health` and `/api/v1/*` respond, and a live
  January 2026 fetch took 12.9 seconds with metrics matching SQLite.
- The backend works even when the platform skips ASGI startup events (it builds its services on
  first use), and it opens HTTP clients per request so nothing outlives an event loop.
- An actual Vercel deployment has not been run from here, because it needs your accounts.

## AI tools

Built with **Claude Code** (Anthropic's agentic coding tool, model Claude Opus 5.5): design,
implementation, tests, documentation and deployment configuration. The frontend design follows the
**UI UX Pro Max** skill. A human engineer should review the work before production use.
