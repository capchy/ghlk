import type { Filters } from '../hooks/useUrlState'

/**
 * The brief scopes accuracy to purchase decisions made in January 2026. Each
 * decision is made on D-1, so those decisions cover delivery days 2 Jan – 1 Feb.
 * The backend's custom window takes delivery dates (Europe/London, inclusive).
 */
export const JANUARY_2026 = { start_date: '2026-01-02', end_date: '2026-02-01' } as const

export type PeriodMode = 'january' | 'rolling' | 'custom'

export function periodMode(filters: Filters): PeriodMode {
  if (filters.period === 'rolling') return 'rolling'
  if (filters.period === 'custom') return 'custom'
  return 'january'
}

/** Query parameters selecting the accuracy period. */
export function periodParams(filters: Filters): { window_days: string; start_date: string; end_date: string } {
  switch (periodMode(filters)) {
    case 'rolling':
      return { window_days: filters.window_days, start_date: '', end_date: '' }
    case 'custom':
      return { window_days: '', start_date: filters.start_date, end_date: filters.end_date }
    default:
      return { window_days: '', ...JANUARY_2026 }
  }
}

/** Ingest request body covering the period (empty = backend default: rolling window). */
export function ingestBody(filters: Filters): { start_date?: string; end_date?: string } {
  const { start_date, end_date } = periodParams(filters)
  return start_date && end_date ? { start_date, end_date } : {}
}
