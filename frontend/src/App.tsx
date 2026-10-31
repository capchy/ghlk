import { useCallback, useState } from 'react'
import { Gauge, Scales, Target, TrendUp } from '@phosphor-icons/react'
import { request } from './api/client'
import type { AccuracySeries, Dashboard, DataStatus, Distribution, Forecast, IngestResult } from './api/types'
import { binSide, upcomingRows } from './charts/data'
import { ErrorDistributionChart } from './charts/ErrorDistributionChart'
import { ForecastVsActualChart } from './charts/ForecastVsActualChart'
import { UpcomingForecastChart } from './charts/UpcomingForecastChart'
import { ChartCard } from './components/ChartCard'
import { Controls } from './components/Controls'
import { DataTable } from './components/DataTable'
import { Header } from './components/Header'
import { KpiCard } from './components/KpiCard'
import { Legend } from './components/Legend'
import { Toast, type ToastMessage } from './components/Toast'
import { Warnings } from './components/Warnings'
import { useApi } from './hooks/useApi'
import { useChartPalette } from './hooks/useChartPalette'
import { useUrlState } from './hooks/useUrlState'
import { ingestBody, periodMode, periodParams } from './lib/period'
import { fmtDateLong, fmtDateTime, fmtGw, fmtMw, fmtNum, fmtPct, fmtSignedMw, fmtTime } from './lib/format'

/** Day totals a buyer needs alongside the hourly profile. */
function forecastStats(forecast: Forecast) {
  const values = forecast.items.map((i) => i.generation_mw)
  const peak = forecast.items.reduce((m, i) => (i.generation_mw > m.generation_mw ? i : m))
  const low = forecast.items.reduce((m, i) => (i.generation_mw < m.generation_mw ? i : m))
  const hours = (forecast.resolution_minutes ?? 60) / 60
  return {
    average: values.reduce((s, v) => s + v, 0) / values.length,
    peak,
    low,
    energyMwh: values.reduce((s, v) => s + v * hours, 0),
  }
}

export default function App() {
  const palette = useChartPalette()
  const [filters, setFilters] = useUrlState()
  const [refreshKey, setRefreshKey] = useState(0)
  const [ingesting, setIngesting] = useState(false)
  const [toast, setToast] = useState<ToastMessage | null>(null)

  const status = useApi<DataStatus>('/api/v1/data/status', {}, refreshKey)
  const serverCutoff = status.data?.default_window.decision_cutoff_configured ?? false
  const cutoffKnown = Boolean(filters.decision_cutoff) || serverCutoff
  const hasData = (status.data?.actual.record_count ?? 0) > 0

  const windowParams = { ...periodParams(filters), decision_cutoff: filters.decision_cutoff }
  // Accuracy endpoints need a cutoff (422 otherwise), so wait until one is known.
  const ready = status.data !== null && cutoffKnown && hasData
  const dashboard = useApi<Dashboard>(
    status.data ? '/api/v1/dashboard' : null,
    { ...windowParams, delivery_date: filters.delivery_date },
    refreshKey,
  )
  const series = useApi<AccuracySeries>(ready ? '/api/v1/accuracy/series' : null, windowParams, refreshKey)
  const dist = useApi<Distribution>(ready ? '/api/v1/accuracy/distribution' : null, { ...windowParams, bins: 24 }, refreshKey)

  const ingest = useCallback(async () => {
    setIngesting(true)
    try {
      // Fetch what the selected period needs; tomorrow's forecast comes live from Elexon anyway.
      const result = await request<IngestResult>('/api/v1/data/ingest', { method: 'POST', body: ingestBody(filters) })
      setToast({
        id: Date.now(),
        tone: result.status === 'success' ? 'success' : 'error',
        text:
          result.status === 'success'
            ? `Data updated: ${fmtNum(result.records_inserted)} new, ${fmtNum(result.records_updated)} revised records.`
            : `Partial update: ${result.errors.join('; ')}`,
      })
      setRefreshKey((k) => k + 1)
    } catch (err) {
      setToast({ id: Date.now(), tone: 'error', text: (err as Error).message })
    } finally {
      setIngesting(false)
    }
  }, [filters])
  const dismissToast = useCallback(() => setToast(null), [])

  const hist = dashboard.data?.historical_accuracy ?? null
  const forecast = dashboard.data?.forecast ?? null
  const metrics = hist?.metrics
  const range = hist?.error_range
  const points = series.data?.series ?? []
  const warnings = [...(dashboard.data?.warnings ?? []), ...(status.data?.warnings ?? [])]
  const accuracyLoading = status.loading || dashboard.loading
  // Every other request waits on /data/status, so a status failure must surface in each card.
  const gate = status.error
  const retryWith = (own: () => void) => (gate ? status.reload : own)

  const periodLabel = periodMode(filters) === 'january' ? 'January 2026' : 'this period'
  // No stored data at all, or none for the selected period (e.g. January not fetched yet).
  const periodHasNoData = !hasData || (hist !== null && hist.coverage.records_matched === 0)
  const accuracyEmpty =
    status.data !== null && periodHasNoData && !dashboard.loading
      ? {
          when: true,
          message: `No data stored for ${periodLabel} yet. Fetch it from Elexon to measure accuracy (about 15–30 seconds).`,
          action: (
            <button type="button" className="btn btn--secondary" onClick={ingest} disabled={ingesting}>
              {ingesting ? 'Fetching…' : `Fetch ${periodLabel} from Elexon`}
            </button>
          ),
        }
      : {
          when: status.data !== null && !cutoffKnown,
          message: 'Set a decision cutoff above to measure forecast accuracy. It is a business decision, so there is no default.',
        }

  const upcoming = forecast ? upcomingRows(forecast) : []
  const stats = forecast?.available ? forecastStats(forecast) : null
  const basis = dashboard.data?.forecast_basis
  const decisionAt = dashboard.data?.decision_at
  const forecastSubtitle = !forecast?.latest_publish_time
    ? 'Forecast for the delivery date'
    : basis === 'decision_cutoff' && decisionAt
      ? `As available at the decision cutoff (${fmtDateTime(decisionAt)}) · published ${fmtDateTime(forecast.latest_publish_time)}`
      : decisionAt
        ? `Latest forecast, published ${fmtDateTime(forecast.latest_publish_time)} · decision cutoff ${fmtDateTime(decisionAt)} still ahead`
        : `Latest forecast, published ${fmtDateTime(forecast.latest_publish_time)} · set a decision cutoff to pin it to decision time`

  return (
    <>
      <a href="#main" className="skip-link">
        Skip to dashboard
      </a>
      <Header status={status.data} onIngest={ingest} ingesting={ingesting} />
      <main id="main" className="layout" tabIndex={-1}>
        <Controls filters={filters} onChange={setFilters} serverCutoff={serverCutoff} />
        <Warnings warnings={warnings} />

        {/* Purpose 1: the forecast available when the purchase decision is made. */}
        <ChartCard
          className="span-full"
          title={forecast ? `Wind forecast for ${fmtDateLong(forecast.day_start)}` : 'Wind forecast for delivery'}
          subtitle={forecastSubtitle}
          loading={dashboard.loading || status.loading}
          error={dashboard.error ?? gate}
          onRetry={retryWith(dashboard.reload)}
          empty={{
            when: forecast !== null && !forecast.available,
            message: forecast?.warnings[0] ?? 'No forecast has been published for this date yet.',
          }}
          summary={
            stats
              ? 'The shaded band is the empirical P10–P90 range of past day-ahead errors for forecasts at a similar level: where actual generation has typically landed. It is illustrative, not a guarantee or a purchase recommendation.'
              : undefined
          }
          height={380}
          table={() => (
            <DataTable
              caption="Hourly wind forecast with estimated actual range"
              rowKey={(r) => String(r.t)}
              rows={upcoming}
              columns={[
                { key: 't', header: 'Hour (UK)', render: (r) => fmtTime(new Date(r.t).toISOString()), sortValue: (r) => r.t },
                { key: 'f', header: 'Forecast', numeric: true, render: (r) => fmtMw(r.forecast), sortValue: (r) => r.forecast },
                { key: 'l', header: 'Likely low', numeric: true, render: (r) => fmtMw(r.low) },
                { key: 'h', header: 'Likely high', numeric: true, render: (r) => fmtMw(r.high) },
              ]}
            />
          )}
        >
          {stats && forecast && (
            <dl className="stat-strip">
              <div>
                <dt>Average</dt>
                <dd>{fmtGw(stats.average)}</dd>
              </div>
              <div>
                <dt>Lowest</dt>
                <dd>
                  {fmtGw(stats.low.generation_mw)} <span className="muted">at {fmtTime(stats.low.start_time)}</span>
                </dd>
              </div>
              <div>
                <dt>Peak</dt>
                <dd>
                  {fmtGw(stats.peak.generation_mw)} <span className="muted">at {fmtTime(stats.peak.start_time)}</span>
                </dd>
              </div>
              <div>
                <dt>Day energy</dt>
                <dd>
                  {fmtNum(stats.energyMwh / 1000)} GWh
                  {!forecast.complete && <span className="muted"> (partial day)</span>}
                </dd>
              </div>
            </dl>
          )}
          <Legend
            items={[
              { label: 'Forecast', color: palette.forecast, kind: 'dashed' },
              { label: 'Likely actual range (P10–P90)', color: palette.band, kind: 'area' },
            ]}
          />
          <UpcomingForecastChart rows={upcoming} palette={palette} />
        </ChartCard>

        {/* Purpose 2: quantified forecast error. */}
        <section className="section-head" aria-labelledby="accuracy-heading">
          <h2 id="accuracy-heading">How accurate are these forecasts?</h2>
          <p className="muted">
            {hist && hist.coverage.records_matched === 0
              ? `No matched forecasts and actuals stored for ${fmtDateTime(hist.analysis_window.start)} – ${fmtDateTime(hist.analysis_window.end)} yet.`
              : hist
              ? `Day-ahead forecasts (published by ${hist.decision_cutoff.time} UK on D-1) vs actual generation, ${fmtDateTime(hist.analysis_window.start)} – ${fmtDateTime(hist.analysis_window.end)}. ${fmtNum(hist.coverage.records_matched)} of ${fmtNum(hist.coverage.target_periods)} hours matched${hist.coverage.actual_data_complete ? '.' : `; ${hist.coverage.missing_actual_periods} half-hours of actuals not yet received.`}`
              : 'Error = forecast − actual. Positive means wind was overforecast.'}
          </p>
        </section>

        <section aria-label="Forecast accuracy summary" className="kpis kpis--4">
          <KpiCard
            label="Typical miss (MAE)"
            icon={<Gauge size={18} />}
            loading={accuracyLoading}
            value={fmtMw(metrics?.mae_mw)}
            detail="Mean absolute error per hour"
          />
          <KpiCard
            label="Bias"
            icon={<Scales size={18} />}
            loading={accuracyLoading}
            tone="accent"
            value={fmtSignedMw(metrics?.bias_mw)}
            detail={
              metrics?.bias_mw == null
                ? 'Average of forecast − actual'
                : metrics.bias_mw > 0
                  ? 'Wind overforecast on average'
                  : 'Wind underforecast on average'
            }
          />
          <KpiCard
            label="Error range P10–P90"
            icon={<Target size={18} />}
            loading={accuracyLoading}
            value={
              range?.available
                ? `${fmtSignedMw(range.p10_error_mw).replace(' MW', '')} to ${fmtSignedMw(range.p90_error_mw)}`
                : '—'
            }
            detail={
              range?.available ? `80% of hours; median ${fmtSignedMw(range.p50_error_mw)}` : range?.explanation ?? 'Needs data and a cutoff'
            }
          />
          <KpiCard
            label="Relative error (WAPE)"
            icon={<TrendUp size={18} />}
            loading={accuracyLoading}
            value={fmtPct(metrics?.wape_pct)}
            detail="Total error ÷ total actual generation"
          />
        </section>

        <ChartCard
          className="span-7"
          title="Forecast vs actual"
          subtitle="Hourly day-ahead forecast against actual wind generation"
          loading={series.loading || status.loading}
          error={series.error ?? gate}
          onRetry={retryWith(series.reload)}
          empty={accuracyEmpty}
          summary={
            metrics?.mae_mw != null
              ? `Over ${fmtNum(metrics.sample_count)} hours the forecast missed by ${fmtMw(metrics.mae_mw)} on average and ran ${fmtSignedMw(metrics.bias_mw)} relative to actuals.`
              : undefined
          }
          table={() => (
            <DataTable
              caption="Hourly forecast and actual wind generation"
              rowKey={(p) => p.timestamp}
              rows={points}
              columns={[
                { key: 't', header: 'Hour (UK)', render: (p) => fmtDateTime(p.timestamp), sortValue: (p) => p.timestamp },
                { key: 'f', header: 'Forecast', numeric: true, render: (p) => fmtNum(p.forecast_mw), sortValue: (p) => p.forecast_mw },
                { key: 'a', header: 'Actual', numeric: true, render: (p) => fmtNum(p.actual_mw), sortValue: (p) => p.actual_mw },
                { key: 'e', header: 'Error', numeric: true, render: (p) => fmtSignedMw(p.error_mw), sortValue: (p) => p.error_mw },
              ]}
            />
          )}
          height={300}
        >
          <Legend
            items={[
              { label: 'Actual', color: palette.actual, kind: 'solid' },
              { label: 'Forecast (day-ahead)', color: palette.forecast, kind: 'dashed' },
            ]}
          />
          <ForecastVsActualChart series={points} palette={palette} />
        </ChartCard>

        <ChartCard
          className="span-5"
          title="Error distribution"
          subtitle="Hours per error band (forecast − actual)"
          loading={dist.loading || status.loading}
          error={dist.error ?? gate}
          onRetry={retryWith(dist.reload)}
          empty={accuracyEmpty}
          summary={
            dist.data
              ? `${fmtNum(dist.data.overforecast_count)} hours overforecast, ${fmtNum(dist.data.underforecast_count)} underforecast${range?.available ? `. 80% of errors fell between ${fmtSignedMw(range.p10_error_mw)} and ${fmtSignedMw(range.p90_error_mw)}` : ''}.`
              : undefined
          }
          table={() => (
            <DataTable
              caption="Forecast error histogram"
              rowKey={(b) => String(b.bin_start_mw)}
              rows={dist.data?.bins ?? []}
              columns={[
                { key: 'r', header: 'Error band (MW)', render: (b) => `${fmtNum(b.bin_start_mw)} to ${fmtNum(b.bin_end_mw)}`, sortValue: (b) => b.bin_start_mw },
                { key: 's', header: 'Direction', render: (b) => binSide(b) },
                { key: 'c', header: 'Hours', numeric: true, render: (b) => b.count, sortValue: (b) => b.count },
              ]}
            />
          )}
          height={300}
        >
          <Legend
            items={[
              { label: 'Overforecast', color: palette.forecast, kind: 'bar' },
              { label: 'Underforecast', color: palette.actual, kind: 'bar' },
            ]}
          />
          {dist.data && <ErrorDistributionChart dist={dist.data} palette={palette} />}
        </ChartCard>
      </main>
      <footer className="app-footer">
        <p>
          {dashboard.data?.scope_note ??
            'Wind generation forecast and historical accuracy only. No demand data or procurement volumes are calculated.'}
        </p>
        <p>Data: Elexon Insights Solution (BMRS) — FUELHH actual outturn and WINDFOR wind forecasts. Times shown in UK local time.</p>
      </footer>
      <Toast toast={toast} onDismiss={dismissToast} />
    </>
  )
}
