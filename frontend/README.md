# GB Wind Forecast Accuracy — Dashboard

A React + TypeScript dashboard for the FastAPI backend in `../backend`. It shows the upcoming wind
forecast with an empirical uncertainty band, plus how accurate the day-ahead forecasts were over a
rolling window. All charts use **Recharts**.

> Wind generation forecast and historical accuracy only. No demand data, and no procurement
> volumes or recommendations.

## Run it

You need Node 20 or later and the backend running on port 8000 (see `../backend/README.md`).

```bash
npm install
npm run dev        # http://localhost:5173
```

The Vite dev server proxies `/api` and `/health` to `http://localhost:8000`, so no CORS setup is
needed. Set `VITE_PROXY_TARGET` in `.env` to use a different backend.

On first use, click **Fetch data from Elexon** (or the button in an empty chart). It calls
`POST /api/v1/data/ingest` for the selected accuracy period and takes about 15–30 seconds. If the
backend has no `DECISION_CUTOFF_TIME`, enter a cutoff in the controls; the accuracy charts wait for one.

```bash
npm run build      # type-check + production bundle in dist/
npm run lint
```

For a production build served from a different origin than the API, set `VITE_API_BASE_URL`
(e.g. `https://api.example.com`) and add that origin to the backend's `CORS_ORIGINS`.

## What's on the page

The page shows only what the brief's two purposes need.

**1. The forecast available when the purchase decision is made**

| Section | Endpoint | Notes |
|---|---|---|
| Wind forecast for the delivery day | `GET /api/v1/dashboard` | Hourly forecast (dashed) with the likely actual range (P10–P90 of past errors at a similar forecast level), plus day totals: average, lowest, peak, and day energy with its likely range in brackets, e.g. `279 GWh (212–280 GWh)`: the day's forecast total scaled by how actual day energy compared with the forecast on past days (backend README section 11). Once the D-1 cutoff has passed, it shows the forecast **as published by the cutoff**, not later revisions the buyer couldn't have used (`forecast_basis`) |

**2. Quantified forecast error**

| Section | Endpoint | Notes |
|---|---|---|
| Accuracy KPIs | `GET /api/v1/dashboard` | Typical miss (MAE), bias, P10–P90 error range, relative error (WAPE), with the period and matched-hours coverage |

**Accuracy period.** The default is **January 2026**, as the brief requires: purchase decisions made
1–31 January 2026, which are deliveries on **2 January – 1 February 2026** (decisions are made on D-1).
**Rolling** (last 7–56 days) and **Custom** delivery dates are also available.

January 2026 measured on live Elexon data with a 09:00 cutoff: 744 of 744 hours matched, MAE 2,039 MW,
bias +1,748 MW, P10–P90 error −549 to +4,098 MW, WAPE 18.1%.

To see the forecast a buyer had for a January delivery day, pick that date. The card shows the
publication available at that day's cutoff; for 15 Jan, that is the 14 Jan 07:30 publication.
| Forecast vs actual | `GET /api/v1/accuracy/series` | The record behind the numbers. Actual solid, forecast dashed |
| Error distribution | `GET /api/v1/accuracy/distribution` | How often and by how much forecasts missed; bars coloured by direction |

Removed because they serve neither purpose (the backend endpoints remain):
- **Forecast evolution:** how one hour's forecast changed. Interesting, but not needed to decide or to size the error.
- **Error over time:** repeated what forecast vs actual already shows.
- **Weekly buckets.**
- **RMSE and matched-hours tiles:** coverage moved into the section's text.
- **Overall/by-level range toggle:** by-level is shown, with overall as the fallback.

The frontend types (`src/api/types.ts`) also list only the fields the page reads.

Data-quality warnings from the API appear in a collapsible panel. Times are shown in UK local time;
the API returns UTC.

Filters live in the URL (`delivery_date`, `decision_cutoff`, `period`, `window_days`, `start_date`,
`end_date`), so any view can be shared or bookmarked.

## Design

The layout and accessibility rules come from the **UI UX Pro Max** skill (`../.claude/skills/ui-ux-pro-max`;
original output in `../design-system/wind-forecast-accuracy/MASTER.md`). The visual style was then
matched to the **REint AI** website:

- **Style:** brand green (`#1e8a3c`) for actions and the active state, charcoal text, warm off-white panels, hairline borders, near-square corners, no shadows. Inter for all text and numbers (tabular figures). Small uppercase letter-spaced labels, and underlined tabs for the accuracy period.
- **Charts:** charcoal dashed forecast line, green actuals, soft red likely-range band, as on the REint demand chart.
- **Theme:** light only, from the tokens in `src/styles/tokens.css`. It stays light even when the OS is set to dark mode, and there is no theme toggle.
- **Charts** (from the skill's chart guidance):
  - Forecast vs actual uses solid vs dashed lines, so series don't depend on colour.
  - Uncertainty is a line plus a confidence band.
  - Every chart has a visible legend, a tooltip, a one-sentence text summary and a **Table** toggle with a sortable data table.
  - Animations are off under `prefers-reduced-motion`.
- **States:** skeletons while loading. Errors are announced (`role="alert"`) with a Retry button. Empty states explain what to do (fetch data, set a cutoff).
- **Accessibility:** skip link, visible 3px focus rings, controls at least 44px, labelled inputs, icon buttons with `aria-label`, Phosphor SVG icons (no emoji), tabular figures, and no horizontal scroll at 375px.

The skill recommends a box plot for distributions. The spec asked for a histogram, so the
histogram stays, with P10/P50/P90 given in its summary.

## Structure

```
src/
  api/        client.ts (fetch wrapper, ApiError), types.ts (response shapes)
  hooks/      useApi (abortable fetch), useUrlState (filters in URL), useChartPalette (chart colours from the tokens)
  charts/     one component per chart, plus common axis and reduced-motion helpers
  components/ ChartCard (states and table toggle), DataTable, KpiCard, Controls, Header, Legend, Toast, Warnings
  lib/        format.ts (UK time, MW/GW formatting)
  styles/     tokens.css (design tokens), app.css
```

## Verified

Checked in a browser against the live backend:
- Every chart renders real data.
- Cutoff entry and the table view work; once the cutoff has passed, the delivery-day forecast switches to the publication available at the cutoff.
- The page stays light with the OS set to dark mode; the header has only the fetch button.
- 375px mobile layout has no horizontal overflow.
- Keyboard focus is visible.
- With the backend stopped, every card shows "Cannot reach the backend" with Retry; one Retry recovers the page once it is back.

`npm run lint` and `npm run build` (type-check and bundle) pass cleanly. Recharts gets its own
cached chunk.

There are no automated frontend tests yet.
