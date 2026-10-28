import { Fragment, useEffect, useState } from 'react'
import { get } from '@/api/client'
import type { Completion, ResearchStatus, RunEvent, RunSummary } from '@/api/types'
import { usePolling } from '@/hooks/useApi'
import { useDemo } from '@/hooks/useDemo'
import { failureNotice, isRunActive, runStatusLabel } from '@/lib/claimState'
import { hrefFor, TABS, type Tab } from '@/lib/route'
import { latestJobBudget, latestJobRequests, tokenValue, usageState } from '@/lib/usage'
import { cn } from '@/lib/utils'
import { ErrorBox, Loading, Notice, Pill, StatTile, num, when } from './bits'
import { CoverageTab } from './CoverageTab'
import { EvidenceTab } from './EvidenceTab'
import { HistoryTab } from './HistoryTab'
import { LifecycleStrip } from './Lifecycle'
import { OverviewTab } from './OverviewTab'
import { Progress } from './Progress'
import { ReportTab } from './ReportTab'
import { RetryPanel } from './RetryPanel'
import { UsageTab } from './UsageTab'
import { Wave } from './Wave'

export interface Live {
  run: RunSummary
  research: ResearchStatus
  completion: Completion
  events: RunEvent[]
}

const TAB_LABEL: Record<Tab, string> = { overview: 'Overview', report: 'Report', evidence: 'Evidence', coverage: 'Coverage', history: 'Run history', usage: 'Usage' }

/** The recorded-run note. On narrow screens it starts as three lines with a toggle, so a long note does not push the answer below the first screen; the full text is always in the page. */
function DemoNote({ title, note }: { title: string; note: string }) {
  const [open, setOpen] = useState(false)
  return (
    <Notice tone="neutral" title={title}>
      <span className={cn('block', !open && 'line-clamp-3 md:line-clamp-none')}>{note}</span>
      <button type="button" aria-expanded={open} onClick={() => setOpen((o) => !o)} className="mt-1 font-medium text-primary underline underline-offset-2 md:hidden">{open ? 'Show less' : 'Read the full note'}</button>
    </Notice>
  )
}

export async function loadLive(runId: number, signal: AbortSignal): Promise<Live> {
  const [run, research, completion, events] = await Promise.all([
    get<RunSummary>(`/runs/${runId}`, signal),
    get<ResearchStatus>(`/runs/${runId}/research`, signal),
    get<Completion>(`/runs/${runId}/completion`, signal),
    get<RunEvent[]>(`/runs/${runId}/events`, signal),
  ])
  return { run, research, completion, events }
}

export function RunPage({ runId, tab, claimId, interval = 2000, onTerminal }: { runId: number; tab: Tab; claimId: number | null; interval?: number; onTerminal?: () => void }) {
  const [restarts, setRestarts] = useState(0) // bumped when a confirmed retry starts, so polling begins again for the new job
  const { info: demo } = useDemo()
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
  const jobBudget = latestJobBudget(events, completion)
  const announcement = active ? `Run ${run.id} is in progress.` : `Run ${run.id}: ${status.label}.`

  return (
    <div className="mx-auto max-w-[1500px] space-y-5 p-4 md:p-6">
      <p role="status" className="sr-only">{announcement}</p>
      <header className="relative overflow-hidden rounded-2xl border bg-gradient-to-r from-white via-[#e8f7f4] to-[#c9def8] p-5 shadow-sm md:px-7 md:py-6">
        <Wave className="pointer-events-none absolute inset-y-0 right-0 hidden h-full w-1/2 md:block" />
        <div className="relative space-y-3">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-sm text-muted-foreground md:pr-[40%]">
          <span className="font-medium text-foreground">Run {run.id}</span>
          <Pill tone={status.tone} icon={status.tone === 'ok' ? 'check' : status.tone === 'bad' ? 'x' : status.tone === 'gap' ? 'alert' : 'clock'}>{status.label}</Pill>
          <span>Started {when(run.created_at)}</span>
          <span className="hidden sm:inline">Updated {when(run.updated_at)}</span>
        </div>
        <h1 className="text-2xl font-bold leading-tight tracking-tight break-words md:pr-[40%]">{run.question}</h1>
        {tab === 'overview' && <div className="@container md:pr-[8%]"><LifecycleStrip run={run} events={events} completion={completion} /></div>}
        {tab !== 'overview' && <dl className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6" aria-label="Run summary">
          <StatTile label="Total model calls for this run">{`${num(used.model_calls)}${used.failed_calls ? ` (${used.failed_calls} failed)` : ''}`}</StatTile>
          <StatTile label="Latest job requests used / limit" hint={jobBudget ? `Recorded by the ${jobBudget.source}` : 'No job has recorded its final budget'}>{latestJobRequests(events, completion)}</StatTile>
          <StatTile label={usageState(used) === 'partial' ? `Tokens in / out (usage from ${used.calls_with_usage ?? 0} of ${used.model_calls} calls)` : 'Tokens in / out (all calls)'}>{`${tokenValue(used.prompt_tokens, used)} / ${tokenValue(used.completion_tokens, used)}`}</StatTile>
          <StatTile label="Searches">{num(research.counts.searches)}</StatTile>
          <StatTile label="Sources">{num(research.counts.sources)}</StatTile>
          <StatTile label="Corrective rounds">{`${completion.corrective_rounds} of ${completion.max_corrective_rounds}`}</StatTile>
        </dl>}
        {failure && (
          <Notice tone="bad" title={failure.title}>
            {failure.detail}
            {research.counts.sources > 0 && <> Collected evidence is preserved: {research.counts.sources} source(s), {research.counts.passages} passages (see the Evidence tab).</>}
          </Notice>
        )}
        {demo?.runs.find((r) => r.run_id === run.id) && <DemoNote title={demo.runs.find((r) => r.run_id === run.id)?.title ?? 'Recorded run'} note={demo.runs.find((r) => r.run_id === run.id)?.note ?? ''} />}
        {!demo && failure && failure.title !== 'Collection failed' && <RetryPanel key={`${run.id}-${run.updated_at}`} runId={run.id} onStarted={() => { setRestarts((r) => r + 1); onTerminal?.() }} />}
        {tab !== 'overview' && !active && stoppedEarly && (
          <Notice tone="gap" title="Stopped early: results are partial">Reason: {stop ?? 'none recorded'}. Whatever was verified before the stop is kept; requirements that were not addressed are listed as gaps.</Notice>
        )}
        {tab !== 'overview' && !active && !stoppedEarly && !failure && stop && <Notice tone="neutral" title="Finished because">{stop}</Notice>}
        </div>
      </header>

      {(active || error) && <Progress live={data} error={error} />}

      <nav aria-label="Run sections" className="flex gap-1 overflow-x-auto border-b">
        {TABS.map((t) => (
          <Fragment key={t}>
            {t === 'history' && <span aria-hidden className="mx-1 ml-auto hidden self-center text-xs uppercase tracking-wide text-muted-foreground sm:inline">Technical</span>}
          <a
            href={hrefFor(run.id, t)}
            aria-current={t === tab ? 'page' : undefined}
            className={cn('-mb-px whitespace-nowrap rounded-t-md border-b-2 px-4 py-2.5 text-sm font-medium transition-colors', t === tab ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:bg-muted/60 hover:text-foreground', (t === 'history' || t === 'usage') && 'font-normal')}
          >
            {TAB_LABEL[t]}
          </a>
          </Fragment>
        ))}
      </nav>
      <section aria-labelledby="section-title" id="section-panel">
        <h2 id="section-title" className="sr-only">{TAB_LABEL[tab]}</h2>
        {tab === 'overview' && <OverviewTab run={run} live={data} version={version} claimId={claimId} />}
        {tab === 'report' && <ReportTab run={run} claimId={claimId} version={version} active={active} events={events} />}
        {tab === 'evidence' && <EvidenceTab run={run} claimId={claimId} version={version} />}
        {tab === 'coverage' && <CoverageTab run={run} version={version} />}
        {tab === 'history' && <HistoryTab run={run} events={events} version={version} completion={completion} />}
        {tab === 'usage' && <UsageTab run={run} live={data} version={version} />}
      </section>
    </div>
  )
}
