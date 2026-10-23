import { Loader2 } from 'lucide-react'
import type { ApiError } from '@/api/client'
import { eventDetail, eventLabel, STAGE_LABEL } from '@/lib/events'
import { Notice, Panel, StatTile, num, when } from './bits'
import type { Live } from './RunPage'

/**
 * Real progress only: the stages that have recorded events, the newest event, and counters read from the database on every poll.
 * There is no percentage because the total amount of work is not known in advance.
 */
export function Progress({ live, error }: { live: Live; error: ApiError | null }) {
  const { events, research, run } = live
  const stages: string[] = []
  for (const e of events) if (!stages.includes(e.stage)) stages.push(e.stage)
  const last = events[events.length - 1]
  const limit = research.limits.model_requests
  const active = run.status === 'researching' || run.verify_status === 'verifying' || run.complete_status === 'completing'
  return (
    <Panel accent={active ? 'neutral' : undefined}>
      <section aria-label="Run progress" className="space-y-3 p-4">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          {active && <Loader2 aria-hidden className="size-4 text-primary motion-safe:animate-spin" />}
          <h2 className="font-semibold">{active ? 'Research in progress' : 'Progress'}</h2>
          <span className="text-sm text-muted-foreground">refreshes every few seconds from stored run data</span>
        </div>
        {error && <Notice tone="gap" title="Could not refresh">{error.message} Retrying.</Notice>}
        {last ? (
          <p className="text-sm" role="status">
            Latest: <strong>{eventLabel(last)}</strong>
            {eventDetail(last) ? <> – <span className="break-words">{eventDetail(last)}</span></> : null}
            <span className="text-muted-foreground"> ({STAGE_LABEL[last.stage] ?? last.stage}, {when(last.created_at)})</span>
          </p>
        ) : (
          <p className="text-sm text-muted-foreground">Waiting for the first recorded step.</p>
        )}
        <ol className="flex flex-wrap gap-1.5 text-xs" aria-label="Stages reached so far">
          {stages.map((s, i) => (
            <li
              key={s}
              className={i === stages.length - 1 && active ? 'rounded-md border border-primary/40 bg-accent px-2 py-0.5 font-medium text-accent-foreground' : 'rounded-md border bg-muted px-2 py-0.5'}
              aria-current={active && i === stages.length - 1 ? 'step' : undefined}
            >
              {STAGE_LABEL[s] ?? s}
            </li>
          ))}
        </ol>
        <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <StatTile label="Model calls recorded for this run" hint={`each job is limited to ${num(limit)}`}>{num(research.model_usage.model_calls)} <span className="text-xs font-normal text-muted-foreground">(job limit {num(limit)})</span></StatTile>
          <StatTile label="Searches">{num(research.counts.searches)} <span className="text-xs font-normal text-muted-foreground">(job limit {num(research.limits.search_queries)})</span></StatTile>
          <StatTile label="Page fetch attempts">{num(research.counts.page_fetch_attempts)} <span className="text-xs font-normal text-muted-foreground">(job limit {num(research.limits.page_fetches)})</span></StatTile>
          <StatTile label="Sources stored">{num(research.counts.sources)}</StatTile>
        </dl>
      </section>
    </Panel>
  )
}
