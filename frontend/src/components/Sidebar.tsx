import { Plus, Search } from 'lucide-react'
import { useEffect } from 'react'
import type { RunPage } from '@/api/types'
import { useResource } from '@/hooks/useApi'
import { isRunActive, runStatusLabel } from '@/lib/claimState'
import { hrefFor } from '@/lib/route'
import { cn } from '@/lib/utils'
import { Pill } from './bits'

/** Dark charcoal navigation: New research plus the real recent runs from GET /runs (newest first). */
export function Sidebar({ activeRunId, version, onNavigate }: { activeRunId: number | null; version: number; onNavigate?: () => void }) {
  const runs = useResource<RunPage>('/runs?limit=30', version)
  const anyActive = runs.data?.items.some(isRunActive) ?? false
  const { reload } = runs
  useEffect(() => {
    if (!anyActive) return // nothing running: no background refresh at all
    const t = setInterval(reload, 4000)
    return () => clearInterval(t)
  }, [anyActive, reload])
  return (
    <nav aria-label="Research navigation" className="flex h-full flex-col gap-5 bg-sidebar p-4 text-sidebar-foreground">
      <div className="flex items-center gap-2.5 px-1 pt-1">
        <span className="flex size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground"><Search aria-hidden className="size-4" /></span>
        <span className="text-lg font-semibold tracking-tight text-white">DeepDive</span>
      </div>
      <a
        href="#/"
        onClick={onNavigate}
        className="flex items-center justify-center gap-2 rounded-lg bg-primary px-3 py-2.5 text-sm font-medium text-primary-foreground transition-colors hover:brightness-110"
      >
        <Plus aria-hidden className="size-4" /> New research
      </a>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <h2 className="px-1 pb-2 text-xs font-semibold uppercase tracking-wider text-sidebar-muted">Recent runs</h2>
        {runs.loading && !runs.data && <p role="status" className="px-1 text-sm text-sidebar-muted">Loading…</p>}
        {runs.error && <p role="alert" className="px-1 text-sm text-red-300">Could not load runs: {runs.error.message}</p>}
        {runs.data && runs.data.items.length === 0 && <p className="px-1 text-sm text-sidebar-muted">No runs yet. Start one with New research.</p>}
        <ul className="space-y-1.5">
          {runs.data?.items.map((r) => {
            const st = runStatusLabel(r)
            const current = r.id === activeRunId
            return (
              <li key={r.id}>
                <a
                  href={hrefFor(r.id)}
                  onClick={onNavigate}
                  aria-current={current ? 'page' : undefined}
                  className={cn(
                    'block rounded-lg border border-l-4 px-3 py-2 transition-colors hover:bg-white/5',
                    current ? 'border-sidebar-border border-l-primary bg-white/10' : 'border-transparent border-l-transparent',
                  )}
                >
                  <span className="line-clamp-2 text-sm leading-snug text-white">{r.question}</span>
                  <span className="mt-1.5 flex items-center gap-2 text-xs text-sidebar-muted">
                    <span>Run {r.id}</span>
                    <Pill tone={st.tone} className="!border-transparent">{st.label}</Pill>
                  </span>
                </a>
              </li>
            )
          })}
        </ul>
        {runs.data && runs.data.total > runs.data.items.length && (
          <p className="px-1 pt-3 text-xs text-sidebar-muted">Showing the newest {runs.data.items.length} of {runs.data.total}.</p>
        )}
      </div>
    </nav>
  )
}
