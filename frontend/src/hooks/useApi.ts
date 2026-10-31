import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, request, type QueryParams } from '../api/client'

export interface ApiState<T> {
  data: T | null
  error: ApiError | null
  loading: boolean
  reload: () => void
}

/**
 * GET an endpoint and re-fetch whenever its parameters change.
 * In-flight requests are aborted on change/unmount, so a slow earlier
 * response can never overwrite a newer one.
 */
export function useApi<T>(path: string | null, params: QueryParams = {}, refreshKey = 0): ApiState<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [loading, setLoading] = useState(path !== null)
  const [nonce, setNonce] = useState(0)
  // Stable string key so callers can pass a fresh object every render.
  const key = JSON.stringify(params)
  const keyRef = useRef(key)
  keyRef.current = key

  useEffect(() => {
    if (path === null) {
      setLoading(false)
      return
    }
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    request<T>(path, { params: JSON.parse(keyRef.current) as QueryParams, signal: controller.signal })
      .then((result) => {
        setData(result)
        setLoading(false)
      })
      .catch((err: unknown) => {
        if ((err as Error).name === 'AbortError') return
        setError(err instanceof ApiError ? err : new ApiError(0, String(err)))
        setData(null)
        setLoading(false)
      })
    return () => controller.abort()
  }, [path, key, refreshKey, nonce])

  const reload = useCallback(() => setNonce((n) => n + 1), [])
  return { data, error, loading, reload }
}
