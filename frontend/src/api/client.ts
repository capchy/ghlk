const BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, '') ?? ''

export type QueryParams = Record<string, string | number | null | undefined>

/** An API failure with the backend's own explanation (FastAPI `detail`). */
export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

function buildUrl(path: string, params?: QueryParams): string {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value !== null && value !== undefined && value !== '') query.set(key, String(value))
  }
  const qs = query.toString()
  return `${BASE_URL}${path}${qs ? `?${qs}` : ''}`
}

function detailMessage(body: unknown, status: number): string {
  if (body && typeof body === 'object' && 'detail' in body) {
    const detail = (body as { detail: unknown }).detail
    if (typeof detail === 'string') return detail
    // FastAPI validation errors: [{loc, msg}, ...]
    if (Array.isArray(detail)) {
      return detail.map((d) => `${(d.loc ?? []).slice(1).join('.')}: ${d.msg}`).join('; ')
    }
  }
  // No JSON body on a 5xx means the dev proxy could not reach the API at all.
  if (body === null && status >= 500) return 'Cannot reach the backend. Check that the API is running on port 8000.'
  if (status === 502) return 'The Elexon data service is unavailable. Try again shortly.'
  return `Request failed (HTTP ${status}).`
}

export async function request<T>(
  path: string,
  options: { params?: QueryParams; method?: 'GET' | 'POST'; body?: unknown; signal?: AbortSignal } = {},
): Promise<T> {
  let response: Response
  try {
    response = await fetch(buildUrl(path, options.params), {
      method: options.method ?? 'GET',
      headers: options.body ? { 'Content-Type': 'application/json' } : undefined,
      body: options.body ? JSON.stringify(options.body) : undefined,
      signal: options.signal,
    })
  } catch (err) {
    if ((err as Error).name === 'AbortError') throw err
    throw new ApiError(0, 'Cannot reach the backend. Check that the API is running on port 8000.')
  }
  const body: unknown = await response.json().catch(() => null)
  if (!response.ok) throw new ApiError(response.status, detailMessage(body, response.status))
  return body as T
}
