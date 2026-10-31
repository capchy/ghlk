import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { Distribution } from '../api/types'
import { TooltipBox } from '../components/ChartTooltip'
import type { ChartPalette } from '../hooks/useChartPalette'
import { fmtGw, fmtNum } from '../lib/format'
import { axisProps, useReducedMotion } from './common'
import { binSide } from './data'

type Bin = Distribution['bins'][number]

export function ErrorDistributionChart({ dist, palette }: { dist: Distribution; palette: ChartPalette }) {
  const reduced = useReducedMotion()
  const data = dist.bins.map((b) => ({ ...b, label: fmtGw(b.bin_start_mw) }))
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }} barCategoryGap={1}>
        <CartesianGrid stroke={palette.grid} strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="label" {...axisProps(palette)} minTickGap={8} />
        <YAxis {...axisProps(palette)} allowDecimals={false} width={40} />
        <Tooltip
          cursor={{ fill: palette.grid, fillOpacity: 0.4 }}
          content={({ active, payload }) => {
            const b = active && (payload?.[0]?.payload as Bin | undefined)
            if (!b) return null
            return (
              <TooltipBox
                title={`${fmtNum(b.bin_start_mw)} to ${fmtNum(b.bin_end_mw)} MW`}
                rows={[
                  { label: binSide(b), value: `${b.count} hour${b.count === 1 ? '' : 's'}` },
                  { label: 'Share', value: `${((b.count / dist.sample_count) * 100).toFixed(1)}%` },
                ]}
              />
            )
          }}
        />
        <Bar dataKey="count" isAnimationActive={!reduced} name="Hours">
          {data.map((b) => (
            <Cell key={b.bin_start_mw} fill={b.bin_start_mw >= 0 ? palette.forecast : palette.actual} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}
