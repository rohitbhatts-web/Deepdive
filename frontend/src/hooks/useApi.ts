import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, get } from '@/api/client'

export interface Resource<T> {
  data: T | null
  error: ApiError | null
  loading: boolean
  reload: () => void
}

/** Fetch `path` (null = do nothing). Re-fetches when `path` or `version` changes; in-flight requests are aborted on change/unmount. */
export function useResource<T>(path: string | null, version = 0): Resource<T> {
  const [state, setState] = useState<{ data: T | null; error: ApiError | null; loading: boolean }>({ data: null, error: null, loading: path !== null })
  const [tick, setTick] = useState(0)
  useEffect(() => {
    if (path === null) return
    const ctl = new AbortController()
    setState((s) => ({ ...s, loading: true, error: null }))
    get<T>(path, ctl.signal)
      .then((data) => setState({ data, error: null, loading: false }))
      .catch((e: unknown) => {
        if (e instanceof DOMException && e.name === 'AbortError') return
        setState({ data: null, error: e instanceof ApiError ? e : new ApiError(0, String(e)), loading: false })
      })
    return () => ctl.abort()
  }, [path, version, tick])
  const reload = useCallback(() => setTick((t) => t + 1), [])
  return { ...state, reload }
}

export interface Polled<T> {
  data: T | null
  error: ApiError | null
  polling: boolean
}

/**
 * Poll `load` every `intervalMs` until `isDone(data)` is true, then stop. Nothing is simulated: every tick is a real request,
 * and the timer plus any in-flight request are cancelled when the component unmounts or `key` changes.
 */
export function usePolling<T>(key: string, load: (signal: AbortSignal) => Promise<T>, isDone: (d: T) => boolean, intervalMs = 2000): Polled<T> {
  const [state, setState] = useState<Polled<T>>({ data: null, error: null, polling: true })
  const loadRef = useRef(load)
  const doneRef = useRef(isDone)
  loadRef.current = load
  doneRef.current = isDone
  useEffect(() => {
    const ctl = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    setState({ data: null, error: null, polling: true })
    const tick = async () => {
      try {
        const data = await loadRef.current(ctl.signal)
        if (ctl.signal.aborted) return
        const done = doneRef.current(data)
        setState({ data, error: null, polling: !done })
        if (!done) timer = setTimeout(tick, intervalMs)
      } catch (e) {
        if (ctl.signal.aborted || (e instanceof DOMException && e.name === 'AbortError')) return
        const err = e instanceof ApiError ? e : new ApiError(0, String(e))
        setState((s) => ({ data: s.data, error: err, polling: err.status !== 404 }))
        if (err.status !== 404) timer = setTimeout(tick, intervalMs) // transient failure: keep trying, keep showing the error
      }
    }
    void tick()
    return () => {
      ctl.abort()
      if (timer) clearTimeout(timer)
    }
  }, [key, intervalMs])
  return state
}
