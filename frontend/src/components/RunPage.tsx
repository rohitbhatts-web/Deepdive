import { useEffect, useState } from 'react'
import { get } from '@/api/client'
import type { Completion, ResearchStatus, RunEvent, RunSummary } from '@/api/types'
import { usePolling } from '@/hooks/useApi'
import { failureNotice, isRunActive, runStatusLabel } from '@/lib/claimState'
import { hrefFor, TABS, type Tab } from '@/lib/route'
import { cn } from '@/lib/utils'
import { ErrorBox, Loading, Notice, Pill, StatTile, num, when } from './bits'
import { CoverageTab } from './CoverageTab'
import { EvidenceTab } from './EvidenceTab'
import { HistoryTab } from './HistoryTab'
import { Progress } from './Progress'
import { ReportTab } from './ReportTab'
import { RetryPanel } from './RetryPanel'
import { UsageTab } from './UsageTab'

export interface Live {
  run: RunSummary
  research: ResearchStatus
  completion: Completion
  events: RunEvent[]
}

const TAB_LABEL: Record<Tab, string> = { report: 'Report', evidence: 'Evidence', coverage: 'Coverage', history: 'Run history', usage: 'Usage' }

export async function loadLive(runId: number, signal: AbortSignal): Promise<Live> {
  const [run, research, completion, events] = await Promise.all([
    get<RunSummary>(`/runs/${runId}`, signal),
    get<ResearchStatus>(`/runs/${runId}/research`, signal),
    get<Completion>(`/runs/${runId}/completion`, signal),
    get<RunEvent[]>(`/runs/${runId}/events`, signal),
  ])
  return { run, research, completion, events }
}

/** The last finished completion job's own budget line, or Unavailable (collection-only runs and running jobs have none yet). */
export function latestJobRequests(c: Completion): string {
  const r = c.budget_used_of_limit?.model_requests
  return r ? `${num(r[0])} / ${num(r[1])}` : 'Unavailable'
}

export function RunPage({ runId, tab, claimId, interval = 2000, onTerminal }: { runId: number; tab: Tab; claimId: number | null; interval?: number; onTerminal?: () => void }) {
  const [restarts, setRestarts] = useState(0) // bumped when a confirmed retry starts, so polling begins again for the new job
  const live = usePolling<Live>(`run-${runId}-${restarts}`, (signal) => loadLive(runId, signal), (d) => !isRunActive(d.run), interval)
  const { data, error } = live
  const finished = data ? !isRunActive(data.run) : false
  useEffect(() => { if (finished) onTerminal?.() }, [finished, onTerminal])

  if (!data && !error) return <div className="mx-auto max-w-[1500px] p-4 md:p-6"><h1 className="sr-only">Loading run {runId}</h1><Loading label="Loading run" blocks={3} /></div>
  if (!data && error)
    return (
      <div className="mx-auto max-w-3xl space-y-3 p-4 md:p-6">
        <h1 className="text-2xl font-semibold tracking-tight">Run {runId} could not be opened</h1>
        <ErrorBox error={error} what={`run ${runId}`} />
        <p className="text-sm"><a className="text-primary underline" href="#/">Back to New research</a></p>
      </div>
    )
  if (!data) return null
  const { run, research, completion, events } = data
  const status = runStatusLabel(run)
  const active = isRunActive(run)
  const version = active ? 0 : 1 // tabs refetch their data once when the run reaches a terminal state
  const stop = completion.stop_reason ?? run.stop_reason
  const failure = active ? null : failureNotice(run, completion)
  const stoppedEarly = !active && !failure && (run.complete_status === 'stopped' || run.status === 'stopped')
  const used = research.model_usage
  const announcement = active ? `Run ${run.id} is in progress.` : `Run ${run.id}: ${status.label}.`

  return (
    <div className="mx-auto max-w-[1500px] space-y-5 p-4 md:p-6">
      <p role="status" className="sr-only">{announcement}</p>
      <header className="space-y-3">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-sm text-muted-foreground">
          <span className="font-medium text-foreground">Run {run.id}</span>
          <Pill tone={status.tone} icon={status.tone === 'ok' ? 'check' : status.tone === 'bad' ? 'x' : status.tone === 'gap' ? 'alert' : 'clock'}>{status.label}</Pill>
          <span>Started {when(run.created_at)}</span>
          <span>Updated {when(run.updated_at)}</span>
        </div>
        <h1 className="text-2xl font-semibold leading-tight tracking-tight break-words md:text-[1.75rem]">{run.question}</h1>
        <dl className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6" aria-label="Run summary">
          <StatTile label="Total model calls for this run">{`${num(used.model_calls)}${used.failed_calls ? ` (${used.failed_calls} failed)` : ''}`}</StatTile>
          <StatTile label="Latest job requests used / limit">{latestJobRequests(completion)}</StatTile>
          <StatTile label="Tokens in / out (all calls)">{`${num(used.prompt_tokens)} / ${num(used.completion_tokens)}`}</StatTile>
          <StatTile label="Searches">{num(research.counts.searches)}</StatTile>
          <StatTile label="Sources">{num(research.counts.sources)}</StatTile>
          <StatTile label="Corrective rounds">{`${completion.corrective_rounds} of ${completion.max_corrective_rounds}`}</StatTile>
        </dl>
        {failure && (
          <Notice tone="bad" title={failure.title}>
            {failure.detail}
            {research.counts.sources > 0 && <> Collected evidence is preserved: {research.counts.sources} source(s), {research.counts.passages} passages (see the Evidence tab).</>}
          </Notice>
        )}
        {failure && failure.title !== 'Collection failed' && <RetryPanel key={`${run.id}-${run.updated_at}`} runId={run.id} onStarted={() => { setRestarts((r) => r + 1); onTerminal?.() }} />}
        {!active && stoppedEarly && (
          <Notice tone="gap" title="Stopped early: results are partial">Reason: {stop ?? 'none recorded'}. Whatever was verified before the stop is kept; requirements that were not addressed are listed as gaps.</Notice>
        )}
        {!active && !stoppedEarly && !failure && stop && <Notice tone="neutral" title="Finished because">{stop}</Notice>}
      </header>

      {(active || error) && <Progress live={data} error={error} />}

      <nav aria-label="Run sections" className="flex gap-1 overflow-x-auto border-b">
        {TABS.map((t) => (
          <a
            key={t}
            href={hrefFor(run.id, t)}
            aria-current={t === tab ? 'page' : undefined}
            className={cn('-mb-px whitespace-nowrap rounded-t-md border-b-2 px-4 py-2.5 text-sm font-medium transition-colors', t === tab ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:bg-muted/60 hover:text-foreground')}
          >
            {TAB_LABEL[t]}
          </a>
        ))}
      </nav>
      <section aria-labelledby="section-title" id="section-panel">
        <h2 id="section-title" className="sr-only">{TAB_LABEL[tab]}</h2>
        {tab === 'report' && <ReportTab run={run} claimId={claimId} version={version} active={active} events={events} />}
        {tab === 'evidence' && <EvidenceTab run={run} claimId={claimId} version={version} />}
        {tab === 'coverage' && <CoverageTab run={run} version={version} />}
        {tab === 'history' && <HistoryTab run={run} events={events} version={version} />}
        {tab === 'usage' && <UsageTab run={run} live={data} version={version} />}
      </section>
    </div>
  )
}
