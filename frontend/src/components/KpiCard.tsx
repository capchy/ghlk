import type { ReactNode } from 'react'

interface KpiCardProps {
  label: string
  value: ReactNode
  detail?: ReactNode
  icon: ReactNode
  loading?: boolean
  tone?: 'default' | 'accent'
}

export function KpiCard({ label, value, detail, icon, loading = false, tone = 'default' }: KpiCardProps) {
  return (
    <div className={`kpi kpi--${tone}`} aria-busy={loading}>
      <div className="kpi__label">
        <span className="kpi__icon" aria-hidden="true">
          {icon}
        </span>
        {label}
      </div>
      {loading ? (
        <div className="skeleton skeleton--text" aria-hidden="true" />
      ) : (
        <>
          <div className="kpi__value">{value}</div>
          {detail && <div className="kpi__detail">{detail}</div>}
        </>
      )}
    </div>
  )
}
