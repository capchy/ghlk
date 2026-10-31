import { Area, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { TooltipBox } from '../components/ChartTooltip'
import type { ChartPalette } from '../hooks/useChartPalette'
import { fmtMw, fmtTime } from '../lib/format'
import { axisProps, DASH_FORECAST, useReducedMotion, yAxisMw } from './common'
import type { UpcomingRow } from './data'

export function UpcomingForecastChart({ rows, palette }: { rows: UpcomingRow[]; palette: ChartPalette }) {
  const reduced = useReducedMotion()
  return (
    <ResponsiveContainer width="100%" height="100%">
      <ComposedChart data={rows} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
        <CartesianGrid stroke={palette.grid} strokeDasharray="3 3" vertical={false} />
        <XAxis
          dataKey="t"
          type="number"
          scale="time"
          domain={['dataMin', 'dataMax']}
          tickFormatter={(v: number) => fmtTime(new Date(v).toISOString())}
          {...axisProps(palette)}
        />
        <YAxis {...yAxisMw(palette)} domain={[0, 'auto']} />
        <Tooltip
          cursor={{ stroke: palette.axis, strokeDasharray: '3 3' }}
          content={({ active, payload }) => {
            const row = active && payload?.[0]?.payload as UpcomingRow | undefined
            if (!row) return null
            return (
              <TooltipBox
                title={fmtTime(new Date(row.t).toISOString())}
                rows={[
                  { label: 'Forecast', value: fmtMw(row.forecast), color: palette.forecast, dashed: true },
                  {
                    label: 'Est. actual range',
                    value: row.low === null ? 'n/a' : `${fmtMw(row.low)} – ${fmtMw(row.high)}`,
                    color: palette.band,
                  },
                ]}
              />
            )
          }}
        />
        <Area
          dataKey="band"
          stroke="none"
          fill={palette.band}
          fillOpacity={0.2}
          isAnimationActive={!reduced}
          name="Estimated actual range (P10–P90)"
        />
        <Line
          dataKey="forecast"
          stroke={palette.forecast}
          strokeWidth={2.5}
          strokeDasharray={DASH_FORECAST}
          dot={false}
          activeDot={{ r: 5 }}
          isAnimationActive={!reduced}
          name="Forecast"
        />
      </ComposedChart>
    </ResponsiveContainer>
  )
}
