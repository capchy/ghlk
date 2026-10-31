import { ArrowsClockwise, Wind } from '@phosphor-icons/react'
import type { DataStatus } from '../api/types'
import { fmtDateTime } from '../lib/format'

interface HeaderProps {
  status: DataStatus | null
  onIngest: () => void
  ingesting: boolean
}

export function Header({ status, onIngest, ingesting }: HeaderProps) {
  const actualsTo = status?.actual.latest_generation_period_end
  const forecastAt = status?.forecast.latest_publish_time
  return (
    <header className="app-header">
      <div className="app-header__brand">
        <span className="app-header__logo" aria-hidden="true">
          <Wind size={22} weight="bold" />
        </span>
        <div>
          <h1 className="app-header__title">GB Wind Forecast Accuracy</h1>
          <p className="app-header__meta">
            {status ? (
              <>
                Actuals to <strong>{actualsTo ? fmtDateTime(actualsTo) : 'none yet'}</strong>
                <span aria-hidden="true"> · </span>
                Latest forecast <strong>{forecastAt ? fmtDateTime(forecastAt) : 'none yet'}</strong>
                <span className="muted"> (UK time)</span>
              </>
            ) : (
              'Elexon WINDFOR forecasts vs FUELHH actual wind generation'
            )}
          </p>
        </div>
      </div>
      <div className="app-header__actions">
        <button type="button" className="btn btn--primary" onClick={onIngest} disabled={ingesting}>
          <ArrowsClockwise size={18} aria-hidden="true" className={ingesting ? 'spin' : undefined} />
          {ingesting ? 'Fetching from Elexon…' : 'Fetch data from Elexon'}
        </button>
      </div>
    </header>
  )
}
