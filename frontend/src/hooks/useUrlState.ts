import { useCallback, useState } from 'react'

export interface Filters {
  /** '' = January 2026 decisions (default), 'rolling' or 'custom'. */
  period: string
  window_days: string
  start_date: string
  end_date: string
  decision_cutoff: string
  delivery_date: string
}

const KEYS: (keyof Filters)[] = ['period', 'window_days', 'start_date', 'end_date', 'decision_cutoff', 'delivery_date']

function read(): Filters {
  const params = new URLSearchParams(window.location.search)
  return Object.fromEntries(KEYS.map((k) => [k, params.get(k) ?? ''])) as unknown as Filters
}

/**
 * Filters live in the URL so every view can be shared or bookmarked
 * (deep-linking). Uses replaceState: changing a filter is not navigation.
 */
export function useUrlState(): [Filters, (patch: Partial<Filters>) => void] {
  const [filters, setFilters] = useState<Filters>(read)

  const update = useCallback((patch: Partial<Filters>) => {
    setFilters((prev) => {
      const next = { ...prev, ...patch }
      const params = new URLSearchParams()
      for (const k of KEYS) if (next[k]) params.set(k, next[k])
      const qs = params.toString()
      window.history.replaceState(null, '', `${window.location.pathname}${qs ? `?${qs}` : ''}`)
      return next
    })
  }, [])

  return [filters, update]
}
