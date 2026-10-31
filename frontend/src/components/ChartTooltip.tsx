import type { ReactNode } from 'react'

export interface TooltipRow {
  label: string
  value: ReactNode
  color?: string
  /** Line style shown in the swatch, so series are not told apart by colour alone. */
  dashed?: boolean
}

export function TooltipBox({ title, rows }: { title: ReactNode; rows: TooltipRow[] }) {
  return (
    <div className="tooltip">
      <div className="tooltip__title">{title}</div>
      {rows.map((row) => (
        <div key={row.label} className="tooltip__row">
          {row.color && (
            <svg width="16" height="8" aria-hidden="true">
              <line
                x1="0"
                y1="4"
                x2="16"
                y2="4"
                stroke={row.color}
                strokeWidth="2.5"
                strokeDasharray={row.dashed ? '4 3' : undefined}
              />
            </svg>
          )}
          <span className="tooltip__label">{row.label}</span>
          <span className="tooltip__value">{row.value}</span>
        </div>
      ))}
    </div>
  )
}
