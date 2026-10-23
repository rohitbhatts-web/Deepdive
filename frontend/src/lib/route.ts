import { useEffect, useState } from 'react'

export const TABS = ['overview', 'report', 'evidence', 'coverage', 'history', 'usage'] as const
export type Tab = (typeof TABS)[number]

export type Route = { page: 'new' } | { page: 'run'; runId: number; tab: Tab; claimId: number | null }

/** Routes live in the URL hash so a refresh keeps the run, the tab and the open claim: #/runs/3/evidence?claim=12 */
export function parseHash(hash: string): Route {
  const [path, query = ''] = hash.replace(/^#/, '').split('?')
  const m = /^\/runs\/(\d+)(?:\/([a-z]+))?$/.exec(path)
  if (!m) return { page: 'new' }
  const tab = (TABS as readonly string[]).includes(m[2] ?? '') ? (m[2] as Tab) : 'overview'
  const claim = new URLSearchParams(query).get('claim')
  return { page: 'run', runId: Number(m[1]), tab, claimId: claim && /^\d+$/.test(claim) ? Number(claim) : null }
}

export function hrefFor(runId: number, tab: Tab = 'overview', claimId?: number | null): string {
  return `#/runs/${runId}/${tab}${claimId ? `?claim=${claimId}` : ''}`
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash))
  useEffect(() => {
    const on = () => setRoute(parseHash(window.location.hash))
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  return route
}
