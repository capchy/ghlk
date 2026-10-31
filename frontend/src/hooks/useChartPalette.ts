import { useState } from 'react'

/** Resolved colours for SVG charts (Recharts takes literal colour values). */
export interface ChartPalette {
  actual: string
  forecast: string
  band: string
  grid: string
  axis: string
  primary: string
  secondary: string
  accent: string
  card: string
  border: string
  text: string
}

function readPalette(): ChartPalette {
  const css = getComputedStyle(document.documentElement)
  const v = (name: string) => css.getPropertyValue(name).trim()
  return {
    actual: v('--chart-actual'),
    forecast: v('--chart-forecast'),
    band: v('--chart-band'),
    grid: v('--chart-grid'),
    axis: v('--chart-axis'),
    primary: v('--color-primary'),
    secondary: v('--color-secondary'),
    accent: v('--color-accent'),
    card: v('--color-card'),
    border: v('--color-border'),
    text: v('--color-card-foreground'),
  }
}

/** Chart colours from the (light-only) design tokens. */
export function useChartPalette(): ChartPalette {
  const [palette] = useState(readPalette)
  return palette
}
