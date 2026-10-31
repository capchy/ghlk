import { useId, useState, type ReactNode } from 'react'
import { ArrowClockwise, ChartLine, Table, WarningCircle } from '@phosphor-icons/react'
import type { ApiError } from '../api/client'

interface ChartCardProps {
  title: string
  subtitle?: ReactNode
  /** One-sentence key insight, read by screen readers and shown under the chart. */
  summary?: ReactNode
  loading: boolean
  error: ApiError | null
  onRetry?: () => void
  empty?: { when: boolean; message: ReactNode; action?: ReactNode }
  actions?: ReactNode
  /** Accessible table fallback for the chart's data. */
  table?: () => ReactNode
  height?: number
  className?: string
  children: ReactNode
}

export function ChartCard({
  title,
  subtitle,
  summary,
  loading,
  error,
  onRetry,
  empty,
  actions,
  table,
  height = 300,
  className = '',
  children,
}: ChartCardProps) {
  const [showTable, setShowTable] = useState(false)
  const headingId = useId()
  const summaryId = useId()

  let body: ReactNode
  if (loading) {
    body = <div className="skeleton" style={{ height }} aria-hidden="true" />
  } else if (error) {
    body = (
      <div className="state state--error" role="alert" style={{ minHeight: height }}>
        <WarningCircle size={28} aria-hidden="true" />
        <p>{error.message}</p>
        {onRetry && (
          <button type="button" className="btn btn--secondary" onClick={onRetry}>
            <ArrowClockwise size={16} aria-hidden="true" /> Retry
          </button>
        )}
      </div>
    )
  } else if (empty?.when) {
    body = (
      <div className="state" style={{ minHeight: height }}>
        <p>{empty.message}</p>
        {empty.action}
      </div>
    )
  } else if (showTable && table) {
    body = (
      <div className="table-scroll" style={{ maxHeight: height + 40 }}>
        {table()}
      </div>
    )
  } else {
    body = (
      <figure className="chart" style={{ height }} aria-labelledby={headingId} aria-describedby={summary ? summaryId : undefined}>
        {children}
      </figure>
    )
  }

  const canToggle = Boolean(table) && !loading && !error && !empty?.when
  return (
    <section className={`card ${className}`} aria-labelledby={headingId} aria-busy={loading}>
      <header className="card__header">
        <div>
          <h2 id={headingId} className="card__title">
            {title}
          </h2>
          {subtitle && <p className="card__subtitle">{subtitle}</p>}
        </div>
        <div className="card__actions">
          {actions}
          {canToggle && (
            <button
              type="button"
              className="btn btn--ghost"
              aria-pressed={showTable}
              onClick={() => setShowTable((s) => !s)}
            >
              {showTable ? <ChartLine size={16} aria-hidden="true" /> : <Table size={16} aria-hidden="true" />}
              {showTable ? 'Chart' : 'Table'}
            </button>
          )}
        </div>
      </header>
      {body}
      {summary && !loading && !error && !empty?.when && (
        <p id={summaryId} className="card__summary">
          {summary}
        </p>
      )}
    </section>
  )
}
