import type { Coverage, CoverageRequirement, RunSummary } from '@/api/types'
import { useResource } from '@/hooks/useApi'
import { REQUIREMENT_STATUS } from '@/lib/claimState'
import { hrefFor } from '@/lib/route'
import { CountChips, Empty, ErrorBox, Loading, Notice, Panel, Pill } from './bits'

function Status({ status }: { status: string }) {
  const s = REQUIREMENT_STATUS[status] ?? { label: status, tone: 'neutral' as const }
  return <Pill tone={s.tone} icon={s.tone === 'ok' ? 'check' : 'alert'}>{s.label}</Pill>
}

function Claims({ runId, ids }: { runId: number; ids: number[] }) {
  if (!ids.length) return <span className="text-muted-foreground">No supporting claim</span>
  return (
    <span>
      Supported by{' '}
      {ids.map((id, i) => (
        <span key={id}>{i > 0 && ', '}<a className="text-primary underline underline-offset-2" href={hrefFor(runId, 'evidence', id)}>claim {id}</a></span>
      ))}
    </span>
  )
}

function Requirement({ runId, q }: { runId: number; q: CoverageRequirement }) {
  const open = q.status !== 'addressed'
  return (
    <Panel accent={q.status === 'addressed' ? 'ok' : 'gap'}>
      <div className="space-y-3 p-4">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-xs text-muted-foreground">{q.requirement_key}</span>
          <Status status={q.status} />
        </div>
        <p className="text-base font-medium leading-snug break-words">{q.requirement_text}</p>
        <p className="text-sm"><Claims runId={runId} ids={q.supporting_claim_ids} /></p>
        {q.gap && open && <p className="rounded-md border border-gap/30 bg-gap-bg px-3 py-2 text-sm text-gap"><strong>Remaining gap:</strong> {q.gap}</p>}
        {q.obligations.length > 0 && (
          <div className="border-t pt-3">
            <h3 className="pb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Obligations (subject and dimension)</h3>
            <ul className="divide-y">
              {q.obligations.map((o) => (
                <li key={o.obligation_key} className="grid gap-x-3 gap-y-1 py-2 text-sm sm:grid-cols-[auto_minmax(0,1fr)]">
                  <div className="flex flex-wrap items-center gap-2 sm:w-56">
                    <span className="font-mono text-xs text-muted-foreground">{o.obligation_key}</span>
                    <Status status={o.status} />
                  </div>
                  <div className="min-w-0 space-y-0.5">
                    <p><strong>{o.subject}</strong> – {o.dimension}</p>
                    <p><Claims runId={runId} ids={o.supporting_claim_ids} /></p>
                    {o.gap && o.status !== 'addressed' && <p className="text-gap">Gap ({o.missing_kind}): {o.gap}</p>}
                  </div>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </Panel>
  )
}

export function CoverageTab({ run, version }: { run: RunSummary; version: number }) {
  const cov = useResource<Coverage>(`/runs/${run.id}/coverage`, version)
  if (cov.loading && !cov.data) return <Loading label="Loading coverage" blocks={3} />
  if (cov.error) return <ErrorBox error={cov.error} what="coverage" />
  const data = cov.data
  if (!data || data.requirements.length === 0) return <Empty title="No coverage assessment yet">Coverage is assessed after claims are verified, as part of the completion step.</Empty>
  const count = (s: string) => data.requirements.filter((r) => r.status === s).length
  const obligations = data.requirements.reduce((n, r) => n + r.obligations.length, 0)
  return (
    <div className="space-y-4">
      <Notice title="How to read this">
        Coverage is assessed by a model against the stored claims, not guaranteed truth. A requirement is split into obligations (a subject and a dimension) and is “addressed” only when every obligation is. Showing assessment round {data.round}.
      </Notice>
      <div className="space-y-1.5">
        <CountChips items={[
          { label: 'addressed', count: count('addressed'), tone: 'ok', icon: 'check' },
          { label: 'partially addressed', count: count('partially_addressed'), tone: 'gap', icon: 'alert' },
          { label: 'unresolved', count: count('unresolved'), tone: 'gap', icon: 'alert' },
        ]} />
        <p className="text-sm text-muted-foreground">{data.requirements.length} requirements, {obligations} obligations.</p>
      </div>
      <ul className="space-y-3">
        {data.requirements.map((q) => <li key={q.requirement_key}><Requirement runId={run.id} q={q} /></li>)}
      </ul>
    </div>
  )
}
