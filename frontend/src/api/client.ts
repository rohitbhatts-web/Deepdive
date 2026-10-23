// Same-origin calls: the dev server proxies /api to the FastAPI backend, so no key or backend address reaches this code.
export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

/** Turn the backend's `detail` (string, validation list or {message}) into one readable line. */
export function describeDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail))
    return detail.map((d) => (d && typeof d === 'object' && 'msg' in d ? String((d as { msg: unknown }).msg) : String(d))).join('; ')
  if (detail && typeof detail === 'object') {
    const d = detail as { message?: unknown; reason?: unknown; missing?: unknown }
    const base = String(d.message ?? d.reason ?? JSON.stringify(detail))
    return Array.isArray(d.missing) && d.missing.length ? `${base} (missing: ${d.missing.join(', ')})` : base
  }
  return 'Unknown error'
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(`/api${path}`, init)
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') throw e
    throw new ApiError(0, 'Cannot reach the DeepDive backend. Is it running?')
  }
  if (!res.ok) {
    let message = res.status >= 500 ? `The backend is not responding correctly (HTTP ${res.status}).` : `HTTP ${res.status} ${res.statusText}`
    try {
      message = describeDetail((await res.json()).detail)
    } catch {
      /* non-JSON error body (for example the dev proxy when the backend is down): keep the status line */
    }
    throw new ApiError(res.status, message)
  }
  return (await res.json()) as T
}

export const get = <T,>(path: string, signal?: AbortSignal) => request<T>(path, { signal })
export const post = <T,>(path: string, body?: unknown) =>
  request<T>(path, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
