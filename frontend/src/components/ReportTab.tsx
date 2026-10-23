import { Download } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import type { Claim, Report, RunEvent, RunSummary } from '@/api/types'
import { Button } from '@/components/ui/button'
import { useResource } from '@/hooks/useApi'
import { claimState, REPORT_OUTCOME, REQUIREMENT_STATUS } from '@/lib/claimState'
import { collectionContext, emptyReportContext } from '@/lib/collection'
import { hrefFor } from '@/lib/route'
import { cn } from '@/lib/utils'
import { Empty, ErrorBox, Loading, Notice, Panel, Pill, SectionHeading, when } from './bits'
import { ClaimPill, Inspector } from './Inspector'

function download(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/markdown;charset=utf-8' }))
  const a = document.createElement('a')
  a.href = url
  a.download = name
  a.click()
  URL.revokeObjectURL(url)
}

export function ReportTab({ run, claimId, version, active, events = [] }: { run: RunSummary; claimId: number | null; version: number; active: boolean; events?: RunEvent[] }) {
  // The run summary already says whether a report exists (report_outcome). Asking for a report that is known not to exist would only produce a
  // 404 that the browser logs as a console error; a 404 that DOES arrive for a run claiming a report is still shown as a failure below.
  const report = useResource<Report>(run.report_outcome === null ? null : `/runs/${run.id}/report`, version)
  const claims = useResource<Claim[]>(`/runs/${run.id}/claims`, version)
  const [passage, setPassage] = useState<string | null>(null)
  const inspectorRef = useRef<HTMLDivElement>(null)
  useEffect(() => { if (claimId) inspectorRef.current?.scrollIntoView?.({ block: 'nearest' }) }, [claimId])

  if (report.loading && !report.data) return <Loading label="Loading report" blocks={3} />
  if (report.error && report.error.status !== 404) return <ErrorBox error={report.error} what="the report" />
  if (!report.data) {
    return (
      <Empty title={active ? 'The report is not written yet' : run.status === 'created' ? 'Research has not started' : 'This run has no report'}>
        {active ? 'It appears here when research and the correction loop finish.' : run.status === 'created' ? 'Research was never started for this run.' : run.complete_status === 'completed' ? 'The run completed without storing a report.' : 'The run stopped before a report was generated. Open the Run history tab to see why.'}
      </Empty>
    )
  }
  const r = report.data
  const outcome = REPORT_OUTCOME[r.outcome] ?? { label: r.outcome, tone: 'neutral' as const, text: '' }
  const current = new Map((claims.data ?? []).map((c) => [c.id, c]))
  const open = (id: number, p: string | null) => { setPassage(p); window.location.hash = hrefFor(run.id, 'report', id).slice(1) }
  const reported = r.sections.reduce((n, s) => n + s.claims.length, 0)

  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_28rem]">
      <article className="min-w-0 space-y-5" aria-label="Report">
        <Notice tone={outcome.tone === 'ok' ? 'ok' : 'gap'} title={`Report ${r.id}: ${outcome.label}`}>
          {outcome.text} Coverage is a model assessment, not a guarantee of truth.
        </Notice>
        {r.outcome === 'insufficient_evidence' && events.length > 0 && (
          <Notice tone="gap" title="What the run collected">{emptyReportContext(collectionContext(events))}</Notice>
        )}
        <Panel>
          <div className="flex flex-wrap items-center justify-between gap-3 p-3 text-sm">
            <p className="min-w-0 flex-1 text-muted-foreground">
              <strong className="font-medium text-foreground">Historical snapshot</strong> created {when(r.created_at)} after {r.corrective_rounds} corrective round(s), {reported} claim(s).
              Claim text and citations are exactly as stored; the state beside each claim is its <em>current</em> state and can differ from when the report was written.
            </p>
            <Button size="sm" variant="outline" onClick={() => download(`deepdive-run-${run.id}-report-${r.id}.md`, r.markdown)}><Download aria-hidden /> Download report (.md)</Button>
          </div>
        </Panel>

        {r.sections.length === 0 && <Empty title="No claim reached the report">Nothing was both verified and eligible, so no claims are listed.</Empty>}
        {r.sections.map((sec) => (
          <section key={sec.position} className="space-y-3" aria-labelledby={`sec-${sec.position}`}>
            <div className="space-y-0.5">
              <p className="text-xs font-semibold uppercase tracking-wider text-primary">{sec.label}</p>
              <h2 id={`sec-${sec.position}`} className="text-lg font-semibold leading-snug tracking-tight">{sec.subquestion_text}</h2>
            </div>
            {sec.answered !== false && (sec.open_requirements?.length ?? 0) > 0 && (
              <Notice tone="gap" title="Not directly answered in this section">
                <ul className="list-disc pl-5">
                  {sec.open_requirements!.map((q) => <li key={q.requirement_key}>{q.requirement_text} ({q.status.replace('_', ' ')}{q.gap ? `: ${q.gap}` : ''})</li>)}
                </ul>
              </Notice>
            )}
            {sec.answered === false && (
              <Notice tone="gap" title="No direct answer was found for this subquestion">
                {sec.context?.length ? 'The verified claims below relate to it but do not answer it.' : 'No verified claim answers it.'}
              </Notice>
            )}
            <ul className="space-y-2.5">
              {sec.claims.map((c) => {
                const now = current.get(c.claim_id)
                const changed = now && (now.version !== c.claim_version || now.superseded_by || now.excluded || !now.eligible_for_report)
                const selected = claimId === c.claim_id
                return (
                  <li key={c.claim_id}>
                    <Panel accent={now ? claimState(now).tone : undefined} className={cn(selected && 'ring-2 ring-primary/40')}>
                      <div className="space-y-2.5 p-3.5">
                        <button type="button" id={`claim-btn-${c.claim_id}`} onClick={() => open(c.claim_id, null)} aria-current={selected ? 'true' : undefined}
                          className="block w-full rounded-md text-left leading-relaxed hover:bg-muted/50">
                          <span className="whitespace-pre-wrap break-words">{c.text}</span>
                        </button>
                        <div className="flex flex-wrap items-center gap-2 text-xs">
                          <span className="text-muted-foreground">Claim {c.claim_id} v{c.claim_version}</span>
                          {now ? <ClaimPill claim={now} /> : claims.loading ? <span className="text-muted-foreground">checking current state…</span> : null}
                          {changed && <Pill tone="gap" icon="alert">Changed since this snapshot: {claimState(now).label}</Pill>}
                        </div>
                        <ul className="flex flex-wrap gap-1.5" aria-label={`Citations for claim ${c.claim_id}`}>
                          {c.citations.map((ct, i) => (
                            <li key={ct.passage_id}>
                              <button
                                type="button"
                                onClick={() => open(c.claim_id, ct.passage_id)}
                                className="rounded-md border bg-muted/70 px-2 py-1 text-xs transition-colors hover:border-primary/50 hover:bg-accent"
                              >
                                [{i + 1}] {ct.title ?? ct.url} · {ct.passage_id}{ct.role === 'neighbour_context' ? ' (context)' : ''}
                              </button>
                            </li>
                          ))}
                        </ul>
                      </div>
                    </Panel>
                  </li>
                )
              })}
            </ul>
            {(sec.context?.length ?? 0) > 0 && (
              <div className="space-y-2" role="group" aria-label={`Related context for ${sec.label}`}>
                <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Related context (verified, but it does not answer this subquestion)</p>
                <ul className="space-y-2.5">
                  {sec.context!.map((c) => (
                    <li key={c.claim_id}>
                      <Panel>
                        <div className="space-y-2 p-3.5">
                          <p className="whitespace-pre-wrap break-words leading-relaxed text-muted-foreground">{c.text}</p>
                          <div className="flex flex-wrap items-center gap-2 text-xs">
                            <span className="text-muted-foreground">Claim {c.claim_id} v{c.claim_version}</span>
                            <Pill tone="neutral" icon="help">Context, not an answer</Pill>
                            {c.role_reason && <span className="text-muted-foreground">{c.role_reason}</span>}
                          </div>
                          <ul className="flex flex-wrap gap-1.5" aria-label={`Citations for claim ${c.claim_id}`}>
                            {c.citations.map((ct, i) => (
                              <li key={ct.passage_id}>
                                <button type="button" onClick={() => open(c.claim_id, ct.passage_id)}
                                  className="rounded-md border bg-muted/70 px-2 py-1 text-xs transition-colors hover:border-primary/50 hover:bg-accent">
                                  [{i + 1}] {ct.title ?? ct.url} · {ct.passage_id}
                                </button>
                              </li>
                            ))}
                          </ul>
                        </div>
                      </Panel>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </section>
        ))}

        <section aria-labelledby="req-h" className="space-y-3">
          <SectionHeading id="req-h" description="Every requirement of the final coverage assessment, addressed or not.">Requirements</SectionHeading>
          {r.unresolved_requirements.length === 0 && r.requirements.length > 0 && <p className="text-sm text-muted-foreground">No requirement was left unresolved in the final assessment.</p>}
          <ul className="space-y-2">
            {r.requirements.map((q, i) => {
              const st = REQUIREMENT_STATUS[String(q.status)] ?? { label: String(q.status ?? 'Unknown'), tone: 'neutral' as const }
              return (
                <li key={String(q.requirement_key ?? i)}>
                  <Panel accent={st.tone === 'ok' ? 'ok' : 'gap'}>
                    <div className="space-y-1 p-3 text-sm">
                      <div className="flex flex-wrap items-center gap-2">
                        <Pill tone={st.tone} icon={st.tone === 'ok' ? 'check' : 'alert'}>{st.label}</Pill>
                        <span className="break-words">{String(q.requirement_text ?? q.requirement_key ?? '')}</span>
                      </div>
                      {q.gap ? <p className="text-gap">Gap: {String(q.gap)}</p> : null}
                    </div>
                  </Panel>
                </li>
              )
            })}
          </ul>
        </section>
        {r.omissions.length > 0 && (
          <section aria-labelledby="om-h" className="space-y-2">
            <SectionHeading id="om-h">Eligible claims left out</SectionHeading>
            <ul className="list-disc pl-5 text-sm">{r.omissions.map((o, i) => <li key={i}>Claim {String(o.claim_id ?? '?')}: {String(o.reason ?? 'no reason recorded')}</li>)}</ul>
          </section>
        )}
        {reported > 0 && <p className="text-xs text-muted-foreground">Sources open in a new tab. Retrieved text is shown as plain text only.</p>}
      </article>

      <div ref={inspectorRef} className="min-w-0 lg:sticky lg:top-4 lg:self-start">
        {claimId ? (
          <Inspector runId={run.id} claimId={claimId} highlight={passage} onClose={() => { setPassage(null); window.location.hash = hrefFor(run.id, 'report').slice(1); setTimeout(() => document.getElementById(`claim-btn-${claimId}`)?.focus(), 0) }} />
        ) : (
          <Empty title="Select a claim or a citation">Its exact stored evidence, integrity check and verdict open here.</Empty>
        )}
      </div>
    </div>
  )
}
