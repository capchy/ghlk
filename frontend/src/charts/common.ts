import { useSyncExternalStore } from 'react'
import type { ChartPalette } from '../hooks/useChartPalette'
import { fmtGw } from '../lib/format'

const QUERY = '(prefers-reduced-motion: reduce)'

/** Charts render their final state immediately when the user asks for reduced motion. */
export function useReducedMotion(): boolean {
  return useSyncExternalStore(
    (notify) => {
      const mq = window.matchMedia(QUERY)
      mq.addEventListener('change', notify)
      return () => mq.removeEventListener('change', notify)
    },
    () => window.matchMedia(QUERY).matches,
  )
}

export const toMs = (iso: string) => new Date(iso).getTime()

export function axisProps(palette: ChartPalette) {
  return {
    stroke: palette.axis,
    tick: { fill: palette.axis, fontSize: 12 },
    tickLine: false,
    axisLine: { stroke: palette.grid },
  }
}

export function yAxisMw(palette: ChartPalette) {
  return {
    ...axisProps(palette),
    tickFormatter: (v: number) => fmtGw(v),
    width: 64,
  }
}

export const DASH_FORECAST = '6 4'
