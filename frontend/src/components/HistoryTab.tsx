import { ArrowRight } from 'lucide-react'
import { useState } from 'react'
import type { Completion, CorrectionRound, Corrections, RunEvent, RunSummary, SearchRow } from '@/api/types'
import { useResource } from '@/hooks/useApi'
import type { StateIcon, Tone } from '@/lib/claimState'
import { eventDetail, eventLabel, STAGE_LABEL } from '@/lib/events'
import { hrefFor } from '@/lib/route'
import { cn } from '@/lib/utils'
import { CountChips, Empty, ErrorBox, Loading, Panel, Pill, SafeLink, SectionHeading, when } from './bits'
import { ClaimPill } from './Inspector'
import { LifecycleDetail } from './Lifecycle'

const GROUPS: { key: string; label: string; test: (e: RunEvent) => boolean }[] = [
  { key: 'all', label: 'All events', test: () => true },
  { key: 'search', label: 'Searches and fetches', test: (e) => ['researcher', 'ingest'].includes(e.stage) },
  { key: 'verify', label: 'Drafting and verification', test: (e) => ['drafter', 'verifier', 'rejudge', 'relevance'].includes(e.stage) },
  { key: 'correct', label: 'Coverage and corrections', test: (e) => ['coverage', 'correct', 'reviser', 'report'].includes(e.stage) },
  { key: 'stop', label: 'Stops and failures', test: (e) => /finished|failed|skipped|rejected|discarded/.test(e.event_type) },
]

/** The coverage summary of one assessment as count chips (real counts from the stored summary), or Unavailable. */
function CoverageSide({ title, summary }: { title: string; summary: Record<string, unknown> | null }) {
  const n = (k: string) => (typeof summary?.[k] === 'number' ? (summary[k] as number) : null)
  return (
    <div className="space-y-1.5 rounded-lg border bg-muted/30 p-3">
      <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{title}</h4>
      {summary && n('addressed') !== null ? (
        <>
          <CountChips items={[
            { label: 'addressed', count: n('addressed') ?? 0, tone: 'ok', icon: 'check' },
            { label: 'partial', count: n('partially_addressed') ?? 0, tone: 'gap', icon: 'alert' },
            { label: 'unresolved', count: n('unresolved') ?? 0, tone: 'gap', icon: 'alert' },
          ]} />
          <p className="text-xs text-muted-foreground">of {n('requirements') ?? 'Unavailable'} requirements</p>
        </>
      ) : <p className="text-sm text-muted-foreground">Unavailable (no assessment follows a round that did not run).</p>}
    </div>
  )
}

function Round({ runId, r }: { runId: number; r: CorrectionRound }) {
  const by = (s: string) => r.tasks.filter((t) => t.status === s).length
  const chips: { label: string; count: number; tone: Tone; icon: StateIcon }[] = [
    { label: 'tasks done', count: by('done'), tone: 'ok', icon: 'check' },
    { label: 'skipped', count: by('skipped'), tone: 'neutral', icon: 'clock' },
    { label: 'failed', count: by('failed'), tone: 'bad', icon: 'x' },
    { label: 'planned', count: by('planned'), tone: 'neutral', icon: 'clock' },
  ]
  return (
    <Panel accent={r.executed ? 'neutral' : 'gap'}>
      <div className="space-y-3 p-4">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-base font-semibold">Corrective round {r.corrective_round}</h3>
          {r.executed ? <Pill tone="ok" icon="check">executed</Pill> : <Pill tone="gap" icon="alert">planned, not executed</Pill>}
        </div>
        <div className="grid items-stretch gap-2 sm:grid-cols-[1fr_auto_1fr]">
          <CoverageSide title="Coverage before" summary={r.coverage_before} />
          <ArrowRight aria-hidden className="hidden self-center text-muted-foreground sm:block" />
          <CoverageSide title="Coverage after" summary={r.coverage_after} />
        </div>
        <CountChips items={chips.filter((i) => i.count > 0)} />
        <details className="text-sm">
          <summary className="cursor-pointer font-medium">All {r.tasks.length} task(s) of this round</summary>
          <ul className="mt-2 space-y-1.5">
            {r.tasks.map((t) => (
              <li key={t.id} className="rounded-md border bg-card p-2">
                <Pill tone={t.status === 'done' ? 'ok' : t.status === 'failed' ? 'bad' : 'neutral'} icon={t.status === 'done' ? 'check' : t.status === 'failed' ? 'x' : 'clock'}>{t.status}</Pill>{' '}
                <strong>{t.kind.replace(/_/g, ' ')}</strong>
                {t.requirement_key ? ` · requirement ${t.requirement_key}` : ''}
                {t.claim_id ? <> · <a className="text-primary underline" href={hrefFor(runId, 'evidence', t.claim_id)}>claim {t.claim_id}</a></> : null}
                <details className="mt-1"><summary className="cursor-pointer text-muted-foreground">Task details</summary><pre className="overflow-x-auto whitespace-pre-wrap break-words text-xs">{JSON.stringify({ spec: t.spec, outcome: t.outcome }, null, 2)}</pre></details>
              </li>
            ))}
          </ul>
        </details>
      </div>
    </Panel>
  )
}

export function HistoryTab({ run, events, version, completion }: { run: RunSummary; events: RunEvent[]; version: number; completion?: Completion | null }) {
  const searches = useResource<SearchRow[]>(`/runs/${run.id}/searches`, version)
  const corr = useResource<Corrections>(`/runs/${run.id}/corrections`, version)
  const [group, setGroup] = useState('all')
  const test = GROUPS.find((g) => g.key === group)!.test
  const failedFetches = events.filter((e) => e.event_type === 'fetch_failed')
  const shown = events.filter(test)
  return (
    <div className="space-y-8">
      {completion !== undefined && <LifecycleDetail run={run} events={events} completion={completion} />}
      <section aria-labelledby="corr-h" className="space-y-3">
        <SectionHeading id="corr-h" description="What each corrective round planned and did, and the coverage assessed before and after it.">Corrections</SectionHeading>
        {corr.loading && !corr.data && <Loading label="Loading corrections" blocks={2} />}
        {corr.error && <ErrorBox error={corr.error} what="corrections" />}
        {corr.data && corr.data.rounds.length === 0 && corr.data.revisions.length === 0 && <Empty title="No correction was made">The first coverage assessment did not lead to any task, or the run never reached it.</Empty>}
        {corr.data?.rounds.map((r) => <Round key={r.planned_round} runId={run.id} r={r} />)}
        {corr.data && corr.data.revisions.length > 0 && (
          <div className="space-y-3">
            <SectionHeading level={3} description="A repaired or split claim is a new version; the original stays in the history unchanged.">Claim versions, before and after</SectionHeading>
            {corr.data.revisions.map((chain) => (
              <ol key={chain[0].claim_id} className={cn('grid gap-2', chain.length === 2 ? 'lg:grid-cols-2' : '')}>
                {chain.map((v, i) => (
                  <li key={v.claim_id}>
                    <Panel accent={v.excluded ? 'bad' : i === 0 ? 'neutral' : 'ok'} className="h-full">
                      <div className="space-y-1.5 p-3.5">
                        <p className="flex flex-wrap items-center gap-2 text-sm">
                          <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{chain.length === 2 ? (i === 0 ? 'Before' : 'After') : `Version ${v.version}`}</span>
                          <a className="text-primary underline" href={hrefFor(run.id, 'evidence', v.claim_id)}>Claim {v.claim_id}</a> v{v.version}
                          {v.parent_claim_id ? <span className="text-muted-foreground">revised from claim {v.parent_claim_id}</span> : null}
                          <ClaimPill claim={{ origin: 'drafted', excluded: v.excluded ? { reason: v.excluded.reason, planned_round: 0 } : null, integrity_verdict: v.integrity_verdict, semantic_verdict: v.semantic_verdict, needs_rejudge: false, superseded_by: v.superseded_by }} />
                        </p>
                        <p className="whitespace-pre-wrap break-words leading-relaxed">{v.text}</p>
                        {v.excluded && <p className="text-sm text-bad">Excluded: {v.excluded.reason}</p>}
                        {typeof v.provenance.reason === 'string' && <p className="text-sm text-muted-foreground">Why: {v.provenance.reason}</p>}
                      </div>
                    </Panel>
                  </li>
                ))}
              </ol>
            ))}
          </div>
        )}
        {corr.data && corr.data.exclusions.length > 0 && (
          <ul className="list-disc pl-5 text-sm">{corr.data.exclusions.map((x) => <li key={x.claim_id}>Claim {x.claim_id} excluded (round {x.planned_round}): {x.reason}</li>)}</ul>
        )}
      </section>

      <section aria-labelledby="search-h" className="space-y-3">
        <SectionHeading id="search-h" description="Every web search the run made and what happened to the pages it selected.">Searches</SectionHeading>
        {searches.loading && !searches.data && <Loading label="Loading searches" blocks={2} />}
        {searches.error && <ErrorBox error={searches.error} what="searches" />}
        {searches.data?.length === 0 && <Empty title="No search was run" />}
        <ul className="space-y-2.5">
          {searches.data?.map((s) => (
            <li key={s.id}>
              <Panel accent={s.error ? 'bad' : undefined}>
                <div className="space-y-1.5 p-3.5 text-sm">
                  <p className="font-medium break-words">{s.query}</p>
                  <p className="text-xs text-muted-foreground">{s.provider} · {when(s.created_at)}</p>
                  {s.error && <p className="text-bad">Search failed: {s.error}</p>}
                  <ul className="space-y-1">
                    {s.selections.map((x, i) => (
                      <li key={i} className="break-all"><Pill tone={x.outcome === 'ingested' || x.outcome === 'source_ingested' ? 'ok' : 'neutral'}>{x.outcome}</Pill> <SafeLink url={x.url} />{x.reason ? <span className="text-muted-foreground"> – {x.reason}</span> : null}</li>
                    ))}
                  </ul>
                </div>
              </Panel>
            </li>
          ))}
        </ul>
        {failedFetches.length > 0 && (
          <div className="space-y-1.5">
            <SectionHeading level={3}>Fetch failures</SectionHeading>
            <ul className="space-y-1 text-sm">{failedFetches.map((e) => <li key={e.id}><Pill tone="gap" icon="alert">fetch failed</Pill> <span className="break-all">{eventDetail(e)}</span></li>)}</ul>
          </div>
        )}
      </section>

      <section aria-labelledby="ev-h" className="space-y-3">
        <SectionHeading id="ev-h" description="Every recorded event, oldest first. Payloads are shown as stored.">Event timeline</SectionHeading>
        <div role="group" aria-label="Filter events" className="flex flex-wrap gap-1.5">
          {GROUPS.map((g) => (
            <button key={g.key} type="button" aria-pressed={group === g.key} onClick={() => setGroup(g.key)}
              className={cn('rounded-full border px-3 py-1 text-sm transition-colors', group === g.key ? 'border-primary bg-accent font-medium text-accent-foreground' : 'bg-card hover:bg-muted')}>{g.label}</button>
          ))}
        </div>
        {shown.length === 0 ? <p className="text-sm text-muted-foreground">No events in this group.</p> : (
          <div role="region" aria-label="Event timeline entries" tabIndex={0} className="max-h-[34rem] overflow-y-auto rounded-lg border bg-card">
            <ol className="divide-y">
              {shown.map((e) => (
                <li key={e.id} className="px-3.5 py-2 text-sm">
                  <span className="font-medium">{eventLabel(e)}</span>
                  <span className="text-muted-foreground"> · {STAGE_LABEL[e.stage] ?? e.stage} · {when(e.created_at)}</span>
                  {eventDetail(e) && <span className="block break-words">{eventDetail(e)}</span>}
                  <details><summary className="cursor-pointer text-xs text-muted-foreground">Stored payload</summary><pre className="overflow-x-auto whitespace-pre-wrap break-words text-xs">{JSON.stringify(e.payload, null, 2)}</pre></details>
                </li>
              ))}
            </ol>
          </div>
        )}
      </section>
    </div>
  )
}
