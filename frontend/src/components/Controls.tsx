import { useId } from 'react'
import type { Filters } from '../hooks/useUrlState'
import { addDays, ukToday } from '../lib/format'
import { JANUARY_2026, periodMode } from '../lib/period'

interface ControlsProps {
  filters: Filters
  onChange: (patch: Partial<Filters>) => void
  serverCutoff: boolean
}

const WINDOW_OPTIONS = [7, 14, 28, 56]

export function Controls({ filters, onChange, serverCutoff }: ControlsProps) {
  const ids = {
    days: useId(),
    start: useId(),
    end: useId(),
    cutoff: useId(),
    cutoffHelp: useId(),
    delivery: useId(),
    periodHelp: useId(),
  }
  const mode = periodMode(filters)
  const today = ukToday()

  return (
    <form className="controls" aria-label="Analysis settings" onSubmit={(e) => e.preventDefault()}>
      <fieldset className="controls__group">
        <legend>Delivery day</legend>
        <label htmlFor={ids.delivery}>
          Delivery date
          <input
            id={ids.delivery}
            type="date"
            value={filters.delivery_date || addDays(today, 1)}
            onChange={(e) => onChange({ delivery_date: e.target.value === addDays(today, 1) ? '' : e.target.value })}
          />
        </label>
      </fieldset>
      <fieldset className="controls__group">
        <legend>Decision cutoff</legend>
        <label htmlFor={ids.cutoff}>
          Published by (UK time, day before delivery)
          <input
            id={ids.cutoff}
            type="time"
            step={60}
            value={filters.decision_cutoff}
            onChange={(e) => onChange({ decision_cutoff: e.target.value })}
            aria-describedby={ids.cutoffHelp}
          />
        </label>
        <p id={ids.cutoffHelp} className="helper">
          {filters.decision_cutoff
            ? 'Only forecasts published by this time on D-1 count in the backtest.'
            : serverCutoff
              ? 'Using the server’s configured cutoff.'
              : 'Required: no cutoff is configured on the server.'}
        </p>
      </fieldset>

      <fieldset className="controls__group">
        <legend>Accuracy period</legend>
        <div className="segmented" role="radiogroup" aria-label="Accuracy period" aria-describedby={ids.periodHelp}>
          <button
            type="button"
            role="radio"
            aria-checked={mode === 'january'}
            className="segmented__option"
            onClick={() => onChange({ period: '', start_date: '', end_date: '', window_days: '' })}
          >
            January 2026
          </button>
          <button
            type="button"
            role="radio"
            aria-checked={mode === 'rolling'}
            className="segmented__option"
            onClick={() => onChange({ period: 'rolling', start_date: '', end_date: '' })}
          >
            Rolling
          </button>
          <button
            type="button"
            role="radio"
            aria-checked={mode === 'custom'}
            className="segmented__option"
            onClick={() =>
              onChange({ period: 'custom', window_days: '', start_date: JANUARY_2026.start_date, end_date: JANUARY_2026.end_date })
            }
          >
            Custom
          </button>
        </div>
        {mode === 'january' ? (
          <p id={ids.periodHelp} className="helper">
            Purchase decisions made 1–31 Jan 2026, i.e. delivery days 2 Jan – 1 Feb 2026.
          </p>
        ) : mode === 'custom' ? (
          <div className="controls__row">
            <label htmlFor={ids.start}>
              Delivery from
              <input
                id={ids.start}
                type="date"
                value={filters.start_date}
                max={filters.end_date || today}
                onChange={(e) => onChange({ start_date: e.target.value })}
              />
            </label>
            <label htmlFor={ids.end}>
              Delivery to
              <input
                id={ids.end}
                type="date"
                value={filters.end_date}
                min={filters.start_date}
                max={today}
                onChange={(e) => onChange({ end_date: e.target.value })}
              />
            </label>
          </div>
        ) : (
          <label htmlFor={ids.days}>
            Last
            <select
              id={ids.days}
              value={filters.window_days || '28'}
              onChange={(e) => onChange({ window_days: e.target.value === '28' ? '' : e.target.value })}
            >
              {WINDOW_OPTIONS.map((d) => (
                <option key={d} value={d}>
                  {d} days
                </option>
              ))}
            </select>
          </label>
        )}
      </fieldset>

    </form>
  )
}
