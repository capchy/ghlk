// Only the response fields the dashboard uses (full shapes: backend/README.md, section 12).

export interface AnalysisWindow {
  start: string
  end: string
}

export interface Coverage {
  missing_actual_periods: number
  actual_data_complete: boolean
  target_periods: number
  records_matched: number
}

export interface Metrics {
  sample_count: number
  mae_mw: number | null
  bias_mw: number | null
  wape_pct: number | null
}

export interface ErrorRange {
  available: boolean
  p10_error_mw: number | null
  p50_error_mw: number | null
  p90_error_mw: number | null
  explanation: string
}

interface EstimatedRange {
  lower_mw: number
  upper_mw: number
}

export interface ForecastItem {
  start_time: string
  publish_time: string
  generation_mw: number
  estimated_actual_range: EstimatedRange | null
  estimated_actual_range_by_level: EstimatedRange | null
}

export interface Forecast {
  day_start: string
  available: boolean
  complete: boolean
  resolution_minutes: number | null
  expected_periods: number | null
  latest_publish_time: string | null
  items: ForecastItem[]
  warnings: string[]
}

export interface Dashboard {
  scope_note: string
  forecast: Forecast | null
  /** decision_cutoff: as published by the D-1 cutoff (passed); latest: cutoff still ahead or unknown. */
  forecast_basis: 'decision_cutoff' | 'latest' | 'as_of'
  decision_at: string | null
  historical_accuracy: {
    analysis_window: AnalysisWindow
    decision_cutoff: { time: string }
    coverage: Coverage
    metrics: Metrics
    error_range: ErrorRange
  } | null
  warnings: string[]
}

export interface SeriesPoint {
  timestamp: string
  forecast_mw: number
  actual_mw: number
  error_mw: number
  forecast_publish_time: string
}

export interface AccuracySeries {
  series: SeriesPoint[]
}

export interface Distribution {
  bins: { bin_start_mw: number; bin_end_mw: number; count: number }[]
  sample_count: number
  overforecast_count: number
  underforecast_count: number
}

export interface DataStatus {
  actual: { record_count: number; latest_generation_period_end: string | null }
  forecast: { latest_publish_time: string | null }
  default_window: { decision_cutoff_configured: boolean }
  warnings: string[]
}

export interface IngestResult {
  status: 'success' | 'partial' | 'failed'
  records_inserted: number
  records_updated: number
  errors: string[]
}
