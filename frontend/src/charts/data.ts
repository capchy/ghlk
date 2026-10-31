// Data shaping shared by chart components and their table fallbacks.
import type { Distribution, Forecast } from '../api/types'
import { toMs } from './common'

export interface UpcomingRow {
  t: number
  forecast: number
  low: number | null
  high: number | null
  band: [number, number] | null
}

/**
 * Hourly forecast with the empirical range a buyer should allow for. The
 * level-based range (errors of past forecasts at a similar level) is used; the
 * overall range is the fallback when there is too little history to band it.
 */
export function upcomingRows(forecast: Forecast): UpcomingRow[] {
  return forecast.items.map((item) => {
    const range = item.estimated_actual_range_by_level ?? item.estimated_actual_range
    return {
      t: toMs(item.start_time),
      forecast: item.generation_mw,
      low: range?.lower_mw ?? null,
      high: range?.upper_mw ?? null,
      band: range ? [range.lower_mw, range.upper_mw] : null,
    }
  })
}

type Bin = Distribution['bins'][number]

/** Bins never straddle 0: the backend aligns edges to multiples of the bin width. */
export const binSide = (b: Bin) => (b.bin_start_mw >= 0 ? 'Overforecast' : 'Underforecast')
