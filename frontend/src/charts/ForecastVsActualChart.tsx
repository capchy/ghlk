import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { SeriesPoint } from '../api/types'
import { TooltipBox } from '../components/ChartTooltip'
import type { ChartPalette } from '../hooks/useChartPalette'
import { fmtDate, fmtDateTime, fmtMw, fmtSignedMw } from '../lib/format'
import { axisProps, DASH_FORECAST, toMs, useReducedMotion, yAxisMw } from './common'

export function ForecastVsActualChart({ series, palette }: { series: SeriesPoint[]; palette: ChartPalette }) {
  const reduced = useReducedMotion()
  const data = series.map((p) => ({ ...p, t: toMs(p.timestamp) }))
  return (
    <ResponsiveContainer width="100%" height="100%">
      <LineChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
        <CartesianGrid stroke={palette.grid} strokeDasharray="3 3" vertical={false} />
        <XAxis
          dataKey="t"
          type="number"
          scale="time"
          domain={['dataMin', 'dataMax']}
          tickFormatter={(v: number) => fmtDate(new Date(v).toISOString())}
          minTickGap={24}
          {...axisProps(palette)}
        />
        <YAxis {...yAxisMw(palette)} domain={[0, 'auto']} />
        <Tooltip
          cursor={{ stroke: palette.axis, strokeDasharray: '3 3' }}
          content={({ active, payload }) => {
            const p = active && (payload?.[0]?.payload as SeriesPoint | undefined)
            if (!p) return null
            return (
              <TooltipBox
                title={fmtDateTime(p.timestamp)}
                rows={[
                  { label: 'Actual', value: fmtMw(p.actual_mw), color: palette.actual },
                  { label: 'Forecast', value: fmtMw(p.forecast_mw), color: palette.forecast, dashed: true },
                  { label: 'Error', value: fmtSignedMw(p.error_mw) },
                  { label: 'Published', value: fmtDateTime(p.forecast_publish_time) },
                ]}
              />
            )
          }}
        />
        <Line dataKey="actual_mw" stroke={palette.actual} strokeWidth={1.75} dot={false} isAnimationActive={!reduced} name="Actual" />
        <Line
          dataKey="forecast_mw"
          stroke={palette.forecast}
          strokeWidth={1.75}
          strokeDasharray={DASH_FORECAST}
          dot={false}
          isAnimationActive={!reduced}
          name="Forecast"
        />
      </LineChart>
    </ResponsiveContainer>
  )
}
