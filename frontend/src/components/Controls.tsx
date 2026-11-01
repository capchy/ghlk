import { useId } from 'react'
import type { Filters } from '../hooks/useUrlState'
import { addDays, ukToday } from '../lib/format'
import { JANUARY_2026, periodMode } from '../lib/period'

interface ControlsProps {
  filters: Filters
  onChange: (patch: Partial<Filters>) => void
  serverCutoff: boolean
  /** Server DECISION_CUTOFF_TIME (HH:MM), shown in the box until the user overrides it. */
  serverCutoffTime: string | null
}

const WINDOW_OPTIONS = [7, 14, 28, 56]

export function Controls({ filters, onChange, serverCutoff, serverCutoffTime }: ControlsProps) {
  const ids = {
    days: useId(),
    start: useId(),
    end: useId(),
    cutoff: useId(),
    cutoffHelp: useId(),
    delivery: useId(),
    periodHelp: useId(),
    periodLabel: useId(),
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
          Published by (UK time, D-1)
          <input
            id={ids.cutoff}
            type="time"
            step={60}
            value={filters.decision_cutoff || serverCutoffTime || ''}
            onChange={(e) =>
              onChange({ decision_cutoff: e.target.value === serverCutoffTime ? '' : e.target.value })
            }
            aria-describedby={ids.cutoffHelp}
          />
        </label>
        <p id={ids.cutoffHelp} className="helper">
          {filters.decision_cutoff
            ? serverCutoffTime
              ? `Your override; server default is ${serverCutoffTime}.`
              : 'Only forecasts published by this time on the day before delivery count.'
            : serverCutoff
              ? 'Server default. Forecasts published after it on D-1 are ignored.'
              : 'Required: no cutoff is configured on the server.'}
        </p>
      </fieldset>

      <fieldset className="controls__group controls__group--wide">
        <legend>Accuracy period</legend>
        <div className="controls__field">
          <span className="controls__label" id={ids.periodLabel}>
            Delivery days measured
          </span>
          <div className="controls__inline">
            <div
              className="segmented"
              role="radiogroup"
              aria-labelledby={ids.periodLabel}
              aria-describedby={ids.periodHelp}
            >
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
            {mode === 'rolling' && (
              <select
                id={ids.days}
                aria-label="Rolling window length"
                value={filters.window_days || '28'}
                onChange={(e) => onChange({ window_days: e.target.value === '28' ? '' : e.target.value })}
              >
                {WINDOW_OPTIONS.map((d) => (
                  <option key={d} value={d}>
                    Last {d} days
                  </option>
                ))}
              </select>
            )}
            {mode === 'custom' && (
              <span className="controls__dates">
                <input
                  id={ids.start}
                  type="date"
                  aria-label="Delivery from"
                  value={filters.start_date}
                  max={filters.end_date || today}
                  onChange={(e) => onChange({ start_date: e.target.value })}
                />
                <span className="controls__dash" aria-hidden="true">
                  –
                </span>
                <input
                  id={ids.end}
                  type="date"
                  aria-label="Delivery to"
                  value={filters.end_date}
                  min={filters.start_date}
                  max={today}
                  onChange={(e) => onChange({ end_date: e.target.value })}
                />
              </span>
            )}
          </div>
        </div>
        <p id={ids.periodHelp} className="helper">
          {mode === 'january'
            ? 'Purchase decisions made 1–31 Jan 2026, i.e. delivery days 2 Jan – 1 Feb 2026.'
            : mode === 'custom'
              ? 'Delivery dates, inclusive (UK).'
              : 'Delivery days up to now; the window moves forward each day.'}
        </p>
      </fieldset>
    </form>
  )
}
