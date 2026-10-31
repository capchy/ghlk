// All API timestamps are UTC; display is in UK local time (Europe/London).
const UK = 'Europe/London'

const dateTime = new Intl.DateTimeFormat('en-GB', {
  timeZone: UK,
  day: '2-digit',
  month: 'short',
  hour: '2-digit',
  minute: '2-digit',
})
const dateOnly = new Intl.DateTimeFormat('en-GB', { timeZone: UK, day: '2-digit', month: 'short' })
const dateLong = new Intl.DateTimeFormat('en-GB', { timeZone: UK, weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })
const timeOnly = new Intl.DateTimeFormat('en-GB', { timeZone: UK, hour: '2-digit', minute: '2-digit' })
const mw = new Intl.NumberFormat('en-GB', { maximumFractionDigits: 0 })
const pct = new Intl.NumberFormat('en-GB', { maximumFractionDigits: 1 })
const ukDateParts = new Intl.DateTimeFormat('en-CA', { timeZone: UK, year: 'numeric', month: '2-digit', day: '2-digit' })

export const fmtDateTime = (iso: string) => dateTime.format(new Date(iso))
export const fmtDate = (iso: string) => dateOnly.format(new Date(iso))
export const fmtDateLong = (iso: string) => dateLong.format(new Date(iso))
export const fmtTime = (iso: string) => timeOnly.format(new Date(iso))
export const fmtMw = (value: number | null | undefined) => (value === null || value === undefined ? '—' : `${mw.format(value)} MW`)
export const fmtNum = (value: number | null | undefined) => (value === null || value === undefined ? '—' : mw.format(value))
export const fmtPct = (value: number | null | undefined) => (value === null || value === undefined ? '—' : `${pct.format(value)}%`)
export const fmtGw = (value: number) => `${(value / 1000).toLocaleString('en-GB', { maximumFractionDigits: 1 })} GW`

/** Signed MW with an explicit +/−, e.g. "+2,596 MW". */
export function fmtSignedMw(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  const sign = value > 0 ? '+' : value < 0 ? '−' : ''
  return `${sign}${mw.format(Math.abs(value))} MW`
}

/** Today's date in the UK as YYYY-MM-DD. */
export function ukToday(): string {
  return ukDateParts.format(new Date())
}

export function addDays(isoDate: string, days: number): string {
  const d = new Date(`${isoDate}T12:00:00Z`)
  d.setUTCDate(d.getUTCDate() + days)
  return d.toISOString().slice(0, 10)
}
